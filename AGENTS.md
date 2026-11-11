# Contributor and agent instructions

## Project contract

This is a Python 3.15+ Google Photos optimizer with inline PEP 723 dependencies in `google-photos-cli.py`. Use `uv run --locked google-photos-cli.py …`; do not add a requirements file or a parallel dependency declaration. Native tools are libavif with AOM, ExifTool, FFmpeg with x265/AAC, and Chrome. Keep the support credit to ReAI at the top of README.md.

Cloud writes are opt-in through `--apply`; preview is the default. Respect a user's account, filters, and scope. Never empty Trash or permanently delete cloud items. Do not publish user media, account identifiers, credentials, Google signed URLs, browser profiles, inventories, catalogs, logs, or migration journals. The Git ignore file is a source allowlist: review every new allowlist entry and the complete staged content before pushing.

## Safety invariants

- Bind inventory, metadata, and journals to the connected account; reject mismatches.
- Keep downloaded originals locally. Commit output files and journals atomically.
- Record intent before remote commit and reconcile interruptions by content hash. Never blindly repeat an ambiguous upload commit.
- Preserve combined Live/Motion Photos unchanged; never split one cloud asset into two. Exclude Locked Folder.
- Require the replacement's expected filename, stored size, cloud capture timestamp/timezone, and byte-identical SHA-256 true-original export before trashing its source.
- Use `pLFTfd` original export. Other download endpoints can return compatibility JPEGs/transcoded video, including for canonical AVIF/MP4 uploads. Processing delays must cause a wait, not a weakened check.
- Preserve reported writable album memberships and descriptions; retain favorites/archive flags. Never alter album access lists.
- Videos must fully decode locally, retain duration/audio presence, expose cloud playback variants, and be smaller before source removal.
- Require Google's Original-quality flag before removing a video source. Storage saver can asynchronously replace an initially byte-identical HEVC upload with VP9 and different bytes/size. See `docs/STORAGE_SAVER.md`. Do not automatically change account settings. `videos --repair --apply` remuxes retained encoded streams, verifies the new Original-quality upload, removes the superseded candidate, and updates the original authoritative journal without changing its baseline size.
- Read back Trash after moving originals. A successful HTTP response is insufficient.
- Fresh audits must reconcile every expected original/replacement and distinguish a filtered selection from the whole library. Never claim complete library migration based on a sample or cached inventory alone.

## Architecture and performance

`google-photos-cli.py` is the entry point. `selection.py` filters the original catalog before mutations. `migration.py` and `video_migration.py` own account-bound journals. `converter.py` and `video_codec.py` invoke native encoders; Python is orchestration rather than the codec implementation. `photos_api.py` handles requests through a loopback broker (`api-bridge.py`) and a bounded browser worker (`api-bridge.js`). Authentication stays in Chrome; do not export Google cookies or CSRF tokens to Python.

Default images: AVIF quality 45, speed 6, 10-bit YUV420, four workers and three AOM threads each. Default video: HEVC Main10/hvc1 MP4, x265 medium CRF 30, at most 1080p/30fps, AAC 96 kbps stereo. Prefer batching and bounded concurrency over unbounded parallelism. Avoid per-photo UI operations. Account for temporary Google processing representations; do not infer readiness from elapsed wall-clock time alone. Use monotonic deadlines.

The broker must remain loopback-only, require its random private session key, check browser origins, and restrict RPCs/media destinations. Never weaken browser security to bootstrap it. Paste the self-authored bootstrap directly into an authorized signed-in Photos console, or use the managed `connect` path. Respect OS access restrictions and have the user unlock blocked applications normally.

The web protocol is undocumented. Fail closed on unexpected schema, host, account, hash, or metadata changes. Public API capabilities are insufficient for the full arbitrary-library workflow. Do not claim that every account or platform has been validated.

## Validation and publishing

Run `uv run --locked google-photos-cli.py self-test`. Tests must be offline and use synthetic fixtures. Add focused tests for selection, journal recovery, and deletion gates when those behaviors change. Never use real account data as public fixtures. Keep a small real-account smoke test separate from offline CI when explicitly authorized.

Inspect `git diff --cached`, `git ls-files`, and tracked content for secrets/media before publishing. Update README examples and safety limitations to match the actual implementation. Do not claim a full migration has finished unless the fresh audit passes.
