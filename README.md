Supported by **[ReAI](https://reai.no)**, Norway's most automated accounting system.

# Google Photos CLI

Batch-convert Google Photos images to AVIF and optimize videos as HEVC MP4, with resumable uploads, album transfer, capture-date checks, local original backups, and verified removal of originals from the active library.

**Python 3.15+, one `uv run` entry point, native encoders, no `requirements.txt`.** Python dependencies are declared inline in the script using PEP 723 and resolved by uv. You clone the repository because the entry point imports its companion modules.

This is an independent, experimental client using Google's undocumented web API. A complete real-library migration has passed a fresh, fully paginated audit, including corrected Original-quality video uploads. Google can change the protocol. Keep an independent backup and try a small batch first. Local original backups are mandatory. The default deletion policy is **permanent removal of verified originals**, with a separate explicit confirmation flag. Until confirmed, originals remain in recoverable Trash. No account-wide empty-Trash operation is performed. This has been validated on one account, not every account or platform.

[Setup](#install) · [Agent prompts](#copy-paste-prompts-for-a-coding-agent) · [Filters](#filters) · [Recovery and troubleshooting](docs/OPERATIONS.md)

## Install

On macOS:

```sh
brew install uv libavif exiftool ffmpeg
git clone https://github.com/gregjotau/google-photos-cli.git
cd google-photos-cli
export GOOGLE_PHOTOS_DATA_DIR="$HOME/google-photos-data"
uv run --locked google-photos-cli.py doctor
uv run --locked google-photos-cli.py self-test
```

Set the same `GOOGLE_PHOTOS_DATA_DIR` in **both terminals** (or pass `--data-dir ~/google-photos-data` before the subcommand every time). Keep this directory outside the checkout: it contains originals, journals, metadata and the Chrome profile. `--data-dir` overrides the environment variable; the legacy fallback is the checkout directory.

Install Google Chrome separately if it is not already installed. FFmpeg must include `libx265` and AAC; libavif must include the AOM encoder. `doctor` checks native encoder capabilities, Pillow AVIF decoding, HEIF support, Chrome discovery and available disk space, reports all detected problems as JSON, and makes no Google requests. On Linux, install equivalent native packages and Chrome; Linux support has not been validated end to end. Windows is currently unsupported because the process lock uses `fcntl`.

`self-test` never accesses Google; it also runs small synthetic codec checks when native tools are present. Node is optional for local browser-worker tests and is installed by CI.

uv selects Python 3.15 or newer and creates a cached dependency environment automatically. There is no manual virtualenv or pip setup. A script lockfile is included; use `uv run --locked google-photos-cli.py …` for locked dependency resolution.

## Connect and preview

In terminal 1:

```sh
uv run --locked google-photos-cli.py connect
```

Sign in to the intended Google account in the opened Chrome window. Allow the connection to the local device if Chrome asks. Keep that terminal and tab open. By default the profile is `DATA_DIR/chrome-profile`, so distinct data directories isolate accounts. The managed connection reattaches the worker after normal page navigation or refresh. This uses a separate persistent Chrome profile; it does not read your normal Chrome profile or export Google cookies to Python.

**Before optimizing images or videos, set Google Photos → Settings → Backup quality to Original quality.** This preserves the small files already compressed by this tool. Storage saver can asynchronously rewrite an uploaded HEVC file into a larger, different file even after an initial byte-identical verification. The client refuses to trash an image or video source unless Google reports Original quality for its replacement. Changing the setting affects your web upload preference; the CLI does not change it automatically.

See the [measured Storage saver issue and recovery procedure](docs/STORAGE_SAVER.md): one HEVC upload grew from 3.77 MB to a 10.43 MB VP9 original export. This is a reason to use Original quality for this optimizer's output, not a blanket recommendation against Storage saver for other media. Downloaded file size and billed storage quota are separate measurements.

In terminal 2, in the same repository:

```sh
export GOOGLE_PHOTOS_DATA_DIR="$HOME/google-photos-data"
# Read-only: snapshot the library and preview a small selection.
uv run --locked google-photos-cli.py plan --year current --limit 5

# Process those same five matching assets.
uv run --locked google-photos-cli.py run --year current --limit 5 --apply

# After inspecting the sample and authorizing permanent removal, process
# the current year with removal after each verified cohort.
uv run --locked google-photos-cli.py run --year current --apply --confirm-permanent-delete

# Or process the whole saved library and archive.
uv run --locked google-photos-cli.py run --apply --confirm-permanent-delete
```

`run`, `migrate`, and `videos` **preview by default**. Cloud uploads and moving originals to Trash require `--apply`. `--dry-run` always overrides `--apply`. A first preview can fetch the inventory and metadata, but never uploads or trashes media. With a complete cached snapshot, previews work offline.

Existing AVIF images are skipped before media download during conversion. Completed journals are also skipped on reruns. Audits can download untracked AVIF files to verify them. **Live/Motion Photos become one AVIF still by default**; cloud motion is intentionally removed, while the complete original still and motion components remain in checksum-verified local backups. Use `--live-photos keep` to preserve them unchanged in Google Photos. No separate cloud video is created. Locked Folder, unsaved shared items, and items already in Trash are outside the migration scope.

## Local backups and deletion

Keep the private data directory on a disk with enough space for **all originals plus encoded outputs**. Originals are always backed up locally; there is no no-backup option. Permanent deletion removes Google's recovery copy, so protect the local originals independently. Google Photos states that items in Trash do not count toward account storage: emptying them is a recovery/privacy choice, not additional measured quota savings.

The default `--delete-mode permanent` records intent for **new conversions**. `--apply` alone converts and verifies, moves sources to Trash, writes a private `permanent-delete-plan.json`, and exits with code 2 while permanent deletions are pending. Review that plan and the backups, then finish with the same filters:

```sh
uv run --locked google-photos-cli.py purge-originals --year 2023
uv run --locked google-photos-cli.py purge-originals --year 2023 --apply --confirm-permanent-delete

# Alternatively authorize the complete workflow in one invocation.
uv run --locked google-photos-cli.py run --year 2023 --apply --confirm-permanent-delete

# Keep recoverable Google Trash instead; local backups are still mandatory.
uv run --locked google-photos-cli.py run --year 2023 --delete-mode trash --apply
```

Permanent removal is irreversible in Google Photos. Agents must follow any action-time confirmation policy their client imposes. The tool never empties unrelated Trash. Legacy completed journals and explicit Trash-mode conversions are **not retroactively upgraded** to permanent deletion by a rerun. An explicit `--delete-mode trash` can cancel pending permanent intent before deletion starts. Interrupted permanent removal must first be reconciled with the original policy. The journal stage `trashed` marks completed source-removal processing; `original_deleted_permanently` distinguishes its final state.

A fresh audit reports `media_complete`, `deletion_complete`, and `complete`. Pending permanent removals prevent `complete`; intentionally retained Trash-mode and legacy sources do not. Original quality is required for the permanent-removal check for both images and videos.

## Incremental removal and temporary storage

Conversion is bounded by verification/removal barriers. A combined run admits at most **10 selected assets per cohort** by default, sharing that limit across images and videos. Each video is verified and its source removed before starting the next video. A confirmed permanent-mode run purges backed-up originals **during conversion**, before admitting another cohort; it does not wait for the whole library to finish. A processing stall, backup failure or removal failure prevents further cohorts from starting. The full-library audit still runs at the end.

```sh
# Smallest temporary cloud footprint: one replacement at a time.
uv run --locked google-photos-cli.py run --year 2023 --batch-size 1 --apply --confirm-permanent-delete

# Higher image throughput, still bounded and deleting as the job progresses.
uv run --locked google-photos-cli.py run --year 2023 --batch-size 10 --apply --confirm-permanent-delete
```

`--batch-size` accepts 1–50 on `run`/`migrate`; standalone `videos` always uses one video at a time. This bounds asset count, not bytes. There must still be room for one replacement (or the chosen cohort), and Google may take time to reflect quota changes. A completely full account may need space freed manually before a replacement can be uploaded safely. The tool never deletes an unverified original to create that space.

Smaller files do not guarantee a smaller billed quota. For example, Google says High/Express-quality backups made before June 1, 2021 can be quota-exempt, while new Original-quality uploads consume storage. Replacing an exempt original can therefore increase billed usage even when the replacement is smaller. This depends on backup eligibility, not just the capture-year filter. Check account storage during the smoke test. See [Google's storage policy](https://support.google.com/photos/answer/10100180?hl=en).

Without `--confirm-permanent-delete`, the same bounded pipeline moves verified sources to recoverable Trash as it goes and leaves permanent removal pending. Google states that Trash does not count toward storage; permanent removal is a recovery/privacy choice. For an agent requiring action-time approval, convert and confirm small scopes in repeated cycles rather than postponing every deletion until the end of the library. Keep the exact year/album/date filters on every cycle.

## Copy-paste prompts for a coding agent

These prompts authorize work but cannot bypass login, MFA, operating-system restrictions, or an agent client's irreversible-action confirmation. macOS is the tested platform; this remains an experimental client. Use a small smoke test before a larger migration.

### Whole library

```text
Use https://github.com/gregjotau/google-photos-cli to optimize my saved Google Photos library and archive end to end on macOS. Clone to ~/google-photos-cli and use a separate private --data-dir ~/google-photos-data on every command; reuse existing journals for this account. Read README.md and AGENTS.md. Keep all private media, metadata, credentials and journals out of Git. Verify the intended account before any writes.

Install missing tools from official sources: Git, uv, Chrome, Python >=3.15 via uv, libavif with AOM, ExifTool, and FFmpeg with libx265/AAC (Homebrew packages on macOS). Use uv run --locked google-photos-cli.py --data-dir ~/google-photos-data COMMAND throughout, without requirements.txt. Run doctor and self-test. Linux is unvalidated and Windows unsupported; stop and explain if the platform cannot support this workflow. Let me handle administrator passwords, sign-in, MFA, CAPTCHAs and OS restrictions directly; never request secrets in chat.

Connect using connect and its separate Chrome profile, or the documented broker/bootstrap workflow in my signed-in Photos tab. Keep the bridge alive; never export Google cookies. Record my Backup quality setting, temporarily use Original quality for every optimized upload, and restore my previous setting after successful verification. I authorize those temporary setting changes and the scoped conversion.

Preview with plan --limit 5, then run --limit 5 --apply for a real smoke test. Use the default Live Photo policy: one AVIF still, with complete still+motion originals backed up locally, no separate cloud motion video. Keep all originals locally; choose a data disk with enough capacity. Use default permanent-deletion intent, initially WITHOUT --confirm-permanent-delete. Exit 2 with a verified conversion and pending permanent plan is expected. Run verify-backups --limit 5 and audit --limit 5 --verify-exports; check exported hashes, dates, albums and all original components. Review the small smoke-test removal plan and obtain required action-time approval, then purge-originals --limit 5 --apply --confirm-permanent-delete. For the full library, run --apply --confirm-permanent-delete --batch-size 10 without --limit once that scope is authorized. This verifies and permanently deletes sources as the migration progresses; do not upload the entire library before cleanup. If your client requires approval for each concrete batch, repeat small conversion-and-confirm cycles instead of deferring all cleanup, keeping the original scope. Never overwrite an active inventory with scan. Use bounded concurrency and native encoders, not per-photo UI clicking. Existing AVIF and completed journal items must be skipped before media download during conversion. Videos that encode larger may remain unchanged.

Keep Locked Folder, unsaved shared items and unrelated existing Trash excluded. Do not empty all Trash or retroactively purge legacy/Trash-mode journals. If Storage saver rewrote a video, use videos --repair --apply under Original quality, with the same scope. Review the exact permanent-delete-plan.json and verified replacements/backups. Obtain any action-time confirmation required by your client, then execute purge-originals --apply --confirm-permanent-delete for only this planned scope. I understand permanent deletion removes Google recovery and local backups must remain protected.

Run verify-backups and finish with audit --verify-exports. Persist until this fresh full audit has media_complete, deletion_complete and complete true, or a real login/OS/confirmation blocker needs me. Reconnect and resume rather than discarding journals. If new arrivals prevent a complete audit, first finish/reconcile pending conversions, then use scan --append to extend the existing inventory safely; preview and process arrivals with the same authorized scope. Never replace or discard the original inventory or journals. Restore my previous upload setting, stop the bridge and leave backups intact. Summarize active counts, exclusions, source-removal status, local backup location and separate images/videos before/after/savings tables. Measure file bytes separately from Google quota; do not claim every account/platform is validated.
```

### Only capture year 2023

```text
Use https://github.com/gregjotau/google-photos-cli to optimize ONLY photos and videos captured in 2023 on my macOS Google Photos account. Clone to ~/google-photos-cli, read README.md and AGENTS.md, and use a separate private --data-dir ~/google-photos-data for every command. Reuse account-bound journals and verify my intended account. Keep media, credentials, metadata and journals private and outside Git.

Install missing Git, uv, Chrome, Python >=3.15 via uv, libavif/AOM, ExifTool and FFmpeg with libx265/AAC from official sources (Homebrew on macOS). Run uv run --locked google-photos-cli.py --data-dir ~/google-photos-data doctor and self-test. Linux is unvalidated; Windows unsupported. Let me handle sign-in, MFA, administrator passwords, CAPTCHAs and OS restrictions directly without sharing secrets. Connect through the separate Chrome profile or documented broker/bootstrap, keep the bridge alive and never export Google cookies.

On EVERY plan, run, migrate, videos, repair, purge-originals, verify-backups and audit invocation append --year 2023. (`--repair` is a videos flag, not a separate command.) Never remove this filter while retrying or expand to other years. Other years, Locked Folder, unsaved shared items and unrelated Trash must remain untouched. Capture timestamp plus recorded offset decides the year.

Record my Backup quality, temporarily switch to Original quality for optimized uploads (I authorize this), and restore it after verification. Preview plan --year 2023 --limit 5; smoke-test run --year 2023 --limit 5 --apply; run verify-backups --year 2023 --limit 5 and audit --year 2023 --limit 5 --verify-exports; inspect checksums, dates and albums. Use default Live Photo conversion to one AVIF still, preserving complete original still+motion locally, and sensible default AVIF/HEVC settings. Back up every original locally on a disk with enough space. Existing AVIF/completed conversions must skip media download; keep videos when a candidate is larger.

Use permanent-deletion intent but initially omit --confirm-permanent-delete. Exit 2 for pending permanent deletions after verified conversion is expected. First review and confirm the smoke-test plan, then use purge-originals --year 2023 --limit 5 --apply --confirm-permanent-delete. Once authorized, continue run --year 2023 --apply --confirm-permanent-delete --batch-size 10 without the sample limit, retaining journals and the original inventory. Sources must be verified and permanently deleted as conversion progresses; do not wait for all of 2023 to be uploaded before cleanup. If your client requires per-batch action-time approval, repeat small conversion-and-confirm cycles with --year 2023 throughout. Use --batch-size 1 for minimal temporary space. If required, videos --year 2023 --repair --apply repairs Storage saver rewrites under Original quality. Review the exact scoped permanent-delete-plan.json and all backup/replacement proofs, obtain any action-time confirmation your client requires, then execute purge-originals --year 2023 --apply --confirm-permanent-delete. Permanent deletion removes Google's recovery copy; keep local backups protected. Never empty account-wide Trash or upgrade legacy/Trash-mode records to permanent deletion.

Run verify-backups --year 2023, then finish with audit --year 2023 --verify-exports reporting media_complete, deletion_complete and complete true. Reconnect and resume after interruptions; never erase journals or overwrite an active inventory with scan. Persist until complete unless login/OS/confirmation needs me. Restore my previous upload setting and stop the bridge. Report only 2023 results, with separate image/video savings tables, kept videos, exclusions, backup location and deletion status; do not claim the rest of my library is converted or equate file savings with billed quota.
```

### Resume an interrupted migration

```text
Resume my existing Google Photos CLI migration in ~/google-photos-cli using private --data-dir ~/google-photos-data. Read README.md, AGENTS.md and docs/OPERATIONS.md first. Do not discard journals, replace the inventory, create a new data directory, or broaden the original authorized filters. Inspect local status/report and the saved journals to establish the exact account, scope, Live Photo policy and deletion policy. If scope cannot be determined, ask before cloud writes.

Use uv run --locked google-photos-cli.py with Python >=3.15; install missing official uv, Chrome, libavif/AOM, ExifTool and FFmpeg x265/AAC tools if needed. Run doctor and self-test. Reconnect the same intended account/profile using connect or the documented broker/bootstrap; never export cookies or bypass login/MFA/OS restrictions. I authorize temporarily selecting Original quality for remaining optimized uploads; record the previous setting and restore it only after all verification.

Run verify-backups with the original scope. A committing journal must only reconcile by content hash; if no match is visible, stop and retry lookup later without another commit. Retain every original backup and output. Resume the original run/repair command with identical filters and policy. After required approval, use --confirm-permanent-delete for permanent-mode runs so cleanup happens during conversion; use --batch-size 1 on run for minimal temporary cloud space. Do not accumulate the entire library before source cleanup. Do not retroactively purge completed legacy/Trash-mode sources. Review the exact pending permanent-delete plan and obtain any required action-time approval before purge-originals --apply --confirm-permanent-delete, with the same scope. Never empty unrelated Trash. Only after pending conversions/repairs are resolved, scan --append can safely add newly arrived originals if they are within my authorized task.

Finish with scoped verify-backups and audit --verify-exports. Report media_complete, deletion_complete and complete honestly, remaining blockers/exclusions, backup location and image/video savings separately. A preview or successful upload alone is not completion. Stop the bridge when finished. Do not publish media, bootstrap keys, account identifiers or journals.
```

### Preview 2023 without changing media

```text
Assess ONLY capture year 2023 using https://github.com/gregjotau/google-photos-cli on macOS. Clone to ~/google-photos-cli, read README.md and AGENTS.md, and keep all private data in --data-dir ~/google-photos-data. Reuse existing account-bound state without overwriting an active migration snapshot. Install missing official Git, uv, Chrome, Python >=3.15 via uv, libavif/AOM, ExifTool and FFmpeg with x265/AAC; run doctor and self-test using uv run --locked google-photos-cli.py. Windows is unsupported and Linux unvalidated.

Connect through the documented separate Chrome profile or broker/bootstrap. Verify the intended account and let me handle login, MFA or OS restrictions without sharing secrets. Run plan --year 2023 with the private data directory. Keep --year 2023 on every scoped command. This is READ-ONLY: no --apply, no --confirm-permanent-delete, no uploads, deletions, settings changes or Google test media. Do not empty Trash or access Locked Folder. Never export cookies or publish private state.

Summarize matching images/videos, original byte totals, existing AVIF skips, Live Photo still-conversion candidates, exclusions and required disk/tools. Explain default local backups, permanent-deletion intent plus explicit confirmation, --delete-mode trash recovery option and --live-photos keep option. Explain Original quality is needed before actual optimized uploads, but do not change it. Savings cannot be predicted from source bytes alone. Stop the bridge after preview and provide a proposed 2023 smoke-test command without executing it.
```

## Filters

Filters are available on `plan`, `run`, `migrate`, `videos`, `purge-originals`, `verify-backups`, and `audit`. Different filter types are combined with AND. Repeated years or album IDs match any of the supplied values.

| Option | Meaning |
| --- | --- |
| `--year 2025` | Match the capture year; repeat for multiple years |
| `--year current` | Use the local computer's current calendar year |
| `--from-date 2025-01-01` | Inclusive capture-date lower bound |
| `--to-date 2025-12-31` | Inclusive capture-date upper bound |
| `--media images` / `videos` / `all` | Select the media type; `run` defaults to both |
| `--album ALBUM_ID` | Only members of an album; repeat to match any listed album |
| `--filename '*.JPG'` | Case-insensitive glob over the original filename |
| `--min-size-mb 5` | Minimum original size in decimal megabytes |
| `--exclude-favorites` | Leave favorites alone |
| `--exclude-archived` | Leave archived assets alone |
| `--limit 10` | First 10 matching assets in the original snapshot's order |

Dates use the capture timestamp plus its recorded UTC offset, rather than the upload date or your current timezone. A missing offset is treated as UTC. Quote filename globs to stop the shell expanding them. Album IDs are the identifiers in Google Photos album URLs, for example the part after `/album/`.

```sh
# Two years of images, excluding favorites.
uv run --locked google-photos-cli.py run --media images --year 2024 --year 2025 --exclude-favorites --apply

# Large videos captured in a date range.
uv run --locked google-photos-cli.py run --media videos --from-date 2024-01-01 --to-date 2025-12-31 --min-size-mb 10 --apply

# Preview only JPEGs from one album.
uv run --locked google-photos-cli.py plan --album ALBUM_ID --filename '*.jpg'

# Explicit dry run, even if --apply was also supplied.
uv run --locked google-photos-cli.py run --year current --apply --dry-run
```

The limit is stable across resumes and includes matching assets already handled or kept unchanged. Repeating `--limit 5` processes the same first five, rather than advancing to another five. Remove or increase it to expand the selection. Reuse the same numeric year for long-running jobs spanning New Year; `current` is resolved each invocation.

## Encoding defaults and speed

| Media | Defaults |
| --- | --- |
| Images | libavif/libaom AVIF; quality 45, speed 6, 10-bit YUV420; orientation normalized into pixels |
| Videos | HEVC Main10 MP4, `hvc1`; x265 medium, CRF 30; up to 1080p in landscape or portrait, up to 30 fps; AAC 96 kbps stereo; fast-start |
| Live/Motion Photos | One AVIF still; complete original still/motion backed up locally; `--live-photos keep` opts out |
| Source deletion | Permanent intent; requires `--confirm-permanent-delete`; `--delete-mode trash` keeps Google recovery |
| Concurrency | Four image workers, three AOM threads per image, one video encoder using four x265 pool threads |

JPEG, PNG, WebP and other Pillow-supported single-frame images are handled; `pillow-heif` adds native HEIC/HEIF decoding through libheif. HEIF container transforms are handled by that decoder, then remaining EXIF orientation is normalized. RAW files, animations and unsupported image modes stop that item with its source retained. Multi-image HEIF is not automatically flattened.

These are lossy, storage-oriented settings. Resolution is retained for still images. Already smaller video sources are kept when the candidate encode would be larger. AVIF can occasionally be larger than a small JPEG; still-image conversion prioritizes obtaining AVIF. Auxiliary depth/MPO data is retained in the local original backup, not encoded into the primary AVIF. Animated still formats are rejected rather than silently flattened. The image working pipeline is 8-bit RGB before 10-bit AVIF encoding; this does **not** preserve HDR/high-bit-depth originals. HDR, Dolby Vision, gain maps and specialized metadata are not guaranteed to survive transcoding. Keep originals if these matter.

```sh
# Trade more CPU and memory for more parallel image work.
uv run --locked google-photos-cli.py run --workers 6 --threads 2 --apply

# Higher image quality, slower encoding.
uv run --locked google-photos-cli.py run --quality 60 --speed 4 --apply

# Local-only conversion.
uv run --locked google-photos-cli.py convert input.jpg output.avif --quality 45 --speed 6
```

Python coordinates the job; AOM and x265 perform compression in native code. A cached, account-bound selection keeps incremental cleanup from re-reading the whole catalog for each small cohort. The client batches inventory metadata, replacement checks, original-export verification, album additions, and trash operations. Catalogs checkpoint every 1,000 new metadata records or 60 seconds instead of rewriting the growing JSON file after each 40-item batch. Video original exports stream through temporary files and are hashed incrementally, so RAM usage does not grow with the entire video size. Journals and original backup files are atomically replaced and synced to disk. It uses 8 MiB upload chunks, streaming fingerprints, bounded concurrency, and hash lookup to reconcile interrupted uploads. Read-only RPCs retry bounded throttling/server errors with backoff; writes are never blindly retried. Queued tasks expire, and the worker rejects expired tasks before sending a Google request. A request already in flight may still complete after a timeout: reconnect and reconcile its journal. Rewriting this coordinator in Go would not speed up the native encoders or Google's processing queue; the useful tuning controls are worker/thread counts and encoding speed/quality. No cross-language benchmark is claimed.

## Verification, metadata, and recovery

For every replacement, the workflow:

1. Saves the original and its cloud metadata locally.
2. Encodes and decodes the output locally; videos also get duration and audio-presence checks.
3. Journals upload intent, uploads, and persists the new media ID.
4. Downloads Google's true-original export and requires a byte-identical SHA-256 match with the uploaded output. A compatibility JPEG or transcoded MP4 is insufficient.
5. Checks stored size and capture date/timezone, transfers reported album membership and description, and applies favorites/archive state. Video processing must expose playable variants.
6. Moves the old item to Trash and reads Trash back to confirm it. Local backups remain.
7. With permanent-mode intent and explicit confirmation, rechecks local backup hashes and remote replacement identity, Original quality, size, dates and albums, permanently removes only the selected source IDs, and verifies their absence from Trash. Local backups remain the recovery copy of the originals.

Image EXIF, XMP, and ICC metadata is copied where supported, with cloud capture dates and available GPS reconciled into the image. Cloud capture dates and timezone offsets are checked independently. Album transfer is limited to memberships visible in the source's metadata and your write permissions. Replacement uploads have new identities: original links, comments/likes, face associations, album ordering, and all specialized metadata cannot be promised. Shared-album replacement changes content visible to that album's existing audience, without altering its access list.

Google can take time to make a new upload available as its true original. The job waits and verifies again; it retains the old item when a check fails. Never lower the hash-verification requirement to force a migration through. An ambiguous `committing` journal is lookup-only on resume: absence of a hash match causes a stop instead of a second upload commit. See [recovery and troubleshooting](docs/OPERATIONS.md).

```sh
uv run --locked google-photos-cli.py status
uv run --locked google-photos-cli.py report
uv run --locked google-photos-cli.py audit --year 2025

# Offline: re-hash complete original backups, with no Google connection.
uv run --locked google-photos-cli.py verify-backups --year 2025

# Stronger online audit: re-download and hash all selected replacement exports.
uv run --locked google-photos-cli.py audit --year 2025 --verify-exports
```

`report` separates image and video savings for confirmed replacements in the journals. It excludes unchanged assets and measures file bytes, not the account's eventual quota adjustment. A filtered audit checks only that original selection and its replacements. An unfiltered audit checks the whole accessible saved library and archive; it can fail if new photos have arrived outside the original snapshot. Routine audits refresh metadata and compare it with recorded byte-identical export proofs; they do not re-download every previously verified replacement. `--verify-exports` also re-downloads and hashes all selected replacements. `verify-backups` checks journaled original files against their recorded download sizes and hashes offline, including every Live Photo component; it reports Google upload/export size differences separately. Legacy missing checksums remain unverifiable rather than being silently filled in. It does not download skipped, already-AVIF originals or provide an independent backup of the whole account.

Resume with the same data directory and command after reconnecting Chrome. Journals prevent successful replacements from being uploaded again and reconcile ambiguous commits using Google's content-hash lookup. Do not erase journals or close the bridge tab during a run. An unqualified `scan` refuses to overwrite a snapshot once journals exist. For new arrivals **after resolving pending conversions**, run `scan --append`, then `plan`/`run` with your intended filters. Append retains the historical original IDs and excludes known replacements and superseded videos, so it does not treat them as new sources. Keep the same directory across recurring runs; a fresh directory loses video optimization history. On macOS, `caffeinate -i uv run --locked google-photos-cli.py run --apply` keeps the Mac awake; it does not override Screen Time. Unlock blocked Chrome normally before resuming.

## Separate accounts and existing Chrome sessions

Use one connected account at a time and a distinct data directory/profile for each:

```sh
uv run --locked google-photos-cli.py --data-dir ~/photos-account-two connect --profile ~/photos-account-two/chrome
uv run --locked google-photos-cli.py --data-dir ~/photos-account-two plan --year current
uv run --locked google-photos-cli.py --data-dir ~/photos-account-two run --year current --apply
```

For an older installation, `connect --profile PATH` can explicitly reuse its prior profile (the old default was `.chrome-profile` inside the checkout). Never open one profile in two Chrome processes.

The reported account must match the snapshot and journals. The loopback broker is shared by this installation, so disconnect the previous account before switching. Keep the profile and data directory private; they contain your session, media, and metadata.

For an already signed-in Chrome tab, run `broker`, then `bootstrap` in another terminal, and paste the generated **local** `bootstrap.js` into that Photos tab's DevTools console once. It contains this repository's self-authored worker and a local broker key. Do not use an `eval(fetch(...))` shortcut; browser Trusted Types policies may reject it. No UI selection is used for per-photo operations.

The broker listens only on `127.0.0.1:8765`, checks a random session key and browser origin, and restricts the browser worker's RPCs and media destinations. Google authentication stays in Chrome. Python receives metadata, media, and short-lived Google original-export links; it does not receive Google cookies or CSRF tokens. Runtime files and media are ignored by Git using an explicit source-file allowlist.

## Development

```sh
uv run --locked google-photos-cli.py self-test
uv lock --script google-photos-cli.py
```

Offline tests cover filter boundaries, preview behavior, backup corruption, account binding, ambiguous commits, append idempotency, broker deadlines and read-only retry behavior. Synthetic codec tests exercise JPEG/HEIC orientation, dates/GPS, video audio presence and frame rate, and stream-preserving remuxing. They run locally when native tools are installed; CI also installs native codecs in a separate Linux job. No CI job accesses Google credentials or real media. Protocol and codec references:

- [libavif](https://github.com/AOMediaCodec/libavif)
- [pillow-heif](https://pillow-heif.readthedocs.io/en/stable/) and its [HEIF orientation notes](https://pillow-heif.readthedocs.io/en/stable/workaround-orientation.html)
- [FFmpeg codecs](https://ffmpeg.org/ffmpeg-codecs.html)
- [Google Photos public API](https://developers.google.com/photos/library/reference/rest) — not sufficient for migrating an arbitrary existing library or trashing its originals
- [Google Photos Web Client](https://github.com/xob0t/google_photos_web_client), [Google Photos Toolkit](https://github.com/xob0t/Google-Photos-Toolkit), and [Google Photos MCP](https://github.com/benjamin-di/google-photos-mcp) — protocol research references; this client does not install or execute their source

MIT licensed. Google Photos is a Google trademark; this project is not affiliated with Google.
