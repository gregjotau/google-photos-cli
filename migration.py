"""Account-bound, resumable migration. All cloud mutations use direct web RPCs."""

import concurrent.futures, hashlib, json, time, threading
from pathlib import Path
from photos_api import API, ROOT, at, upload, sha1_b64, originals, original_files
from converter import convert, digest, atomic_json, atomic_bytes
from selection import Selection

WORK = ROOT / "work-api"


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(path, data)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def album_ids(ext):
    return {a[0] for a in at(ext, 0, 19, default=[]) or []}


def attrs(row):
    return next((value for value in reversed(row) if isinstance(value, dict)), {})


def is_video(row):
    return "76647426" in attrs(row) and "146008172" not in attrs(row)


class Migration:
    def __init__(
        self,
        quality=45,
        speed=6,
        threads=3,
        workers=4,
        selection=None,
        batch_size=10,
        after_batch=None,
        stop_event=None,
    ):
        self.api = API()
        self.quality = quality
        self.speed = speed
        self.threads = threads
        self.workers = workers
        self.batch_size = batch_size
        self.after_batch = after_batch
        self.stop_event = stop_event or threading.Event()
        self.selection = selection or Selection()
        self.inventory = json.loads((ROOT / "inventory-api.json").read_text())
        self.catalog = json.loads((ROOT / "catalog-api.json").read_text())
        self.account = self.api.call({"op": "hello"})["account"]
        if (
            self.account != self.inventory["account"]
            or self.account != self.catalog["account"]
        ):
            raise RuntimeError("Account mismatch")
        WORK.mkdir(exist_ok=True)
        self.waiting = []
        self.errors = []
        self.logfile = ROOT / "migration.jsonl"
        self.wait_lock = threading.Lock()

    def log(self, event, **kw):
        row = {"time": time.time(), "event": event, **kw}
        with self.logfile.open("a") as f:
            f.write(json.dumps(row) + "\n")
        print(json.dumps(row), flush=True)

    def queue(self, state):
        with self.wait_lock:
            self.waiting.append(state)

    def state(self, key):
        f = WORK / key / "state.json"
        return (
            json.loads(f.read_text())
            if f.exists()
            else {"original_key": key, "account": self.account}
        )

    def write(self, state, **kw):
        path = WORK / state["original_key"] / "state.json"
        latest = json.loads(path.read_text()) if path.exists() else {}
        latest.update(state)
        latest.update(kw)
        state.clear()
        state.update(latest)
        save(path, state)

    def gps(self, ext):
        point = at(ext, 0, 9, 0) or at(ext, 0, 13, 0)
        if (
            isinstance(point, list)
            and len(point) == 2
            and all(isinstance(v, (int, float)) for v in point)
        ):
            return tuple(v / 10000000 for v in point)
        return None

    def prepare(self, row, pool):
        key = row[0]
        state = self.state(key)
        if state.get("account") != self.account:
            raise RuntimeError("Journal account mismatch")
        if state.get("original_key") != key:
            raise RuntimeError("Journal source identity does not match selected item")
        if state.get("stage") in ("trashed", "already_avif", "video"):
            return
        live = "146008172" in attrs(row)
        if live and self.selection.live_photos == "keep":
            if state.get("replacement_key"):
                raise RuntimeError(
                    "Pending Live Photo still replacement exists; finish it before changing policy to keep"
                )
            self.write(
                state,
                stage="live_photo_unchanged",
                replacement_key=None,
                error=None,
                reason="Keep combined Live Photos unchanged",
            )
            return
        if is_video(row):
            self.write(state, stage="video")
            return
        ext = self.catalog["items"].get(key)
        if not ext:
            self.write(state, stage="absent")
            return
        er = ext[0]
        filename = er[2]
        if filename.lower().endswith(".avif"):
            self.write(state, stage="already_avif", filename=filename, bytes=er[5])
            return
        if state.get("replacement_key"):
            self.queue(state)
            return
        if not state.get("deletion_mode"):
            self.write(state, deletion_mode=self.selection.delete_mode)
        folder = WORK / key
        folder.mkdir(exist_ok=True)
        source = folder / ("original" + Path(filename).suffix.lower())
        output = folder / (
            Path(filename).stem + ("-still-avif.avif" if live else "-avif.avif")
        )
        if state.get("stage") == "committing" and (
            not source.exists() or not output.exists()
        ):
            raise RuntimeError(
                "Ambiguous commit requires its original backup and encoded output; restore missing files before resuming"
            )
        if (
            source.exists()
            and state.get("original_sha256")
            and digest(source) != state["original_sha256"]
        ):
            raise RuntimeError("Original backup changed; refusing to resume upload")
        if not state.get("original_info"):
            info = self.api.info(key)
            if at(info, 0, 0) != key:
                raise RuntimeError("Original info identity mismatch")
            self.write(
                state,
                original_info=info,
                original_ext=ext,
                filename=filename,
                original_bytes=er[5],
                original_quota=at(er, 30, 1, default=0) or 0,
                quality=self.quality,
                speed=self.speed,
                source=str(source),
                output=str(output),
                inventory_row=row,
            )
        info = state["original_info"]
        if live:
            self.write(
                state,
                source=str(source),
                output=str(output),
                live_photo_policy="still",
                inventory_row=row,
                error=None,
                reason="Convert combined Live Photo to one AVIF still; retain all original components locally",
            )
            self.backup_live(state, source)
        if (
            source.exists()
            and state.get("original_backup_bytes", er[5])
            and source.stat().st_size != state.get("original_backup_bytes", er[5])
        ):
            source.unlink()
        if not source.exists():
            try:
                response = self.api.fetch(info[1])
                if response["status"] != 200 or not response["headers"].get(
                    "content-type", ""
                ).startswith(("image/", "application/octet-stream")):
                    raise RuntimeError("Download did not return image")
                if er[5] and len(response["body"]) != er[5]:
                    raise RuntimeError("Original size mismatch; possible processing")
                atomic_bytes(source, response["body"])
            except Exception:
                files = originals(self.api, [key])
                stills = [
                    (n, b)
                    for n, b in files
                    if Path(n).suffix.lower() not in (".mov", ".mp4", ".json")
                ]
                if len(stills) != 1:
                    raise RuntimeError("Unexpected original export contents")
                atomic_bytes(source, stills[0][1])
                clips = [
                    (n, b)
                    for n, b in files
                    if Path(n).suffix.lower() in (".mov", ".mp4")
                ]
                if clips:
                    motion_path = folder / (
                        "original-motion" + Path(clips[0][0]).suffix.lower()
                    )
                    atomic_bytes(motion_path, clips[0][1])
                    self.write(
                        state,
                        motion_source=str(motion_path),
                        motion_original_bytes=len(clips[0][1]),
                    )
                if er[5] and source.stat().st_size != er[5]:
                    self.write(
                        state,
                        source_representation="Google original export differs in size from recorded upload",
                        original_export_verified=True,
                    )
                self.write(
                    state,
                    original_export_verified=True,
                    original_export_method="pLFTfd",
                )
            self.write(
                state,
                stage="downloaded",
                original_sha256=digest(source),
                original_backup_bytes=source.stat().st_size,
            )
        if output.exists():
            # A finished file is moved atomically by convert. Resume after conversion.
            fut = concurrent.futures.Future()
            fut.set_result(
                {"avif_sha256": digest(output), "avif_bytes": output.stat().st_size}
            )
        else:
            args = (
                source,
                output,
                state.get("quality", self.quality),
                state.get("speed", self.speed),
                self.threads,
                (er[3], er[4] or 0),
                self.gps(ext),
                live,
            )
            if pool is None:
                fut = concurrent.futures.Future()
                fut.set_result(convert(*args))
            else:
                fut = pool.submit(convert, *args)
        return state, fut

    def backup_live(self, state, source):
        """Back up every original component before discarding cloud motion."""
        if state.get("live_backup_manifest"):
            self.verify_live_backup(state)
            return
        files = originals(self.api, [state["original_key"]])
        files = [(n, b) for n, b in files if Path(n).suffix.lower() != ".json"]
        movie_suffixes = {".mov", ".mp4", ".m4v"}
        stills = [
            (n, b) for n, b in files if Path(n).suffix.lower() not in movie_suffixes
        ]
        if len(stills) != 1:
            raise RuntimeError(
                "Live Photo export must contain exactly one primary image"
            )
        if sum(len(b) for _, b in files) != state["original_bytes"]:
            raise RuntimeError(
                "Live Photo component sizes do not match the recorded combined original"
            )
        manifest = []
        for i, (name, data) in enumerate(files):
            movie = Path(name).suffix.lower() in movie_suffixes
            target = (
                source.parent
                / ("original-motion-" + str(i) + Path(name).suffix.lower())
                if movie
                else source
            )
            atomic_bytes(target, data)
            manifest.append(
                {
                    "path": str(target),
                    "sha256": sha(data),
                    "bytes": len(data),
                    "role": "motion" if movie else "still",
                }
            )
        image = next(item for item in manifest if item["role"] == "still")
        self.write(
            state,
            live_backup_manifest=manifest,
            original_sha256=image["sha256"],
            original_backup_bytes=image["bytes"],
            original_export_verified=True,
            original_export_method="pLFTfd",
            stage="downloaded",
        )

    def verify_live_backup(self, state):
        manifest = state.get("live_backup_manifest")
        if (
            not manifest
            or sum(item["bytes"] for item in manifest) != state["original_bytes"]
        ):
            raise RuntimeError(
                "Complete Live Photo original backup is required before cloud replacement"
            )
        for item in manifest:
            path = Path(item["path"])
            if (
                not path.is_file()
                or path.stat().st_size != item["bytes"]
                or digest(path) != item["sha256"]
            ):
                raise RuntimeError(
                    "Live Photo original component backup changed; retain the cloud original"
                )

    def commit(self, state, future):
        result = future.result()
        output = Path(state["output"])
        ambiguous = state.get("stage") == "committing"
        if state.get("avif_sha256") and state["avif_sha256"] != result["avif_sha256"]:
            raise RuntimeError("Encoded output changed since journal; upload refused")
        self.write(
            state,
            stage="committing" if ambiguous else "converted",
            avif_sha256=result["avif_sha256"],
            avif_bytes=output.stat().st_size,
            sha1=sha1_b64(output.read_bytes()),
            display_size=result.get("display_size"),
        )

        def intent(**kw):
            self.write(state, **kw)

        key = upload(
            self.api,
            output,
            state["original_ext"][0][3],
            intent,
            reconcile_only=ambiguous,
        )
        if key == state["original_key"]:
            raise RuntimeError("Replacement equals original")
        self.write(
            state, stage="uploaded", replacement_key=key, uploaded_at=time.time()
        )
        self.queue(state)
        self.log(
            "uploaded",
            key=state["original_key"],
            replacement=key,
            original_bytes=state["original_bytes"],
            avif_bytes=state["avif_bytes"],
        )

    def finalize(self, state, defer_trash=False, cloud_values=None):
        live = "146008172" in attrs(state.get("inventory_row", []))
        if live and state.get("live_photo_policy") != "still":
            raise RuntimeError(
                "Live Photo replacement requires explicit still policy in its journal"
            )
        key = state["replacement_key"]
        old = state["original_key"]
        ext = state["original_ext"]
        er = ext[0]
        if state.get("stage") == "trashing":
            if old in self.api.in_trash([old], exhaustive=True):
                self.write(state, stage="trashed", trashed_at=time.time())
                return True
        values = cloud_values or self.api.batch(
            [
                ["VrseUb", [key, None, None, None, None]],
                ["fDcn4b", [key, 1, None, None, 1]],
            ]
        )
        info, newext = values
        if not info or not newext:
            return False
        if at(info, 0, 0) != key or at(newext, 0, 0) != key:
            raise RuntimeError("Replacement metadata identity mismatch")
        if live and "146008172" in attrs(info[0]):
            raise RuntimeError(
                "Replacement is still a Live Photo; retain the combined original"
            )
        ne = newext[0]
        if at(newext, 0, 30, 2) != 2:
            raise RuntimeError(
                "Image replacement must use Original quality; retain source and see README recovery notes"
            )
        if not ne[2].lower().endswith(".avif") or ne[5] != state["avif_bytes"]:
            return False
        if state.get("display_size") and list(ne[6:8]) != list(state["display_size"]):
            raise RuntimeError("Cloud image dimensions mismatch; retain source")
        # Capture date and timezone are cloud metadata, independently verified.
        if ne[3:5] != er[3:5]:
            if state.get("date_repair_attempted"):
                raise RuntimeError("Capture date repair did not verify")
            self.write(state, date_repair_attempted=True)
            self.api.rpc("DaSgWe", [[[info[0][3], er[3] / 1000, (er[4] or 0) / 1000]]])
            return False
        if state.get("download_verified_sha256") != state["avif_sha256"]:
            files = originals(self.api, [key])
            hashes = {sha(content) for _, content in files}
            if state["avif_sha256"] not in hashes:
                return False
            self.write(state, download_verified_sha256=state["avif_sha256"])
        self.write(
            state,
            stage="verified",
            verified_at=time.time(),
            verification="byte-identical-true-original-export+capture-date+dimensions",
        )
        # Preserve every album that the original's extended metadata reports.
        missing = album_ids(ext) - album_ids(newext)
        for album in er[19] or []:
            if album[0] in missing:
                self.api.add_album([key], album[0], bool(at(album, -1, "72930366", 4)))
        if missing:
            check = self.api.extended(key)
            if not album_ids(ext) <= album_ids(check):
                return False
        dedup = info[0][3]
        if er[1] and at(newext, 0, 1) != er[1]:
            self.api.rpc("AQNOFd", [None, er[1], dedup])
            if at(self.api.extended(key), 0, 1) != er[1]:
                raise RuntimeError("Description mismatch")
        inv = state["inventory_row"]
        favorite = bool(at(inv, -1, "163238866", 0))
        archived = bool(at(inv, 13))
        if favorite and not at(attrs(info[0]), "163238866", 0):
            self.api.rpc("Ftfh0", [[[None, dedup]], [1]])
        if archived and not at(info, 0, 13):
            self.api.rpc("w7TP3c", [[[None, [1], [None, dedup]]], None, 1])
        if favorite or archived:
            check = self.api.info(key)
            if favorite and not at(attrs(check[0]), "163238866", 0):
                raise RuntimeError("Favorite mismatch")
            if archived and not at(check, 0, 13):
                raise RuntimeError("Archive mismatch")
        if state.get("source") and (
            digest(state["source"]) != state["original_sha256"]
        ):
            raise RuntimeError("Original backup size mismatch; retain original")
        if live:
            self.verify_live_backup(state)
        self.write(
            state,
            stage="trashing",
            albums_preserved=True,
            metadata_verified_at=time.time(),
        )
        if defer_trash:
            return True
        # Trash can be retried after read-back reconciliation; never delete the replacement.
        self.api.trash([state["original_info"][0][3]])
        trash = self.api.rpc("zy0IHe", [None])
        if old not in {r[0] for r in (at(trash, 0, default=[]) or [])}:
            raise RuntimeError("Original was not found in recent Trash page")
        self.write(
            state,
            stage="trashed",
            trashed_at=time.time(),
            replacement_quota=at(ne, 30, 1, default=state["avif_bytes"]),
        )
        self.log(
            "replaced",
            key=old,
            replacement=key,
            saved_bytes=state["original_bytes"] - state["avif_bytes"],
        )
        return True

    def drain(self, force=False):
        remaining = []
        with self.wait_lock:
            pending = self.waiting
            self.waiting = []
        eligible = [
            s
            for s in pending
            if s.get("download_verified_sha256") != s.get("avif_sha256")
        ]
        for offset in range(0, len(eligible), 30):
            group = eligible[offset : offset + 30]
            try:
                with original_files(
                    self.api, [s["replacement_key"] for s in group]
                ) as files:
                    hashes = {digest(path) for _, path in files}
                for s in group:
                    if s["avif_sha256"] in hashes:
                        self.write(s, download_verified_sha256=s["avif_sha256"])
            except Exception as e:
                self.log("batch_verification_pending", error=str(e), count=len(group))
        # Copy album membership in groups. Per-item readback below verifies it.
        groups = {}
        for state in pending:
            if state.get("download_verified_sha256") != state.get("avif_sha256"):
                continue
            for album in at(state, "original_ext", 0, 19, default=[]) or []:
                groups.setdefault(
                    (album[0], bool(at(album, -1, "72930366", 4))), []
                ).append(state["replacement_key"])
        for (album, shared), keys in groups.items():
            keys = list(dict.fromkeys(keys))
            for start in range(0, len(keys), 100):
                try:
                    self.api.add_album(keys[start : start + 100], album, shared)
                except Exception as e:
                    self.log("album_batch_retry_per_item", error=str(e))
        # Forty metadata RPCs share one request. This removes a network roundtrip
        # per image while preserving fresh per-item metadata validation.
        cloud = {}
        verified = [
            s
            for s in pending
            if s.get("download_verified_sha256") == s.get("avif_sha256")
        ]
        for start in range(0, len(verified), 20):
            group = verified[start : start + 20]
            calls = []
            for s in group:
                key = s["replacement_key"]
                calls.extend(
                    [
                        ["VrseUb", [key, None, None, None, None]],
                        ["fDcn4b", [key, 1, None, None, 1]],
                    ]
                )
            try:
                results = self.api.batch(calls)
                for i, s in enumerate(group):
                    cloud[s["replacement_key"]] = results[i * 2 : i * 2 + 2]
            except Exception as e:
                self.log("metadata_batch_retry_per_item", error=str(e))

        def finish(state):
            try:
                # A batch export can temporarily contain server-generated compatibility
                # copies. Wait for the next batch rather than exporting every item again.
                if state.get("download_verified_sha256") != state.get("avif_sha256"):
                    return ("pending", state)
                if not self.finalize(state, True, cloud.get(state["replacement_key"])):
                    return ("pending", state)
                return (
                    "done"
                    if state.get("stage") in ("trashed", "live_photo_unchanged")
                    else "ready",
                    state,
                )
            except Exception as e:
                self.write(state, error=str(e))
                self.errors.append(state["original_key"])
                self.log("finalize_error", key=state["original_key"], error=str(e))
            return ("error", state)

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as finalize_pool:
            finished = list(finalize_pool.map(finish, pending))
        remaining = [s for kind, s in finished if kind == "pending"]
        ready = [s for kind, s in finished if kind == "ready"]
        for offset in range(0, len(ready), 50):
            group = ready[offset : offset + 50]
            try:
                self.api.trash([s["original_info"][0][3] for s in group])
            except Exception as e:
                self.log("batch_trash_reconciling", error=str(e))
            trash = self.api.rpc("zy0IHe", [None])
            found = {r[0] for r in at(trash, 0, default=[]) or []}
            for state in group:
                if state["original_key"] in found:
                    self.write(state, stage="trashed", trashed_at=time.time())
                    self.log(
                        "replaced",
                        key=state["original_key"],
                        replacement=state["replacement_key"],
                        saved_bytes=state["original_bytes"] - state["avif_bytes"],
                    )
                else:
                    remaining.append(state)
        with self.wait_lock:
            self.waiting.extend(remaining)

    def run(self, limit=None, rows=None):
        def task(row):
            try:
                prepared = self.prepare(row, None)
                if prepared:
                    self.commit(*prepared)
            except Exception as e:
                state = self.state(row[0])
                self.write(state, error=str(e))
                self.errors.append(row[0])
                self.log("item_error", key=row[0], error=str(e))
                self.stop_event.set()

        rows = [
            r
            for r in (
                rows
                if rows is not None
                else self.selection.select(self.inventory, self.catalog)
            )
            if not is_video(r)
        ]
        if limit is not None:
            chosen = []
            for row in rows:
                if self.state(row[0]).get("stage") not in (
                    "trashed",
                    "already_avif",
                    "video",
                ) and not is_video(row):
                    chosen.append(row)
                if len(chosen) >= limit:
                    break
            rows = chosen
        from workflow import run_batches

        run_batches(
            rows,
            task=task,
            drain=self.drain,
            errors=self.errors,
            waiting=lambda: self.waiting,
            workers=self.workers,
            batch_size=self.batch_size,
            after_batch=self.after_batch,
            stop_event=self.stop_event,
            timeout=1800,
            pause=2,
        )
        self.log("pass_finished", errors=len(self.errors), waiting=len(self.waiting))
        return self.errors, self.waiting
