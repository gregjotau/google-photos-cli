# Contributor and agent instructions

## Project contract

This is a Python 3.15+ Google Photos optimizer with inline PEP 723 dependencies in `google-photos-cli.py`. Use `uv run --locked google-photos-cli.py …`; do not add a requirements file or a parallel dependency declaration. Native tools are libavif with AOM, ExifTool, FFmpeg with x265/AAC, and Chrome. Keep the support credit to ReAI at the top of README.md.

Cloud writes are opt-in through `--apply`; preview is the default. Respect a user's account, filters, and scope. Never empty account-wide Trash or delete unrelated items. Permanent removal is allowed only for explicit permanent-mode sources, after complete verified local backups and replacement checks, with --confirm-permanent-delete and any action-time confirmation required by the client. Never upgrade legacy or Trash-mode completed journals to permanent deletion. Do not publish user media, account identifiers, credentials, Google signed URLs, browser profiles, inventories, catalogs, logs, or migration journals. The Git ignore file is a source allowlist: review every new allowlist entry and the complete staged content before pushing.

## Safety invariants

- Bind inventory, metadata, and journals to the connected account; reject mismatches.
- Keep downloaded originals locally. Commit journals and backups atomically with file/directory sync. Never accept partial original downloads after an interruption.
- Record intent before remote commit and reconcile interruptions by content hash. Never blindly repeat an ambiguous upload commit. A committing journal must use upload(reconcile_only=True): if hash lookup has no match, stop and retry lookup later without uploading again.
- Default Live/Motion Photos to one AVIF still, backing up ALL original still/motion components locally before removal. --live-photos keep preserves the combined cloud original; never create two cloud assets from one. Exclude Locked Folder.
- Require the replacement's expected filename, stored size, cloud capture timestamp/timezone, and byte-identical SHA-256 true-original export before trashing its source.
- Use `pLFTfd` original export. Other download endpoints can return compatibility JPEGs/transcoded video, including for canonical AVIF/MP4 uploads. Processing delays must cause a wait, not a weakened check.
- Preserve reported writable album memberships and descriptions; retain favorites/archive flags. Never alter album access lists.
- Videos must fully decode locally, retain duration/audio presence against the ORIGINAL source, expose cloud playback variants, and be smaller before source removal. Recover an unjournaled output with validate_encoded; never use the encoded file itself as the duration reference.
- Require Google's Original-quality flag before removing any image or video source. Storage saver can asynchronously replace an initially byte-identical HEVC upload with VP9 and different bytes/size. See `docs/STORAGE_SAVER.md`. Do not automatically change account settings without user authorization; record and restore the previous preference when a user authorizes a temporary change. `videos --repair --apply` remuxes retained encoded streams, verifies the new Original-quality upload, removes the superseded candidate, and updates the original authoritative journal without changing its baseline size.
- Read back Trash after moving originals; permanent removal must match authoritative Trash media IDs and deletion keys against original metadata, then confirm absence. A successful HTTP response is insufficient.
- Use verify-backups for offline original checks and audit --verify-exports for a fresh re-download of selected replacements. Fresh audits must reconcile every expected original/replacement and distinguish a filtered selection from the whole library. Never claim complete library migration based on a sample or cached inventory alone. Use scan --append for new arrivals only after reconciling pending conversions; never overwrite the original snapshot or treat known replacements as new sources.

## Architecture and performance

`google-photos-cli.py` is the entry point. `selection.py` filters the original catalog before mutations. `migration.py` and `video_migration.py` own account-bound journals. `converter.py` and `video_codec.py` invoke native encoders; Python is orchestration rather than the codec implementation. `photos_api.py` handles requests through a loopback broker (`api-bridge.py`) and a bounded browser worker (`api-bridge.js`). Authentication stays in Chrome; do not export Google cookies or CSRF tokens to Python.

Default images: AVIF quality 45, speed 6, 10-bit YUV420, four workers and three AOM threads each. Default video: HEVC Main10/hvc1 MP4, x265 medium CRF 30, at most 1080p/30fps, AAC 96 kbps stereo. Prefer batching and bounded concurrency over unbounded parallelism. Bound uploads by verification/removal barriers: default combined cohort 10 assets, standalone video one at a time. With explicit permanent confirmation, remove eligible originals during each cohort, before admitting another. Never defer all cleanup until the whole library finishes. By default, stop admitting cohorts on verification, backup or deletion failure. The explicit `run --continue-on-item-error` mode may defer only its allowlisted item failures before source removal, recording incomplete journals and an account-bound diagnostic ledger. Account/journal identity failures, corrupt backups or outputs, stalled verification and Trash/permanent-deletion failures still stop admission. Deferral never marks an item complete or authorizes a repeated uncertain commit. Use --batch-size 1 for minimal temporary cloud space; do not promise zero temporary headroom or instant quota updates. Avoid per-photo UI operations. Account for temporary Google processing representations; do not infer readiness from elapsed wall-clock time alone. Use monotonic deadlines.

The broker must remain loopback-only, require its random private session key, check browser origins, and restrict RPCs/media destinations. Never weaken browser security to bootstrap it. Paste the self-authored bootstrap directly into an authorized signed-in Photos console, or use the managed `connect` path. Respect OS access restrictions and have the user unlock blocked applications normally.

The web protocol is undocumented. Fail closed on unexpected schema, host, account, hash, or metadata changes. Public API capabilities are insufficient for the full arbitrary-library workflow. Do not claim that every account or platform has been validated.

## Validation and publishing

Run `uv run --locked google-photos-cli.py self-test`. Tests must be offline and use synthetic fixtures. Add focused tests for selection, journal recovery, and deletion gates when those behaviors change. Never use real account data as public fixtures. Keep a small real-account smoke test separate from offline CI when explicitly authorized.

Inspect `git diff --cached`, `git ls-files`, and tracked content for secrets/media before publishing. Update README examples and safety limitations to match the actual implementation. Do not claim a full migration has finished unless the fresh audit passes.
