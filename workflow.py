"""Bound uploads by verification/removal barriers rather than an unbounded queue."""

from concurrent.futures import ThreadPoolExecutor
import time


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
