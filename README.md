Supported by **[ReAI](https://reai.no)**, Norway's most automated accounting system.

# Google Photos CLI

Batch-convert Google Photos images to AVIF and optimize videos as HEVC MP4, with resumable uploads, album transfer, capture-date checks, local original backups, and verified removal of originals from the active library.

**Python 3.15+, one `uv run` entry point, native encoders, no `requirements.txt`.** Python dependencies are declared inline in the script using PEP 723 and resolved by uv. You clone the repository because the entry point imports its companion modules.

This is an independent, experimental client using Google's undocumented web API. It has been exercised against a real library, but a complete migration of that library is still being validated. Google can change the protocol. Keep an independent backup and try a small batch first. Originals are moved to recoverable Google Photos Trash only after verification; the tool never empties Trash. Google's own Trash retention rules still apply.

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

Existing AVIF images are kept. Combined Live/Motion Photos are kept intact, without splitting them into separate still and video items. Locked Folder, unsaved shared items, and items already in Trash are outside the migration scope.

## Filters

Filters are available on `plan`, `run`, `migrate`, `videos`, and `audit`. Different filter types are combined with AND. Repeated years or album IDs match any of the supplied values.

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
