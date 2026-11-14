"""Bound uploads by verification/removal barriers rather than an unbounded queue."""

from concurrent.futures import ThreadPoolExecutor
import time
from pathlib import Path
import hashlib
import json
import threading


def batches(rows, size):
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


def run_batches(
    rows,
    *,
    task,
    drain,
    errors,
    waiting,
    workers,
    batch_size,
    after_batch,
    stop_event,
    timeout,
    pause,
):
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for group in batches(rows, batch_size):
                if stop_event.is_set():
                    break
                # Submit only this batch. Google processing must not let uploads
                # accumulate across the library while verification falls behind.
                for future in [pool.submit(task, row) for row in group]:
                    future.result()
                end = time.monotonic() + timeout
                drain()
                delay = pause
                while (
                    waiting()
                    and not errors
                    and not stop_event.is_set()
                    and time.monotonic() < end
                ):
                    time.sleep(min(delay, max(0, end - time.monotonic())))
                    drain()
                    delay = min(20, delay * 1.5)
                if errors or waiting():
                    stop_event.set()
                    break
                if after_batch:
                    after_batch([row[0] for row in group])
    except BaseException:
        stop_event.set()
        raise


# In-process workers share one atomic, account-bound diagnostic ledger.
_failure_lock = threading.RLock()


def record_item_failure(worker, root, state, error, event, *, key, folder):
    """Defer only known failures before source removal; never certify completion."""
    from converter import atomic_json, digest
    from removal import local_backup

    if state.get("original_key") != key or state.get("account") != worker.account:
        raise RuntimeError("Failed-item journal identity/account mismatch")
    worker.write(state, error=str(error))
    message, stage = str(error), state.get("stage")
    eligible = False
    backup_required = False
    if getattr(worker, "continue_on_item_error", False) and not state.get("permanent_delete_started"):
        if message == "Original source is unavailable":
            eligible = not stage and not any(state.get(k) for k in ("source", "original_info", "replacement_key"))
        elif message in ("Capture date repair did not verify", "Video capture date/zone mismatch"):
            eligible = stage == "uploaded" and bool(state.get("replacement_key"))
            backup_required = True
        elif message == "Commit outcome unknown; reconcile using hash" or message.startswith("Previous upload commit is ambiguous and its hash is not visible yet."):
            eligible = stage == "committing" and not state.get("replacement_key")
            backup_required = True
        elif message in ("TypeError: Failed to fetch", "timed out", "Error: Request deadline exceeded; reconcile writes before retrying", "HTTP Error 500: Internal Server Error", "HTTP Error 502: Bad Gateway", "HTTP Error 503: Service Unavailable", "HTTP Error 504: Gateway Timeout"):
            eligible = stage in (None, "downloaded", "converted") and not state.get("replacement_key")
            backup_required = bool(state.get("original_sha256") or stage)
            # A partial or unjournaled file needs inspection, not silent deferral.
            if state.get("source") and Path(state["source"]).exists():
                backup_required = True
    if eligible:
        if backup_required:
            local_backup(state)
            if stage in ("committing", "uploaded"):
                expected = state.get("avif_sha256") or state.get("encoded_sha256")
                if not expected or digest(Path(state["output"])) != expected:
                    raise RuntimeError("Deferred output checksum mismatch; inspection required")
        root = Path(root)
        if not hasattr(worker, "_failure_sources"):
            inventory = getattr(worker, "inventory", None) or getattr(worker, "inv", None)
            if inventory is None:
                inventory = json.loads((root / "inventory-api.json").read_text())
            if inventory["account"] != worker.account:
                raise RuntimeError("Deferred inventory account mismatch")
            worker._failure_sources = frozenset(r[0] for r in inventory["items"])
        if key not in worker._failure_sources:
            raise RuntimeError("Deferred source differs from bound inventory")
        journal = root / folder / key / "state.json"
        if json.loads(journal.read_text()) != state:
            raise RuntimeError("Deferred journal changed during inspection")
        with _failure_lock:
            path = root / "deferred-items.json"
            ledger = json.loads(path.read_text()) if path.exists() else {"account": worker.account, "items": {}}
            if ledger.get("account") != worker.account or not isinstance(ledger.get("items"), dict):
                raise RuntimeError("Deferred ledger account/schema mismatch")
            ledger["items"][key] = dict(reason=message, stage=stage, recorded_at=time.time(),
                                        journal_sha256=hashlib.sha256(journal.read_bytes()).hexdigest(),
                                        backup_verified=backup_required, source_removal_attempted=False)
            atomic_json(path, ledger)
        worker.deferred.append(key)
        worker.log("item_deferred", key=key, error=message, stage=stage,
                   backup_verified=backup_required, selection_unchanged=True)
        return True
    worker.errors.append(key)
    worker.log(event, key=key, error=message)
    return False
