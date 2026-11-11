"""Direct web RPC transport via local broker; browser retains authentication."""

import base64, json, time, urllib.request, threading, os
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("GOOGLE_PHOTOS_DATA_DIR", CODE_ROOT)).resolve()


def at(x, *path, default=None):
    try:
        for k in path:
            x = x[k]
        return x
    except (IndexError, KeyError, TypeError):
        return default


class API:
    def __init__(self):
        self.config = json.loads((CODE_ROOT / ".bridge.json").read_text())
        self.write_lock = threading.RLock()
        self.authuser = 0
        self.account = None

    def local(self, path, data=None):
        req = urllib.request.Request(
            self.config["url"] + path + "?key=" + self.config["key"],
            data=json.dumps(data).encode() if data is not None else None,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)

    def call(self, task, timeout=180):
        if self.account:
            task["account"] = self.account
        taskid = self.local("/enqueue", task)["id"]
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            result = self.local("/result/" + taskid)
            if result:
                if not result.get("ok"):
                    raise RuntimeError(result.get("error"))
                if task.get("op") == "hello":
                    self.authuser = result["value"].get("authuser", 0)
                    self.account = result["value"]["account"]
                return result["value"]
            time.sleep(0.04)
        raise TimeoutError("Browser bridge did not answer; do not retry writes blindly")

    def batch(self, calls):
        return self.call({"op": "rpc", "rpc": calls, "data": None})

    def rpc(self, rpc, data):
        task = {"op": "rpc", "rpc": rpc, "data": data}
        if rpc in {
            "mdpdU",
            "XwAOJf",
            "E1Cajb",
            "laUYf",
            "DaSgWe",
            "AQNOFd",
            "Ftfh0",
            "w7TP3c",
        }:
            with self.write_lock:
                return self.call(task)
        return self.call(task)

    def fetch(self, url, method="GET", headers=None, body=None):
        task = {"op": "fetch", "url": url, "method": method, "headers": headers or {}}
        if isinstance(body, bytes):
            task["body64"] = base64.b64encode(body).decode()
        elif body is not None:
            task["body"] = body
        r = self.call(task, 600)
        r["body"] = base64.b64decode(r.pop("body64"))
        return r

    def info(self, key):
        return self.rpc("VrseUb", [key, None, None, None, None])

    def extended(self, key):
        return self.rpc("fDcn4b", [key, 1, None, None, 1])

    def library(self, cursor=None, size=500):
        return self.rpc("lcxiM", [cursor, None, size, None, 1, 3])

    def albums(self, cursor=None):
        return self.rpc(
            "Z5xsfc", [cursor, None, None, None, 1, None, None, 100, [2], 5]
        )

    def album(self, key, cursor=None):
        return self.rpc("snAcKc", [key, cursor, None, None])

    def add_album(self, keys, album, shared=False):
        return (
            self.rpc(
                "laUYf",
                [album, [2, None, [[[key]] for key in keys], None, None, None, [1]]],
            )
            if shared
            else self.rpc("E1Cajb", [keys, album])
        )

    def trash(self, keys):
        return self.rpc("XwAOJf", [None, 1, keys, 3])

    def purge(self, keys, *, confirmed=False):
        """Targeted permanent removal; never an account-wide Trash operation.

        Web UI trace: XwAOJf [null,2,[dedup keys],2]. Require a deliberate
        confirmation and a bounded explicit key list for every request.
        """
        if confirmed is not True:
            raise ValueError("Permanent deletion requires explicit confirmation")
        if (
            not keys
            or len(keys) > 50
            or len(set(keys)) != len(keys)
            or not all(isinstance(k, str) and k for k in keys)
        ):
            raise ValueError("Permanent deletion requires 1..50 unique explicit keys")
        return self.rpc("XwAOJf", [None, 2, list(keys), 2])

    def in_trash(self, media_keys, exhaustive=False):
        """Readback by media ID, paginating when reconciling an interrupted job."""
        return set(self.trash_items(media_keys, exhaustive=exhaustive))

    def trash_items(self, media_keys, exhaustive=False):
        """Return authoritative Trash rows, including their deletion keys."""
        wanted = set(media_keys)
        found = {}
        cursor = None
        seen = set()
        while wanted - found.keys():
            page = self.rpc("zy0IHe", [cursor])
            found.update(
                {r[0]: r for r in (at(page, 0, default=[]) or []) if r[0] in wanted}
            )
            cursor = at(page, 1)
            if not exhaustive or not cursor:
                break
            if cursor in seen:
                raise RuntimeError("Trash pagination loop; cannot reconcile removal")
            seen.add(cursor)
        return found


if __name__ == "__main__":
    a = API()
    print(a.call({"op": "hello"}))
    p = a.library(size=5)
    (ROOT / "research" / "live-library-probe.json").write_text(json.dumps(p, indent=2))
    print("library entries", len(p[0]), "next cursor", bool(p[1]))
    for x in p[0]:
        print(x[0], x[2], x[1][1:3])


def sha1_b64(data):
    import hashlib

    return base64.b64encode(hashlib.sha1(data).digest()).decode()


def upload(api, path, modified_ms, write_ahead=lambda **kw: None):
    """Hash lookup reconciles a commit after interruption; never blindly repeat mdpdU."""
    import re
    from urllib.parse import urlparse

    path = Path(path)
    import hashlib

    fingerprint = hashlib.sha1()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            fingerprint.update(block)
    sha = base64.b64encode(fingerprint.digest()).decode()
    matches = api.rpc("swbisb", [[sha], None, 3, 0])
    for entry in at(matches, 0, default=[]) or []:
        if entry[0] == sha and at(entry, 1, 0):
            return entry[1][0]
    size = path.stat().st_size
    n = size
    var = []
    while n > 127:
        var.append((n & 127) | 128)
        n >>= 7
    var.append(n)
    name = path.name
    common = {
        "x-goog-hash": "sha1=" + sha,
        "x-goog-upload-file-name": "".join(
            c if 32 <= ord(c) < 127 else "_" for c in name
        ),
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
    }
    # Current bridge's account is the default tab account, as exposed by WIZ_global_data.
    start = api.fetch(
        "https://photos.google.com/_/upload/uploadmedia/interactive?authuser="
        + str(api.authuser),
        "POST",
        common
        | {
            "x-goog-upload-protocol": "resumable",
            "x-goog-upload-header-content-length": str(size),
            "x-goog-upload-command": "start",
        },
        bytes([8, 2, 16, 1, 24, 1, 32, 1, 56] + var + [64, 1]),
    )
    if (
        start["status"] != 200
        or start["headers"].get("x-goog-upload-status") != "active"
    ):
        raise RuntimeError("Upload start failed: " + str(start["status"]))
    target = start["headers"].get("x-goog-upload-url", "")
    u = urlparse(target)
    if (
        u.scheme != "https"
        or u.hostname != "photos.google.com"
        or u.path != "/_/upload/uploadmedia/interactive"
    ):
        raise RuntimeError("Unexpected upload session URL")
    offset = 0
    token = None
    while offset < size:
        with path.open("rb") as source:
            source.seek(offset)
            piece = source.read(8 * 1024 * 1024)
        if not piece:
            raise RuntimeError("Upload source changed or was truncated")
        last = offset + len(piece) == size
        result = api.fetch(
            target,
            "POST",
            common
            | {
                "x-goog-upload-offset": str(offset),
                "x-goog-upload-command": "upload, finalize" if last else "upload",
            },
            piece,
        )
        if result["status"] != 200:
            raise RuntimeError("Upload chunk failed: " + str(result["status"]))
        if last:
            if result["headers"].get("x-goog-upload-status") != "final":
                raise RuntimeError("Upload not finalized")
            token = result["body"].decode().strip()
        offset += len(piece)
    if not token or not re.fullmatch(r"[A-Za-z0-9+/=_-]{16,8192}", token):
        raise RuntimeError("Invalid upload token")
    write_ahead(stage="committing", sha1=sha)
    with api.write_lock:
        data = api.rpc("mdpdU", [[[token, name, int(modified_ms), 2, 2]]])
    key = at(data, 0, 0, 1, 0)
    if not isinstance(key, str) or not key.startswith("AF1"):
        raise RuntimeError("Commit outcome unknown; reconcile using hash")
    return key


def originals(api, keys):
    """True originals export. Legacy yCLA7/dnv2s may export compatibility JPEGs.
    pLFTfd returns a short-lived Google-signed ZIP URL; no cookies leave Chrome.
    """
    import tempfile, zipfile, urllib.parse

    result = api.rpc("pLFTfd", [keys, None, None])
    url = at(result, 0)
    u = urllib.parse.urlparse(url)
    if u.scheme != "https" or u.hostname != "video-downloads.googleusercontent.com":
        raise RuntimeError("Unexpected original-export host")
    with urllib.request.urlopen(url, timeout=180) as response:
        if response.status != 200:
            raise RuntimeError("Original-export HTTP " + str(response.status))
        with tempfile.TemporaryFile() as file:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                file.write(chunk)
            file.seek(0)
            if not zipfile.is_zipfile(file):
                file.seek(0)
                return [("original", file.read())]
            with zipfile.ZipFile(file) as archive:
                return [
                    (entry.filename, archive.read(entry))
                    for entry in archive.infolist()
                    if not entry.is_dir()
                ]
