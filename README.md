Supported by **[ReAI](https://reai.no)**, Norway's most automated accounting system.

# Google Photos CLI

Batch-convert Google Photos images to AVIF and optimize videos as HEVC MP4, with resumable uploads, album transfer, capture-date checks, local original backups, and verified removal of originals from the active library.

**Python 3.15+, one `uv run` entry point, native encoders, no `requirements.txt`.** Python dependencies are declared inline in the script using PEP 723 and resolved by uv. You clone the repository because the entry point imports its companion modules.

This is an independent, experimental client using Google's undocumented web API. A complete real-library migration has passed a fresh, fully paginated audit, including corrected Original-quality video uploads. Google can change the protocol. Keep an independent backup and try a small batch first. Local original backups are mandatory. The default deletion policy is **permanent removal of verified originals**, with a separate explicit confirmation flag. Until confirmed, originals remain in recoverable Trash. No account-wide empty-Trash operation is performed. This has been validated on one account, not every account or platform.

## Install

On macOS:

```sh
brew install uv libavif exiftool ffmpeg
git clone https://github.com/gregjotau/google-photos-cli.git
cd google-photos-cli
uv run google-photos-cli.py doctor
```

Install Google Chrome separately if it is not already installed. FFmpeg must include `libx265` and AAC; libavif must include the AOM encoder. `doctor` checks installed tool versions. On Linux, install equivalent native packages and Chrome; Linux support has not been validated end to end. Windows is currently unsupported because the process lock uses `fcntl`.

uv selects Python 3.15 or newer and creates a cached dependency environment automatically. There is no manual virtualenv or pip setup. A script lockfile is included; use `uv run --locked google-photos-cli.py …` for locked dependency resolution.

## Connect and preview

In terminal 1:

```sh
uv run google-photos-cli.py connect
```

Sign in to the intended Google account in the opened Chrome window. Allow the connection to the local device if Chrome asks. Keep that terminal and tab open. This uses a separate persistent Chrome profile; it does not read your normal Chrome profile or export Google cookies to Python.

**Before optimizing images or videos, set Google Photos → Settings → Backup quality to Original quality.** This preserves the small files already compressed by this tool. Storage saver can asynchronously rewrite an uploaded HEVC file into a larger, different file even after an initial byte-identical verification. The client refuses to trash a video source unless Google reports Original quality for its replacement. Changing the setting affects your web upload preference; the CLI does not change it automatically.

See the [measured Storage saver issue and recovery procedure](docs/STORAGE_SAVER.md): one HEVC upload grew from 3.77 MB to a 10.43 MB VP9 original export. This is a reason to use Original quality for this optimizer's output, not a blanket recommendation against Storage saver for other media. Downloaded file size and billed storage quota are separate measurements.

In terminal 2, in the same repository:

```sh
# Read-only: snapshot the library and preview a small selection.
uv run google-photos-cli.py plan --year current --limit 5

# Process those same five matching assets.
uv run google-photos-cli.py run --year current --limit 5 --apply

# Process the current year, resuming previous work.
uv run google-photos-cli.py run --year current --apply

# Or process the whole saved library and archive.
uv run google-photos-cli.py run --apply
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

## Copy-paste prompts for a coding agent

These prompts authorize work but cannot bypass login, MFA, operating-system restrictions, or an agent client's irreversible-action confirmation. macOS is the tested platform; this remains an experimental client. Use a small smoke test before a larger migration.

### Whole library

```text
Use https://github.com/gregjotau/google-photos-cli to optimize my saved Google Photos library and archive end to end on macOS. Clone to ~/google-photos-cli and use a separate private --data-dir ~/google-photos-data on every command; reuse existing journals for this account. Read README.md and AGENTS.md. Keep all private media, metadata, credentials and journals out of Git. Verify the intended account before any writes.

Install missing tools from official sources: Git, uv, Chrome, Python >=3.15 via uv, libavif with AOM, ExifTool, and FFmpeg with libx265/AAC (Homebrew packages on macOS). Use uv run --locked google-photos-cli.py --data-dir ~/google-photos-data COMMAND throughout, without requirements.txt. Run doctor and self-test. Linux is unvalidated and Windows unsupported; stop and explain if the platform cannot support this workflow. Let me handle administrator passwords, sign-in, MFA, CAPTCHAs and OS restrictions directly; never request secrets in chat.

Connect using connect and its separate Chrome profile, or the documented broker/bootstrap workflow in my signed-in Photos tab. Keep the bridge alive; never export Google cookies. Record my Backup quality setting, temporarily use Original quality for every optimized upload, and restore my previous setting after successful verification. I authorize those temporary setting changes and the scoped conversion.

Preview with plan --limit 5, then run --limit 5 --apply for a real smoke test. Use the default Live Photo policy: one AVIF still, with complete still+motion originals backed up locally, no separate cloud motion video. Keep all originals locally; choose a data disk with enough capacity. Use default permanent-deletion intent, initially WITHOUT --confirm-permanent-delete. Exit 2 with a verified conversion and pending permanent plan is expected. Check exported hashes, dates, albums and full local backup checksums. Then run --apply without --limit for the whole library, resuming journals. Never overwrite an active inventory with scan. Use bounded concurrency and native encoders, not per-photo UI clicking. Existing AVIF and completed journal items must be skipped before media download during conversion. Videos that encode larger may remain unchanged.

Keep Locked Folder, unsaved shared items and unrelated existing Trash excluded. Do not empty all Trash or retroactively purge legacy/Trash-mode journals. If Storage saver rewrote a video, use videos --repair --apply under Original quality, with the same scope. Review the exact permanent-delete-plan.json and verified replacements/backups. Obtain any action-time confirmation required by your client, then execute purge-originals --apply --confirm-permanent-delete for only this planned scope. I understand permanent deletion removes Google recovery and local backups must remain protected.

Persist until a fresh full audit has media_complete, deletion_complete and complete true, or a real login/OS/confirmation blocker needs me. Reconnect and resume rather than discarding journals. If new arrivals prevent a complete audit, report them honestly. Restore my previous upload setting, stop the bridge and leave backups intact. Summarize active counts, exclusions, source-removal status, local backup location and separate images/videos before/after/savings tables. Measure file bytes separately from Google quota; do not claim every account/platform is validated.
```

### Only capture year 2023

```text
Use https://github.com/gregjotau/google-photos-cli to optimize ONLY photos and videos captured in 2023 on my macOS Google Photos account. Clone to ~/google-photos-cli, read README.md and AGENTS.md, and use a separate private --data-dir ~/google-photos-data for every command. Reuse account-bound journals and verify my intended account. Keep media, credentials, metadata and journals private and outside Git.

Install missing Git, uv, Chrome, Python >=3.15 via uv, libavif/AOM, ExifTool and FFmpeg with libx265/AAC from official sources (Homebrew on macOS). Run uv run --locked google-photos-cli.py --data-dir ~/google-photos-data doctor and self-test. Linux is unvalidated; Windows unsupported. Let me handle sign-in, MFA, administrator passwords, CAPTCHAs and OS restrictions directly without sharing secrets. Connect through the separate Chrome profile or documented broker/bootstrap, keep the bridge alive and never export Google cookies.

On EVERY plan, run, migrate, videos, repair, purge-originals and audit invocation append --year 2023. Never remove this filter while retrying or expand to other years. Other years, Locked Folder, unsaved shared items and unrelated Trash must remain untouched. Capture timestamp plus recorded offset decides the year.

Record my Backup quality, temporarily switch to Original quality for optimized uploads (I authorize this), and restore it after verification. Preview plan --year 2023 --limit 5; smoke-test run --year 2023 --limit 5 --apply; inspect backup checksums, exported hashes, dates and albums. Use default Live Photo conversion to one AVIF still, preserving complete original still+motion locally, and sensible default AVIF/HEVC settings. Back up every original locally on a disk with enough space. Existing AVIF/completed conversions must skip media download; keep videos when a candidate is larger.

Use permanent-deletion intent but initially omit --confirm-permanent-delete. Exit 2 for pending permanent deletions after verified conversion is expected. Continue run --year 2023 --apply without the sample limit, retaining journals and the original inventory. If required, videos --year 2023 --repair --apply repairs Storage saver rewrites under Original quality. Review the exact scoped permanent-delete-plan.json and all backup/replacement proofs, obtain any action-time confirmation your client requires, then execute purge-originals --year 2023 --apply --confirm-permanent-delete. Permanent deletion removes Google's recovery copy; keep local backups protected. Never empty account-wide Trash or upgrade legacy/Trash-mode records to permanent deletion.

Finish with audit --year 2023 reporting media_complete, deletion_complete and complete true. Reconnect and resume after interruptions; never erase journals or overwrite an active inventory with scan. Persist until complete unless login/OS/confirmation needs me. Restore my previous upload setting and stop the bridge. Report only 2023 results, with separate image/video savings tables, kept videos, exclusions, backup location and deletion status; do not claim the rest of my library is converted or equate file savings with billed quota.
```

### Preview 2023 without changing media

```text
Assess ONLY capture year 2023 using https://github.com/gregjotau/google-photos-cli on macOS. Clone to ~/google-photos-cli, read README.md and AGENTS.md, and keep all private data in --data-dir ~/google-photos-data. Reuse existing account-bound state without overwriting an active migration snapshot. Install missing official Git, uv, Chrome, Python >=3.15 via uv, libavif/AOM, ExifTool and FFmpeg with x265/AAC; run doctor and self-test using uv run --locked google-photos-cli.py. Windows is unsupported and Linux unvalidated.

Connect through the documented separate Chrome profile or broker/bootstrap. Verify the intended account and let me handle login, MFA or OS restrictions without sharing secrets. Run plan --year 2023 with the private data directory. Keep --year 2023 on every scoped command. This is READ-ONLY: no --apply, no --confirm-permanent-delete, no uploads, deletions, settings changes or test media. Do not empty Trash or access Locked Folder. Never export cookies or publish private state.

Summarize matching images/videos, original byte totals, existing AVIF skips, Live Photo still-conversion candidates, exclusions and required disk/tools. Explain default local backups, permanent-deletion intent plus explicit confirmation, --delete-mode trash recovery option and --live-photos keep option. Explain Original quality is needed before actual optimized uploads, but do not change it. Savings cannot be predicted from source bytes alone. Stop the bridge after preview and provide a proposed 2023 smoke-test command without executing it.
```

## Filters

Filters are available on `plan`, `run`, `migrate`, `videos`, `purge-originals`, and `audit`. Different filter types are combined with AND. Repeated years or album IDs match any of the supplied values.

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
uv run google-photos-cli.py run --media images --year 2024 --year 2025 --exclude-favorites --apply

# Large videos captured in a date range.
uv run google-photos-cli.py run --media videos --from-date 2024-01-01 --to-date 2025-12-31 --min-size-mb 10 --apply

# Preview only JPEGs from one album.
uv run google-photos-cli.py plan --album ALBUM_ID --filename '*.jpg'

# Explicit dry run, even if --apply was also supplied.
uv run google-photos-cli.py run --year current --apply --dry-run
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

These are lossy, storage-oriented settings. Resolution is retained for still images. Already smaller video sources are kept when the candidate encode would be larger. AVIF can occasionally be larger than a small JPEG; still-image conversion prioritizes obtaining AVIF. Auxiliary depth/MPO data is retained in the local original backup, not encoded into the primary AVIF. Animated still formats are rejected rather than silently flattened. HDR, Dolby Vision, and specialized metadata are not guaranteed to survive transcoding.

```sh
# Trade more CPU and memory for more parallel image work.
uv run google-photos-cli.py run --workers 6 --threads 2 --apply

# Higher image quality, slower encoding.
uv run google-photos-cli.py run --quality 60 --speed 4 --apply

# Local-only conversion.
uv run google-photos-cli.py convert input.jpg output.avif --quality 45 --speed 6
```

Python coordinates the job; AOM and x265 perform compression in native code. The client batches inventory metadata, replacement checks, original-export verification, album additions, and trash operations. It uses chunked uploads, streaming file fingerprints, bounded concurrency, and hash lookup to reconcile interrupted uploads. Rewriting this coordinator in Go would not speed up the native encoders or Google's processing queue; the useful tuning controls are worker/thread counts and encoding speed/quality. No cross-language benchmark is claimed.

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

Google can take time to make a new upload available as its true original. The job waits and verifies again; it retains the old item when a check fails. Never lower the hash-verification requirement to force a migration through.

```sh
uv run google-photos-cli.py status
uv run google-photos-cli.py report
uv run google-photos-cli.py audit --year 2025
```

`report` separates image and video savings for confirmed replacements in the journals. It excludes unchanged assets and measures file bytes, not the account's eventual quota adjustment. A filtered audit checks only that original selection and its replacements. An unfiltered audit checks the whole accessible saved library and archive; it can fail if new photos have arrived outside the original snapshot.

Resume with the same data directory and command after reconnecting Chrome. Journals prevent successful replacements from being uploaded again and reconcile ambiguous commits using Google's content-hash lookup. Do not erase journals, close/refresh the bridge tab during a run, or overwrite an active migration's initial snapshot with `scan`. On macOS, `caffeinate -i uv run google-photos-cli.py run --apply` keeps the Mac awake; it does not override Screen Time. Unlock blocked Chrome normally before resuming.

## Separate accounts and existing Chrome sessions

Use one connected account at a time and a distinct data directory/profile for each:

```sh
uv run google-photos-cli.py --data-dir ~/photos-account-two connect --profile ~/photos-account-two/chrome
uv run google-photos-cli.py --data-dir ~/photos-account-two plan --year current
uv run google-photos-cli.py --data-dir ~/photos-account-two run --year current --apply
```

The reported account must match the snapshot and journals. The loopback broker is shared by this installation, so disconnect the previous account before switching. Keep the profile and data directory private; they contain your session, media, and metadata.

For an already signed-in Chrome tab, run `broker`, then `bootstrap` in another terminal, and paste the generated **local** `bootstrap.js` into that Photos tab's DevTools console once. It contains this repository's self-authored worker and a local broker key. Do not use an `eval(fetch(...))` shortcut; browser Trusted Types policies may reject it. No UI selection is used for per-photo operations.

The broker listens only on `127.0.0.1:8765`, checks a random session key and browser origin, and restricts the browser worker's RPCs and media destinations. Google authentication stays in Chrome. Python receives metadata, media, and short-lived Google original-export links; it does not receive Google cookies or CSRF tokens. Runtime files and media are ignored by Git using an explicit source-file allowlist.

## Development

```sh
uv run --locked google-photos-cli.py self-test
uv lock --script google-photos-cli.py
```

Offline tests cover filter boundaries, preview behavior, refusal to trash on failed size/hash checks, and write-ahead upload intent. CI runs these tests without Google credentials. Protocol and codec references:

- [libavif](https://github.com/AOMediaCodec/libavif)
- [FFmpeg codecs](https://ffmpeg.org/ffmpeg-codecs.html)
- [Google Photos public API](https://developers.google.com/photos/library/reference/rest) — not sufficient for migrating an arbitrary existing library or trashing its originals
- [Google Photos Web Client](https://github.com/xob0t/google_photos_web_client), [Google Photos Toolkit](https://github.com/xob0t/Google-Photos-Toolkit), and [Google Photos MCP](https://github.com/benjamin-di/google-photos-mcp) — protocol research references; this client does not install or execute their source

MIT licensed. Google Photos is a Google trademark; this project is not affiliated with Google.
