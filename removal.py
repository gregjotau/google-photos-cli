"""Confirmed, account-bound permanent removal after complete local backups.

No account-wide empty-Trash operation exists here. Legacy/trash-mode journals
are never upgraded to permanent mode by a rerun.
"""

import hashlib
import json
import threading
from pathlib import Path

from converter import digest
from migration import album_ids, is_video, save
from photos_api import API, at


def load_scope(root, selection):
    inv = json.loads((root / "inventory-api.json").read_text())
    cat = json.loads((root / "catalog-api.json").read_text())
    if inv["account"] != cat["account"]:
        raise RuntimeError("Removal catalog account mismatch")
    return inv["account"], {r[0]: r for r in selection.select(inv, cat)}


def records(root, selection, source_keys=None, scope=None):
    account, rows = scope if scope is not None else load_scope(root, selection)
    selected = (
        rows.values()
        if source_keys is None
        else (rows[k] for k in sorted(source_keys) if k in rows)
    )
    for row in selected:
        path = (
            root
            / ("work-video" if is_video(row) else "work-api")
            / row[0]
            / "state.json"
        )
        if path.exists():
            state = json.loads(path.read_text())
            if state.get("original_key") != row[0]:
                raise RuntimeError(
                    "Journal source identity differs from selected inventory item"
                )
            if state.get("original_ext") and at(state["original_ext"], 0, 0) != row[0]:
                raise RuntimeError(
                    "Original metadata identity differs from selected inventory item"
                )
            yield path, state, account


def canonical_export_proof(state):
    return (
        bool(state.get("original_key"))
        and state.get("original_export_verified") is True
        and state.get("original_export_method") == "pLFTfd"
        and at(state.get("original_info"), 0, 0) == state.get("original_key")
        and at(state.get("original_ext"), 0, 0) == state.get("original_key")
        and at(state.get("original_ext"), 0, 5) == state.get("original_bytes")
    )


def local_backup(state, *, require_cloud_size=True):
    parts = state.get("live_backup_manifest") or [
        {
            "path": state.get("source"),
            "sha256": state.get("original_sha256"),
            "bytes": state.get("original_backup_bytes", state.get("original_bytes")),
        }
    ]
    mismatch = sum(p.get("bytes") or 0 for p in parts) != state.get("original_bytes")
    if (
        not parts
        or mismatch
        and (
            state.get("live_backup_manifest")
            or require_cloud_size
            and not canonical_export_proof(state)
        )
    ):
        raise RuntimeError(
            "Complete original backup size must match before permanent deletion"
        )
    for part in parts:
        if not part.get("sha256"):
            raise RuntimeError(
                "No recorded original checksum; this legacy backup cannot be verified retrospectively"
            )
        path = Path(part["path"]) if part.get("path") else None
        if (
            not path
            or not path.is_file()
            or not part.get("sha256")
            or path.stat().st_size != part["bytes"]
            or digest(path) != part["sha256"]
        ):
            raise RuntimeError(
                "Original backup missing or changed; permanent deletion refused"
            )
    return parts


def pending(root, selection):
    return [
        s["original_key"]
        for _, s, _ in records(root, selection)
        if s.get("deletion_mode") == "permanent"
        and s.get("stage") == "trashed"
        and not s.get("original_deleted_permanently")
    ]


def verify_backups(root, selection):
    checked = 0
    total = 0
    failures = []
    representations = []
    for _, s, account in records(root, selection):
        if not s.get("source"):
            continue
        try:
            if s.get("account") != account:
                raise RuntimeError("Backup journal account mismatch")
            parts = local_backup(s, require_cloud_size=False)
            total += sum(p["bytes"] for p in parts)
            checked += 1
            if sum(p["bytes"] for p in parts) != s.get("original_bytes"):
                representations.append(
                    {
                        "key": s["original_key"],
                        "reported_cloud_bytes": s.get("original_bytes"),
                        "recorded_download_bytes": sum(p["bytes"] for p in parts),
                        "canonical_export_provenance_recorded": canonical_export_proof(
                            s
                        ),
                    }
                )
        except (OSError, RuntimeError) as error:
            failures.append({"key": s["original_key"], "error": str(error)})
    return {
        "complete": not failures,
        "checked_originals": checked,
        "backup_bytes": total,
        "failures": failures,
        "cloud_requests": 0,
        "cloud_size_mismatches": representations,
    }


def downgrade_to_trash(root, selection):
    """Explicit --delete-mode trash can safely cancel an unstarted purge."""
    for path, s, _ in records(root, selection):
        if s.get("deletion_mode") != "permanent" or s.get(
            "original_deleted_permanently"
        ):
            continue
        if s.get("permanent_delete_started"):
            raise RuntimeError(
                "An interrupted permanent deletion must be reconciled before changing its policy"
            )
        s["deletion_mode"] = "trash"
        save(path, s)


def create_plan(root, selection, source_keys=None, scope=None):
    items = []
    account = None
    for path, s, expected in records(root, selection, source_keys, scope):
        account = expected
        if (
            s.get("deletion_mode") != "permanent"
            or s.get("stage") != "trashed"
            or s.get("original_deleted_permanently")
        ):
            continue
        if s.get("account") != account:
            raise RuntimeError("Removal journal account mismatch")
        kind = "video" if path.parent.parent.name == "work-video" else "image"
        expected_sha = s.get("encoded_sha256" if kind == "video" else "avif_sha256")
        if not expected_sha or s.get("download_verified_sha256") != expected_sha:
            raise RuntimeError(
                "Verified replacement export required before permanent deletion"
            )
        if not s.get("replacement_key") or s["replacement_key"] == s["original_key"]:
            raise RuntimeError("Replacement must have a distinct identity")
        output = Path(s["output"])
        if not output.is_file() or digest(output) != expected_sha:
            raise RuntimeError(
                "Local encoded replacement changed; permanent deletion refused"
            )
        parts = local_backup(s)
        items.append(
            {
                "source_key": s["original_key"],
                "replacement_key": s["replacement_key"],
                "filename": s.get("filename"),
                "kind": kind,
                "journal": str(path),
                "replacement_sha256": expected_sha,
                "backup_files": parts,
            }
        )
    sources = {i["source_key"] for i in items}
    targets = {i["replacement_key"] for i in items}
    if sources & targets or len(sources) != len(items):
        raise RuntimeError("Unsafe removal identity overlap")
    body = {
        "account": account,
        "items": items,
        "source_bytes": sum(p["bytes"] for i in items for p in i["backup_files"]),
        "never_empties_entire_trash": True,
    }
    body["plan_sha256"] = hashlib.sha256(
        json.dumps(body, sort_keys=True).encode()
    ).hexdigest()
    save(root / "permanent-delete-plan.json", body)
    return body


def summary(plan):
    return {
        "pending_permanent_deletions": len(plan["items"]),
        "local_backup_bytes": plan["source_bytes"],
        "plan_sha256": plan["plan_sha256"],
        "unrelated_trash_untouched": True,
    }


def execute(
    root, selection, *, confirmed=False, api=None, source_keys=None, scope=None
):
    if confirmed is not True:
        raise ValueError("Explicit permanent-deletion confirmation is required")
    plan = create_plan(root, selection, source_keys, scope)
    if not plan["items"]:
        return 0
    api = api or API()
    if api.call({"op": "hello"})["account"] != plan["account"]:
        raise RuntimeError("Connected account differs from the removal plan")
    entries = plan["items"]
    states = {
        i["source_key"]: json.loads(Path(i["journal"]).read_text()) for i in entries
    }
    present = api.trash_items(states, exhaustive=True)
    # Validate all remote replacements before the first irreversible request.
    dedups = {}
    for start in range(0, len(entries), 20):
        group = entries[start : start + 20]
        calls = []
        for item in group:
            calls.extend(
                [
                    ["fDcn4b", [item["replacement_key"], 1, None, None, 1]],
                    ["VrseUb", [item["source_key"], None, None, None, None]],
                ]
            )
        values = api.batch(calls)
        for n, item in enumerate(group):
            ext, old = values[n * 2 : n * 2 + 2]
            s = states[item["source_key"]]
            er = s["original_ext"][0]
            expected_size = s[
                "encoded_bytes" if item["kind"] == "video" else "avif_bytes"
            ]
            if (
                at(ext, 0, 0) != item["replacement_key"]
                or at(ext, 0, 30, 2) != 2
                or at(ext, 0, 5) != expected_size
                or at(ext, 0, 3) != er[3]
                or at(ext, 0, 4) != er[4]
                or at(ext, 0, 2) != Path(s["output"]).name
            ):
                raise RuntimeError(
                    "Replacement identity/quality/size/date changed; permanent deletion refused"
                )
            if s.get("display_size") and list(at(ext, 0, default=[])[6:8]) != list(
                s["display_size"]
            ):
                raise RuntimeError(
                    "Replacement dimensions changed; permanent deletion refused"
                )
            if not album_ids(s["original_ext"]) <= album_ids(ext) or (
                er[1] and at(ext, 0, 1) != er[1]
            ):
                raise RuntimeError(
                    "Replacement metadata changed; permanent deletion refused"
                )
            if item["source_key"] not in present:
                if s.get("permanent_delete_started") and not old:
                    s["original_deleted_permanently"] = True
                    save(Path(item["journal"]), s)
                    continue
                raise RuntimeError("Source is not in Trash; permanent deletion refused")
            trash_row = present[item["source_key"]]
            key = at(trash_row, 3)
            if (
                at(trash_row, 0) != item["source_key"]
                or not key
                or key != at(s["original_ext"], 0, 11)
                or key == at(ext, 0, 11)
            ):
                raise RuntimeError("Source identity changed or matches replacement")
            dedups[item["source_key"]] = key
    count = 0
    for start in range(0, len(entries), 50):
        group = [i for i in entries[start : start + 50] if i["source_key"] in dedups]
        if not group:
            continue
        keys = [dedups[i["source_key"]] for i in group]
        if len(set(keys)) != len(keys):
            raise RuntimeError("Duplicate permanent-deletion keys refused")
        for item in group:
            s = states[item["source_key"]]
            local_backup(s)
            s.update(
                permanent_delete_started=True,
                permanent_delete_plan_sha256=plan["plan_sha256"],
            )
            save(Path(item["journal"]), s)
        api.purge(keys, confirmed=True)
        remaining = api.in_trash([i["source_key"] for i in group], exhaustive=True)
        after = api.batch(
            [["VrseUb", [i["source_key"], None, None, None, None]] for i in group]
        )
        for item, info in zip(group, after):
            if item["source_key"] in remaining or info:
                raise RuntimeError(
                    "Permanent deletion not yet confirmed; retain journal for reconciliation"
                )
            s = states[item["source_key"]]
            s["original_deleted_permanently"] = True
            save(Path(item["journal"]), s)
            count += 1
    return count


class IncrementalRemoval:
    """Serialize plans/deletions for concurrently running image/video cohorts."""

    def __init__(self, root, selection, *, confirmed=False):
        self.root = root
        self.selection = selection
        self.confirmed = confirmed
        self.lock = threading.Lock()
        self.scope = None

    def __call__(self, keys):
        if not self.confirmed or self.selection.delete_mode != "permanent":
            return
        # Never touch a different year/album or another batch's pending Trash.
        with self.lock:
            candidates = set()
            for key in keys:
                for folder in ("work-api", "work-video"):
                    path = self.root / folder / key / "state.json"
                    if not path.exists():
                        continue
                    state = json.loads(path.read_text())
                    if (
                        state.get("deletion_mode") == "permanent"
                        and state.get("stage") == "trashed"
                        and not state.get("original_deleted_permanently")
                    ):
                        candidates.add(key)
            if candidates:
                if self.scope is None:
                    self.scope = load_scope(self.root, self.selection)
                count = execute(
                    self.root,
                    self.selection,
                    confirmed=True,
                    source_keys=candidates,
                    scope=self.scope,
                )
                print(
                    json.dumps(
                        {"event": "originals_permanently_deleted", "count": count}
                    ),
                    flush=True,
                )
