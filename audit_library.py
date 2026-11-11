"""Fresh paginated library audit plus reconciliation of migration journals."""

import json
from pathlib import Path
from photos_api import API, ROOT, at, originals
from migration import attrs, is_video, save, album_ids


def audit(root=ROOT, verbose=True, selection=None):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "cli", Path(__file__).parent / "google-photos-cli.py"
    )
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    api = API()
    inv = cli.scan(root, api, "audit-inventory.json")
    # Refresh metadata, rather than relying on a previous audit's cache.
    old = root / "audit-catalog.json"
    if old.exists():
        old.unlink()
    cat = cli.catalog(root, api, inv, "audit-catalog.json")
    states = [
        json.loads(p.read_text()) for p in (root / "work-api").glob("*/state.json")
    ]
    vst = [
        json.loads(p.read_text()) for p in (root / "work-video").glob("*/state.json")
    ]
    scoped = bool(selection and selection.filtered)
    scope = None
    if scoped:
        original_inv = json.loads((root / "inventory-api.json").read_text())
        original_cat = json.loads((root / "catalog-api.json").read_text())
        scope = {r[0] for r in selection.select(original_inv, original_cat)}
        states = [s for s in states if s["original_key"] in scope]
        vst = [s for s in vst if s["original_key"] in scope]
        target = set(scope)
        for s in states + vst:
            if s.get("replacement_key"):
                target.add(s["replacement_key"])
    else:
        target = {r[0] for r in inv["items"]}
    avif = []
    live = []
    videos = []
    bad = []
    for row in inv["items"]:
        if row[0] not in target:
            continue
        e = cat["items"].get(row[0])
        entry = {
            "key": row[0],
            "filename": at(e, 0, 2),
            "bytes": at(e, 0, 5, default=0) or 0,
            "quota_bytes": at(e, 0, 30, 1, default=0) or 0,
        }
        if is_video(row):
            videos.append(entry)
        elif "146008172" in attrs(row):
            live.append(entry)
        elif (entry["filename"] or "").lower().endswith(".avif"):
            avif.append(entry)
        else:
            bad.append(entry)
    active = {r[0] for r in inv["items"]}
    problems = []
    proof_keys = set()
    original_inv = json.loads((root / "inventory-api.json").read_text())
    if original_inv["account"] != inv["account"]:
        raise RuntimeError("Audit account differs from original snapshot")
    journal = {s["original_key"]: s for s in states + vst}
    for row in original_inv["items"]:
        key = row[0]
        if scope is not None and key not in scope:
            continue
        s = journal.get(key, {})
        expected = s.get("replacement_key") if s.get("stage") == "trashed" else key
        if expected not in active:
            problems.append(
                {
                    "key": key,
                    "problem": "original and expected replacement do not reconcile",
                }
            )
        if is_video(row) and s.get("stage") not in ("trashed", "already_efficient"):
            problems.append(
                {
                    "key": key,
                    "problem": "video has not finished optimization assessment",
                }
            )

    def metadata_check(s, size_field):
        key = s.get("replacement_key")
        ext = cat["items"].get(key)
        expected = s.get("original_ext")
        if not ext or not expected:
            return
        current, original = ext[0], expected[0]
        if current[3:5] != original[3:5]:
            problems.append({"key": key, "problem": "capture date or timezone changed"})
        if current[5] != s.get(size_field):
            problems.append({"key": key, "problem": "stored replacement size changed"})
        if not album_ids(expected) <= album_ids(ext):
            problems.append(
                {"key": key, "problem": "original album membership missing"}
            )
        if original[1] and current[1] != original[1]:
            problems.append({"key": key, "problem": "description changed"})

    for s in states:
        stage = s.get("stage")
        if stage == "trashed":
            metadata_check(s, "avif_bytes")
            if s["original_key"] in active:
                problems.append(
                    {"key": s["original_key"], "problem": "original still active"}
                )
            key = s.get("replacement_key")
            if key not in active:
                problems.append({"key": key, "problem": "replacement absent"})
            if not s.get("avif_sha256") or s.get("download_verified_sha256") != s.get(
                "avif_sha256"
            ):
                problems.append(
                    {"key": key, "problem": "missing byte-identical export proof"}
                )
            else:
                proof_keys.add(key)
        elif (
            stage in ("already_avif", "live_photo_unchanged")
            and s["original_key"] not in active
        ):
            problems.append(
                {"key": s["original_key"], "problem": "retained image absent"}
            )
        elif stage not in ("video", "already_avif", "live_photo_unchanged"):
            problems.append(
                {
                    "key": s["original_key"],
                    "problem": "unfinished image journal",
                    "stage": stage,
                }
            )
    for s in vst:
        if s.get("stage") == "trashed":
            metadata_check(s, "encoded_bytes")
            if s["original_key"] in active:
                problems.append(
                    {"key": s["original_key"], "problem": "video original still active"}
                )
            if s.get("replacement_key") not in active:
                problems.append(
                    {
                        "key": s.get("replacement_key"),
                        "problem": "video replacement absent",
                    }
                )
            if not s.get("encoded_sha256") or s.get("encoded_sha256") != s.get(
                "download_verified_sha256"
            ):
                problems.append(
                    {
                        "key": s.get("replacement_key"),
                        "problem": "missing video export proof",
                    }
                )
        elif s.get("stage") == "already_efficient" and s["original_key"] not in active:
            problems.append(
                {"key": s["original_key"], "problem": "retained video absent"}
            )
        elif s.get("stage") != "already_efficient":
            problems.append(
                {
                    "key": s["original_key"],
                    "problem": "unfinished video journal",
                    "stage": s.get("stage"),
                }
            )
    # Items outside this migration's verified outputs also need AVIF magic validation.
    unknown = [e for e in avif if e["key"] not in proof_keys]
    for start in range(0, len(unknown), 40):
        group = unknown[start : start + 40]
        files = originals(api, [e["key"] for e in group])
        if not all(
            b[4:8] == b"ftyp" and (b"avif" in b[:80] or b"avis" in b[:80])
            for _, b in files
        ):
            for e in group:
                fs = originals(api, [e["key"]])
                if not all(
                    b[4:8] == b"ftyp" and (b"avif" in b[:80] or b"avis" in b[:80])
                    for _, b in fs
                ):
                    bad.append(e)
    result = {
        "account": inv["account"],
        "scope": "filtered_selection" if scoped else "whole_library_and_archive",
        "locked_folder_excluded": True,
        "pagination_complete": inv["complete"],
        "avif_images": len(avif),
        "live_photos_unchanged": len(live),
        "videos": len(videos),
        "non_avif_images": bad,
        "journal_problems": problems,
        "image_bytes": sum(e["bytes"] for e in avif + live),
        "video_bytes": sum(e["bytes"] for e in videos),
        "live_photo_bytes": sum(e["bytes"] for e in live),
        "images": avif,
        "retained_live_photos": live,
        "video_items": videos,
        "complete": inv["complete"] and not bad and not problems,
    }
    save(root / "audit-result.json", result)
    if verbose:
        print(
            json.dumps(
                {
                    k: v
                    for k, v in result.items()
                    if k not in ("images", "retained_live_photos", "video_items")
                },
                indent=2,
            )
        )
    return result


if __name__ == "__main__":
    raise SystemExit(0 if audit()["complete"] else 2)
