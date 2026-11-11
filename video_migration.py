"""HEVC/AAC-in-MP4 storage optimization with verified playback readiness."""

import concurrent.futures, hashlib, json, time, threading
from pathlib import Path
from photos_api import API, ROOT, at, original_files, upload
from converter import backup_file
from migration import save, attrs, is_video, album_ids
from video_codec import encode, probe, validate_encoded
from selection import Selection

WORK = ROOT / "work-video"


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class Videos:
    def __init__(self, selection=None):
        self.selection = selection or Selection()
        self.errors = []
        self.api = API()
        self.account = self.api.call({"op": "hello"})["account"]
        self.inv = json.loads((ROOT / "inventory-api.json").read_text())
        self.cat = json.loads((ROOT / "catalog-api.json").read_text())
        if self.account != self.inv["account"] or self.account != self.cat["account"]:
            raise RuntimeError("Video account mismatch")
        WORK.mkdir(exist_ok=True)
        self.waiting = []
        self.lock = threading.Lock()

    def write(self, s, **kw):
        s.update(kw)
        save(WORK / s["original_key"] / "state.json", s)

    def log(self, event, **kw):
        row = {"time": time.time(), "event": event, **kw}
        with (ROOT / "video-migration.jsonl").open("a") as f:
            f.write(json.dumps(row) + "\n")
        print(json.dumps(row), flush=True)

    def process(self, row):
        key = row[0]
        f = WORK / key
        f.mkdir(exist_ok=True)
        statefile = f / "state.json"
        s = (
            json.loads(statefile.read_text())
            if statefile.exists()
            else {"original_key": key, "account": self.account}
        )
        if s.get("account") != self.account:
            raise RuntimeError("Video journal account mismatch")
        if s.get("original_key") != key:
            raise RuntimeError(
                "Video journal source identity does not match selected item"
            )
        if s.get("stage") in ("trashed", "already_efficient"):
            return
        if s.get("replacement_key"):
            with self.lock:
                self.waiting.append(s)
            return
        if not s.get("deletion_mode"):
            self.write(s, deletion_mode=self.selection.delete_mode)
        e = self.cat["items"][key]
        er = e[0]
        source = f / ("original" + Path(er[2]).suffix.lower())
        target = f / (Path(er[2]).stem + "-hevc.mp4")
        if s.get("stage") == "committing" and (
            not source.exists() or not target.exists()
        ):
            raise RuntimeError(
                "Ambiguous video commit requires its retained backup and output; restore missing files before resuming"
            )
        if not s.get("original_ext"):
            self.write(
                s,
                original_ext=e,
                original_info=self.api.info(key),
                inventory_row=row,
                source=str(source),
                output=str(target),
                original_bytes=er[5],
                filename=er[2],
            )
        if not source.exists():
            with original_files(self.api, [key]) as files:
                data = [(n, p) for n, p in files if Path(n).suffix.lower() != ".json"]
                if len(data) != 1:
                    raise RuntimeError("Unexpected video export")
                backup_file(data[0][1], source)
            self.write(
                s,
                stage="downloaded",
                original_sha256=sha(source),
                original_backup_bytes=source.stat().st_size,
            )
        if not s.get("original_sha256"):
            # An atomic file can survive a crash just before its journal update.
            if source.stat().st_size != er[5]:
                raise RuntimeError(
                    "Unjournaled video backup size mismatch; retain source"
                )
            self.write(
                s,
                original_sha256=sha(source),
                original_backup_bytes=source.stat().st_size,
            )
        if sha(source) != s["original_sha256"]:
            raise RuntimeError("Video original backup changed; upload refused")
        if not target.exists():
            self.write(s, conversion=encode(source, target, er[3], codec="hevc"))
        elif not s.get("conversion"):
            self.write(s, conversion=validate_encoded(source, target))
        if target.stat().st_size >= er[5]:
            self.write(
                s,
                stage="already_efficient",
                reason="Optimized video would not save storage",
            )
            self.log("already_efficient", key=key)
            return

        def intent(**kw):
            self.write(s, **kw)

        encoded_sha = sha(target)
        recorded_sha = s.get("encoded_sha256") or s.get("conversion", {}).get("sha256")
        if recorded_sha and recorded_sha != encoded_sha:
            raise RuntimeError("Encoded video changed since journal; upload refused")
        self.write(s, encoded_sha256=encoded_sha, encoded_bytes=target.stat().st_size)
        new = upload(
            self.api,
            target,
            er[3],
            intent,
            reconcile_only=s.get("stage") == "committing",
        )
        if new == key:
            raise RuntimeError("Video replacement equals original")
        self.write(
            s,
            stage="uploaded",
            replacement_key=new,
            encoded_bytes=target.stat().st_size,
            encoded_sha256=encoded_sha,
            uploaded_at=time.time(),
        )
        with self.lock:
            self.waiting.append(s)
        self.log(
            "uploaded",
            key=key,
            replacement=new,
            original_bytes=er[5],
            encoded_bytes=target.stat().st_size,
        )

    def finalize(self, s):
        reconcile = s.get("stage") == "trashing"
        if (
            reconcile
            and s.get("encoded_sha256")
            and s.get("download_verified_sha256") == s["encoded_sha256"]
            and s.get("playback_ready")
            and s.get("capture_date_verified")
            and s.get("albums_preserved")
            and s["original_key"]
            in self.api.in_trash([s["original_key"]], exhaustive=True)
        ):
            if sha(s["source"]) != s["original_sha256"]:
                raise RuntimeError("Video original backup changed")
            self.write(s, stage="trashed", trashed_at=time.time())
            self.log("removal_reconciled", key=s["original_key"])
            return True
        key = s["replacement_key"]
        er = s["original_ext"][0]
        info, ext = self.api.batch(
            [
                ["VrseUb", [key, None, None, None, None]],
                ["fDcn4b", [key, 1, None, None, 1]],
            ]
        )
        if not info or not ext:
            return False
        if at(info, 0, 0) != key or at(ext, 0, 0) != key:
            raise RuntimeError("Video replacement metadata identity mismatch")
        quality = at(ext, 0, 30, 2)
        if quality is None:
            return False
        if quality != 2:
            raise RuntimeError(
                "Video replacement uses Google Storage saver, which can rewrite HEVC after verification. Set web backup quality to Original quality before uploading. Source retained; see README recovery notes."
            )
        e = ext[0]
        v = attrs(info[0]).get("76647426")
        if not v or not at(v, 0) or not at(v, 7):
            return False
        expected = s.get("conversion", {}).get("duration")
        if expected is None:
            expected = float(probe(s["source"])["format"]["duration"])
        if abs(v[0] / 1000 - expected) > 0.3:
            raise RuntimeError("Cloud video duration mismatch")
        if e[5] != s["encoded_bytes"]:
            return False
        if e[3:5] != er[3:5]:
            self.api.rpc("DaSgWe", [[[info[0][3], er[3] / 1000, (er[4] or 0) / 1000]]])
            if self.api.extended(key)[0][3:5] != er[3:5]:
                raise RuntimeError("Video capture date/zone mismatch")
        if s.get("download_verified_sha256") != s["encoded_sha256"]:
            with original_files(self.api, [key]) as files:
                if s["encoded_sha256"] not in {sha(path) for _, path in files}:
                    return False
            self.write(s, download_verified_sha256=s["encoded_sha256"])
        missing = album_ids(s["original_ext"]) - album_ids(ext)
        for album in er[19] or []:
            if album[0] in missing:
                self.api.add_album([key], album[0], bool(at(album, -1, "72930366", 4)))
        if missing and not album_ids(s["original_ext"]) <= album_ids(
            self.api.extended(key)
        ):
            return False
        if er[1] and e[1] != er[1]:
            self.api.rpc("AQNOFd", [None, er[1], info[0][3]])
            if self.api.extended(key)[0][1] != er[1]:
                raise RuntimeError("Video description mismatch")
        if at(s["inventory_row"], -1, "163238866", 0):
            self.api.rpc("Ftfh0", [[[None, info[0][3]]], [1]])
        if at(s["inventory_row"], 13):
            self.api.rpc("w7TP3c", [[[None, [1], [None, info[0][3]]]], None, 1])
        if at(s["inventory_row"], -1, "163238866", 0) or at(s["inventory_row"], 13):
            check = self.api.info(key)
            if at(s["inventory_row"], -1, "163238866", 0) and not at(
                attrs(check[0]), "163238866", 0
            ):
                raise RuntimeError("Video favorite mismatch")
            if at(s["inventory_row"], 13) and not at(check, 0, 13):
                raise RuntimeError("Video archive mismatch")
        if sha(s["source"]) != s["original_sha256"]:
            raise RuntimeError("Video original backup changed")
        self.write(
            s,
            stage="trashing",
            playback_ready=True,
            capture_date_verified=True,
            albums_preserved=True,
        )
        # Reconcile a previous interrupted trash rather than relying on a successful HTTP status.
        present = self.api.in_trash([s["original_key"]], exhaustive=reconcile)
        if s["original_key"] not in present:
            self.api.trash([s["original_info"][0][3]])
            if s["original_key"] not in self.api.in_trash(
                [s["original_key"]], exhaustive=True
            ):
                raise RuntimeError("Video Trash readback failed")
        self.write(s, stage="trashed", trashed_at=time.time())
        self.log(
            "replaced",
            key=s["original_key"],
            saved_bytes=s["original_bytes"] - s["encoded_bytes"],
        )
        return True

    def drain(self):
        with self.lock:
            pending = self.waiting
            self.waiting = []
        for s in pending:
            try:
                if not self.finalize(s):
                    with self.lock:
                        self.waiting.append(s)
            except Exception as e:
                self.errors.append(s["original_key"])
                self.write(s, error=str(e))
                self.log("finalize_error", key=s["original_key"], error=str(e))

    def run(self, limit=None):
        rows = [r for r in self.selection.select(self.inv, self.cat) if is_video(r)]
        if limit:
            rows = rows[:limit]

        def task(row):
            try:
                self.process(row)
            except Exception as e:
                path = WORK / row[0] / "state.json"
                s = (
                    json.loads(path.read_text())
                    if path.exists()
                    else {"original_key": row[0], "account": self.account}
                )
                self.errors.append(row[0])
                self.write(s, error=str(e))
                self.log("item_error", key=row[0], error=str(e))

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            for n, _ in enumerate(pool.map(task, rows)):
                if n % 3 == 0:
                    self.drain()
        end = time.monotonic() + 3600
        while self.waiting and time.monotonic() < end:
            self.drain()
            if self.waiting:
                self.log("awaiting_processing", count=len(self.waiting))
                time.sleep(20)
        self.log("pass_finished", waiting=len(self.waiting))
        return self.errors, self.waiting
