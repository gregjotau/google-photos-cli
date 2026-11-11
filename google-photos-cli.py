#!/usr/bin/env python3
# /// script
# requires-python = ">=3.15"
# dependencies = ["Pillow==13.0.0", "playwright==1.65.0"]
# ///
"""Google Photos AVIF migration using direct authenticated web requests.
Google authentication stays inside Chrome. Start `connect`, or run `broker` and
install the generated bootstrap.js once in an existing signed-in Photos tab.
"""

import argparse, asyncio, collections, json, os, subprocess, sys

if sys.version_info < (3, 15):
    raise SystemExit(
        "Python 3.15+ is required. Use: uv run google-photos-cli.py --help"
    )
from pathlib import Path
from datetime import date
from selection import from_args, year_value, positive_int

CODE = Path(__file__).resolve().parent


def filters(p):
    p.add_argument(
        "--year",
        type=year_value,
        action="append",
        help="Capture year, or current; repeat for multiple years",
    )
    p.add_argument(
        "--from-date", type=date.fromisoformat, help="Inclusive capture date YYYY-MM-DD"
    )
    p.add_argument(
        "--to-date", type=date.fromisoformat, help="Inclusive capture date YYYY-MM-DD"
    )
    p.add_argument("--media", choices=["all", "images", "videos"], default="all")
    p.add_argument(
        "--album", action="append", help="Album ID; repeat to match any of these albums"
    )
    p.add_argument(
        "--filename",
        help="Case-insensitive glob over original filenames; quote it in your shell",
    )

    def megabytes(value):
        import math

        n = float(value)
        if not math.isfinite(n) or n < 0:
            raise argparse.ArgumentTypeError("Must be a finite nonnegative size")
        return n

    p.add_argument(
        "--min-size-mb",
        type=megabytes,
        default=0,
        help="Minimum original size in decimal MB",
    )
    p.add_argument("--exclude-favorites", action="store_true")
    p.add_argument("--exclude-archived", action="store_true")
    p.add_argument(
        "--limit",
        type=positive_int,
        help="First N matching assets in snapshot order; stable across resumes",
    )


def encoding_options(p):
    p.add_argument(
        "--quality", type=int, default=45, choices=range(0, 101), metavar="0..100"
    )
    p.add_argument(
        "--speed", type=int, default=6, choices=range(0, 11), metavar="0..10"
    )
    p.add_argument("--workers", type=positive_int, default=4)
    p.add_argument("--threads", type=positive_int, default=3)


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--data-dir",
        type=Path,
        default=CODE,
        help="Separate directory for each Google account",
    )
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")
    sub.add_parser(
        "self-test", help="Run offline safety and filter tests without a Google account"
    )
    sub.add_parser("broker", help="Serve local API bridge; leave running")
    sub.add_parser(
        "bootstrap", help="Write bootstrap.js for the existing Chrome session"
    )
    con = sub.add_parser(
        "connect", help="Open a persistent Chrome profile and connect API bridge"
    )
    con.add_argument("--profile", type=Path, default=CODE / ".chrome-profile")
    sub.add_parser(
        "scan", help="Snapshot all library and archived media via paginated API"
    )
    sub.add_parser("catalog", help="Fetch metadata in batches for snapshot")
    mig = sub.add_parser(
        "migrate", help="Download, encode, upload, verify, copy albums, trash originals"
    )
    encoding_options(mig)
    vid = sub.add_parser("videos", help="Optimize account videos as HEVC/AAC MP4")
    combined = sub.add_parser(
        "run",
        help="Run resumable image and video migration; keep Live Photos unchanged",
    )
    encoding_options(combined)
    plan = sub.add_parser(
        "plan", help="Preview matching assets and sizes without uploading or trashing"
    )
    for command in (mig, vid, combined, plan):
        filters(command)
    for command in (mig, vid, combined):
        command.add_argument(
            "--apply",
            action="store_true",
            help="Upload verified replacements and move originals to recoverable Trash",
        )
        command.add_argument(
            "--dry-run", action="store_true", help="Preview only; overrides --apply"
        )
    sub.add_parser("status", help="Show journal counts and measured file savings")
    sub.add_parser(
        "report",
        help="Image and video savings for confirmed replacements in local journals",
    )
    audit = sub.add_parser(
        "audit", help="Rescan library and reconcile selected replacements"
    )
    filters(audit)
    cv = sub.add_parser("convert", help="Convert one local image without cloud changes")
    cv.add_argument("source", type=Path)
    cv.add_argument("output", type=Path)
    cv.add_argument("--quality", type=int, default=45)
    cv.add_argument("--speed", type=int, default=6)
    return p


def preview(root, selection, inventory, catalog):
    from migration import attrs, is_video

    rows = selection.select(inventory, catalog)
    counts = collections.Counter()
    sizes = collections.Counter()
    pending = collections.Counter()
    for row in rows:
        e = catalog["items"][row[0]][0]
        kind = (
            "live_photos_kept"
            if "146008172" in attrs(row)
            else "videos"
            if is_video(row)
            else "existing_avif_kept"
            if (e[2] or "").lower().endswith(".avif")
            else "images"
        )
        counts[kind] += 1
        sizes[kind] += e[5] or 0
        folder = "work-video" if is_video(row) else "work-api"
        path = root / folder / row[0] / "state.json"
        stage = json.loads(path.read_text()).get("stage") if path.exists() else None
        if kind in ("images", "videos") and stage not in (
            "trashed",
            "already_efficient",
        ):
            pending[kind] += 1
    return {
        "matching_assets": len(rows),
        "counts": dict(counts),
        "source_bytes": dict(sizes),
        "pending": dict(pending),
        "locked_folder_excluded": True,
        "cloud_changes": False,
        "note": "Estimates use recorded source sizes, not predicted encoding savings. Live Photos are kept unchanged.",
    }


def scan(root, api, destination="inventory-api.json"):
    from photos_api import at
    from migration import save

    items = {}
    cursor = None
    seen = set()
    pages = 0
    account = api.call({"op": "hello"})["account"]
    while True:
        data = api.library(cursor, 500)
        pages += 1
        for row in data[0] or []:
            items[row[0]] = row
        cursor = at(data, 1)
        result = {
            "account": account,
            "complete": not bool(cursor),
            "pages": pages,
            "items": list(items.values()),
        }
        save(root / destination, result)
        print(f"Page {pages}: {len(items)} media", flush=True)
        if not cursor:
            return result
        if cursor in seen:
            raise RuntimeError("Pagination loop; scan is incomplete")
        seen.add(cursor)


def catalog(root, api, inventory, destination="catalog-api.json"):
    from migration import save

    if api.call({"op": "hello"})["account"] != inventory["account"]:
        raise RuntimeError("Connected account differs from inventory")

    path = root / destination
    cat = (
        json.loads(path.read_text())
        if path.exists()
        else {"account": inventory["account"], "items": {}}
    )
    if cat["account"] != inventory["account"]:
        raise RuntimeError("Catalog account mismatch")
    keys = [r[0] for r in inventory["items"] if r[0] not in cat["items"]]
    for start in range(0, len(keys), 40):
        group = keys[start : start + 40]
        data = api.batch([["fDcn4b", [k, 1, None, None, 1]] for k in group])
        for key, result in zip(group, data):
            if not result or not result[0] or result[0][0] != key:
                raise RuntimeError(
                    f"Metadata missing or identity mismatch for {key}; catalog remains incomplete"
                )
            cat["items"][key] = result
        save(path, cat)
        print(f"Metadata: {len(cat['items'])} / {len(inventory['items'])}", flush=True)
    return cat


def status(root):
    states = [
        json.loads(p.read_text()) for p in (root / "work-api").glob("*/state.json")
    ]
    done = [s for s in states if s.get("stage") == "trashed"]
    video_states = [
        json.loads(p.read_text()) for p in (root / "work-video").glob("*/state.json")
    ]
    return {
        "videos": dict(collections.Counter(s.get("stage") for s in video_states)),
        "states": dict(collections.Counter(s.get("stage") for s in states)),
        "original_bytes_replaced": sum(s.get("original_bytes", 0) for s in done),
        "avif_bytes_uploaded": sum(s.get("avif_bytes", 0) for s in done),
        "file_bytes_saved": sum(
            s.get("original_bytes", 0) - s.get("avif_bytes", 0) for s in done
        ),
        "errors": [
            {k: s.get(k) for k in ("original_key", "filename", "error", "stage")}
            for s in states
            if s.get("error") and s.get("stage") != "trashed"
        ],
    }


def savings_report(root):
    result = {
        "scope": "Confirmed replacements only; unchanged assets excluded",
        "images": {},
        "videos": {},
    }
    for name, folder, size_field in [
        ("images", "work-api", "avif_bytes"),
        ("videos", "work-video", "encoded_bytes"),
    ]:
        states = [
            json.loads(p.read_text()) for p in (root / folder).glob("*/state.json")
        ]
        done = [s for s in states if s.get("stage") == "trashed"]
        before = sum(s["original_bytes"] for s in done)
        after = sum(s[size_field] for s in done)
        result[name] = {
            "replaced": len(done),
            "before_bytes": before,
            "after_bytes": after,
            "saved_bytes": before - after,
            "saved_percent": round(100 * (before - after) / before, 2) if before else 0,
        }
    return result


async def connect(profile):
    from playwright.async_api import async_playwright

    # Local-only server; no Google cookies/tokens are exported to Python.
    config = CODE / ".bridge.json"
    import urllib.request

    active = False
    if config.exists():
        c = json.loads(config.read_text())
        try:
            with urllib.request.urlopen(
                c["url"] + "/bootstrap?key=" + c["key"], timeout=2
            ) as r:
                active = r.status == 200
        except Exception:
            pass
    broker = None
    if not active:
        broker = subprocess.Popen([sys.executable, str(CODE / "api-bridge.py")])
        for _ in range(50):
            if config.exists():
                c = json.loads(config.read_text())
                try:
                    with urllib.request.urlopen(
                        c["url"] + "/bootstrap?key=" + c["key"], timeout=0.2
                    ) as r:
                        if r.status == 200:
                            break
                except Exception:
                    pass
            await asyncio.sleep(0.1)
    c = json.loads(config.read_text())
    script = (CODE / "api-bridge.js").read_text().replace("__BRIDGE_KEY__", c["key"])
    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            str(profile.resolve()), channel="chrome", headless=False
        )
        try:
            page = await context.new_page()
            await page.goto("https://photos.google.com/")
            print(
                "Sign in to the intended Google account in Chrome. Allow the local-device connection when Chrome asks.",
                flush=True,
            )
            while not await page.evaluate("!!window.WIZ_global_data?.SNlM0e"):
                await asyncio.sleep(1)
            # No DOM selectors for Photos operations; direct authenticated requests only.
            # Long-lived worker is started without awaiting its polling loop.
            script = script.replace(
                "(async()=>{", "window.avifBridgeWorker = (async()=>{", 1
            )
            await page.evaluate("() => { " + script + "; return true; }")
            print(
                "Connected. Run scan, catalog, migrate and audit in another terminal. Keep this window open.",
                flush=True,
            )
            while not page.is_closed():
                await asyncio.sleep(1)
        finally:
            await context.close()
            if broker:
                broker.terminate()


def main():
    p = parser()
    args = p.parse_args()
    root = args.data_dir.resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    selection = None
    if args.command in ("run", "migrate", "videos", "plan", "audit"):
        try:
            selection = from_args(args)
        except ValueError as e:
            p.error(str(e))
        if args.command in ("migrate", "videos"):
            wanted = "images" if args.command == "migrate" else "videos"
            if selection.media not in ("all", wanted):
                p.error(f"{args.command} only processes {wanted}")
            selection.media = wanted
    os.environ["GOOGLE_PHOTOS_DATA_DIR"] = str(root)
    # One modifying workflow per account directory, including combined runs.
    lock = None
    if args.command in ("run", "migrate", "videos") and args.apply and not args.dry_run:
        import fcntl

        lock = (root / ".migration.lock").open("a")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("A migration is already running for this data directory")
    from photos_api import API
    from migration import Migration

    if args.command in ("run", "migrate", "videos", "plan"):
        invpath = root / "inventory-api.json"
        catpath = root / "catalog-api.json"
        inv = json.loads(invpath.read_text()) if invpath.exists() else scan(root, API())
        if not inv.get("complete"):
            raise SystemExit("Inventory incomplete; run scan again before proceeding")
        cat = json.loads(catpath.read_text()) if catpath.exists() else None
        if not cat or any(r[0] not in cat["items"] for r in inv["items"]):
            cat = catalog(root, API(), inv)
        if cat["account"] != inv["account"]:
            raise SystemExit("Catalog account mismatch")
        if args.command == "plan" or args.dry_run or not args.apply:
            print(json.dumps(preview(root, selection, inv, cat), indent=2))
            if args.command != "plan":
                print(
                    "Preview only. Add --apply to upload replacements and move verified originals to Trash."
                )
            return
    if args.command == "broker":
        os.execv(sys.executable, [sys.executable, str(CODE / "api-bridge.py")])
    elif args.command == "self-test":
        raise SystemExit(
            subprocess.call(
                [
                    sys.executable,
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    str(CODE / "tests"),
                    "-v",
                ]
            )
        )
    elif args.command == "bootstrap":
        cfg = json.loads((CODE / ".bridge.json").read_text())
        dest = root / "bootstrap.js"
        dest.write_text(
            (CODE / "api-bridge.js").read_text().replace("__BRIDGE_KEY__", cfg["key"])
        )
        dest.chmod(0o600)
        print(
            f"Bootstrap saved to {dest}. Run its contents once in the signed-in Photos DevTools console."
        )
    elif args.command == "connect":
        asyncio.run(connect(args.profile))
    elif args.command == "doctor":
        from converter import run

        print(run("avifenc", "--version"))
        print("ExifTool", run("exiftool", "-ver").strip())
        import PIL

        print("Pillow", PIL.__version__)
        print(run("ffmpeg", "-version").splitlines()[0])
        print(run("ffprobe", "-version").splitlines()[0])
    elif args.command == "convert":
        from converter import convert

        print(
            json.dumps(
                convert(args.source, args.output, args.quality, args.speed), indent=2
            )
        )
    elif args.command == "status":
        print(json.dumps(status(root), indent=2))
    elif args.command == "report":
        print(json.dumps(savings_report(root), indent=2))
    elif args.command == "videos":
        from video_migration import Videos

        errors, waiting = Videos(selection=selection).run()
        if errors or waiting:
            raise SystemExit(2)
    elif args.command == "run":
        import concurrent.futures
        from video_migration import Videos

        def images():
            return Migration(
                args.quality,
                args.speed,
                args.threads,
                args.workers,
                selection=selection,
            ).run()

        def videos():
            return Videos(selection=selection).run()

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            image_job = pool.submit(images)
            video_job = pool.submit(videos)
            image_job.result()
            video_job.result()
        images()
        videos()
        from audit_library import audit

        if not audit(root, selection=selection)["complete"]:
            raise SystemExit(2)
    elif args.command == "scan":
        scan(root, API())
    elif args.command == "catalog":
        catalog(root, API(), json.loads((root / "inventory-api.json").read_text()))
    elif args.command == "migrate":
        errors, waiting = Migration(
            args.quality, args.speed, args.threads, args.workers, selection=selection
        ).run()
        print(json.dumps(status(root), indent=2))
        if errors or waiting:
            raise SystemExit(2)
    elif args.command == "audit":
        from audit_library import audit

        if not audit(root, selection=selection)["complete"]:
            raise SystemExit(2)


if __name__ == "__main__":
    main()
