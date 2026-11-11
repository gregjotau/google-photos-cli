# Storage saver can enlarge an already compressed HEVC upload

Observed on 2026-11-11 while validating this client against Google Photos web uploads. This is a reproducible workflow observation, not a claim that Storage saver always increases sizes or that Google's billed quota equals downloadable file size.

## Measured example

| Representation | Codec | Bytes |
| --- | --- | ---: |
| Locally optimized upload | HEVC Main10 in MP4/hvc1 | 3,773,661 |
| Later Google original export, uploaded in Storage saver | VP9 in MP4 | 10,425,991 |
| Container-only remux uploaded in Original quality | Same HEVC streams | 3,773,732 |

The clip was approximately 39.864 seconds at 1080×1920. The Storage saver export was about **2.76 times** the uploaded size, with no increase in resolution. The Original-quality upload was verified byte for byte after Google exposed playable video variants. The 71-byte difference between the two HEVC files is container metadata; no further video/audio encode was performed.

The Storage saver upload initially exported byte-identically to the HEVC input. After asynchronous processing, its reported size and true-original export changed. This means a successful early download/hash check alone is insufficient when Storage saver is enabled. The final library audit detected the later changes. Across the tested video batch, reported sizes changed in multiple directions; do not assume every transcode became larger.

## Reproduction procedure

1. Use an already compressed, playable HEVC Main10/hvc1 MP4 with AAC audio at 1080p or below. Record its size, SHA-256, codec, and dimensions with `ffprobe`.
2. Select Google Photos web Settings → Backup quality → Storage saver and upload the file.
3. Wait for processing/playback variants. Re-download the original later, not merely immediately after upload. Compare bytes, codec, dimensions, and hash.
4. Repeat in Original quality with a distinct container hash; Google may deduplicate a byte-identical re-upload to the earlier item. A container-only remux can preserve the same video/audio streams.
5. Compare Original-quality export bytes with the actual uploaded file.

Results depend on the input and Google processing. No private clip, account identifier, media ID, cookie, or signed download URL is included in this report. The exact sample is not distributed.

## Tool behavior and recovery

Use **Original quality before image or video optimization**. This preserves this tool's already compressed output; it does not mean uploading the large source MOV again. The client checks Google's reported quality and refuses to remove a video source if the replacement is in Storage saver.

If earlier replacements were rewritten, first select Original quality in the web settings, then run:

```sh
uv run --locked google-photos-cli.py videos --repair --apply
uv run --locked google-photos-cli.py audit --verify-exports
```

Filters can be used on the repair command too, for example `--year 2025`. Repair uses the retained local encoded backup, remuxes its streams without another lossy encode, journals the new upload, verifies quality/date/timezone/playback/original-export hash, removes the superseded cloud replacement, and updates the authoritative journal. The original source backup remains local. Do not delete journals to force a retry. A filtered repair does not repair videos outside its selection. Add the same year/media filters to repair, audit, backup verification and permanent-removal commands. New permanent-mode conversions may finish their media repair but exit 2 while source purging awaits explicit confirmation; follow the README two-phase removal flow.

The tool does not change your account setting automatically. Only restore a prior web upload preference after all replacement uploads and their verification have completed. Do not select “Convert existing photos & videos to Storage saver” for the optimized results if you want their encoded bytes preserved.

Google documents that Original quality retains resolution/quality, that Storage saver can recompress media, and that the browser upload preference is separate from device settings. It does not promise a smaller downloadable representation for every pre-compressed input. See [Google's backup-quality documentation](https://support.google.com/photos/answer/6220791?co=GENIE.Platform%3DDesktop&hl=en).

## Suggested Google report

Submitted through Google Photos web **Help & Feedback** on 2026-11-11. The UI confirmed “Thanks for your feedback!” No media or screenshot was attached, and no public ticket URL was provided. This confirms submission, not Google's acceptance or diagnosis of the issue.

Subject: **Storage saver asynchronously expands an optimized HEVC upload into a larger VP9 original export**

Please investigate the Storage saver transcoding policy for already compressed HEVC/hvc1 MP4 uploads. In one measured 1080×1920, approximately 39.864-second example, a 3,773,661-byte HEVC Main10/AAC MP4 initially re-downloaded byte-identically, but after asynchronous processing the original export became a 10,425,991-byte VP9/AAC MP4 at the same resolution (2.76× larger). A container-only remux of the same HEVC streams uploaded in Original quality remained byte-identical at 3,773,732 bytes after playable variants were available.

Expected: keep an already efficient original when the proposed Storage saver transcode is larger, or clearly expose/document this behavior and distinguish original download size from billed quota. Actual: the original representation and its reported size changed after an initially successful verification. We have not established whether billed quota increases by the same amount and are not claiming universal behavior. No private media or account/session information is attached.
