import json, subprocess, tempfile
from converter import digest
from pathlib import Path
from fractions import Fraction
from datetime import datetime, timezone


def run(args):
    p = subprocess.run([str(x) for x in args], capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError(p.stderr[-4000:])
    return p.stdout


def probe(path):
    return json.loads(
        run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_format",
                "-show_streams",
                "-of",
                "json",
                path,
            ]
        )
    )


def frame_rate(stream):
    for field in ("avg_frame_rate", "r_frame_rate"):
        try:
            rate = float(Fraction(stream.get(field) or "0"))
            if rate > 0:
                return rate
        except (ValueError, ZeroDivisionError):
            pass
    return None


def validate_encoded(source, target, *, before=None, codec="hevc"):
    """Validate a new or recovered output against the retained SOURCE."""
    before = before or probe(source)
    after = probe(target)
    original = next(s for s in before["streams"] if s["codec_type"] == "video")
    video = next(s for s in after["streams"] if s["codec_type"] == "video")
    if video["codec_name"] != codec:
        raise RuntimeError("Unexpected video codec")
    duration = float(before["format"]["duration"])
    encoded_duration = float(after["format"]["duration"])
    if abs(duration - encoded_duration) > max(
        0.25, 1 / max(frame_rate(original) or 30, 1)
    ):
        raise RuntimeError("Encoded duration mismatch against original source")
    has_audio = lambda p: any(s["codec_type"] == "audio" for s in p["streams"])
    if has_audio(before) != has_audio(after):
        raise RuntimeError("Encoded audio presence mismatch against original source")
    width, height = video["width"], video["height"]
    rate = frame_rate(video)
    if (
        min(width, height) > 1080
        or max(width, height) > 1920
        or rate is None
        or rate > 30.01
    ):
        raise RuntimeError("Encoded dimensions/frame rate exceed optimization limits")
    if codec == "hevc" and (
        video.get("codec_tag_string") != "hvc1" or video.get("pix_fmt") != "yuv420p10le"
    ):
        raise RuntimeError("Expected HEVC Main10/hvc1 output")
    run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-xerror",
            "-err_detect",
            "explode",
            "-nostdin",
            "-i",
            target,
            "-f",
            "null",
            "-",
        ]
    )
    return {
        "sha256": digest(target),
        "duration": encoded_duration,
        "width": width,
        "height": height,
        "codec": codec,
    }


def remux_original_quality(source, target):
    """Change container metadata for a fresh upload without re-encoding streams.

    Google deduplicates by upload hash even after rewriting a Storage saver
    item. A distinct container permits a new Original-quality upload.
    """
    source, target = Path(source), Path(target)

    def stream_hash(path):
        return run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                path,
                "-map",
                "0",
                "-c",
                "copy",
                "-f",
                "streamhash",
                "-hash",
                "sha256",
                "-",
            ]
        )

    if target.exists():
        if stream_hash(source) != stream_hash(target):
            raise RuntimeError("Existing remux does not preserve encoded streams")
        return
    with tempfile.TemporaryDirectory(dir=target.parent) as td:
        pending = Path(td) / "original-quality.mp4"
        run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-nostdin",
                "-i",
                source,
                "-map",
                "0",
                "-c",
                "copy",
                "-map_metadata",
                "0",
                "-metadata",
                "comment=google-photos-cli original-quality preservation",
                "-movflags",
                "+faststart",
                pending,
            ]
        )
        before, after = probe(source), probe(pending)
        if (
            abs(
                float(before["format"]["duration"]) - float(after["format"]["duration"])
            )
            > 0.1
        ):
            raise RuntimeError("Remux changed video duration")
        if [(s["codec_type"], s["codec_name"]) for s in before["streams"]] != [
            (s["codec_type"], s["codec_name"]) for s in after["streams"]
        ]:
            raise RuntimeError("Remux changed streams")
        if stream_hash(source) != stream_hash(pending):
            raise RuntimeError("Remux changed encoded stream bytes")
        pending.replace(target)


def encode(source, target, timestamp, crf=35, preset=8, threads=4, codec="hevc"):
    source, target = Path(source), Path(target)
    if target.exists():
        raise RuntimeError("Video output already exists")
    before = probe(source)
    v = next(s for s in before["streams"] if s["codec_type"] == "video")
    fps = frame_rate(v)
    scale = "scale=w='min(iw,if(gte(iw,ih),1920,1080))':h='min(ih,if(gte(iw,ih),1080,1920))':force_original_aspect_ratio=decrease:force_divisible_by=2"
    if fps is None or fps > 30.01:
        scale += ",fps=30"
    date = (
        datetime.fromtimestamp(timestamp / 1000, timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )
    with tempfile.TemporaryDirectory(dir=target.parent) as td:
        pending = Path(td) / "encoded.mp4"
        args = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-xerror",
            "-err_detect",
            "explode",
            "-nostdin",
            "-i",
            source,
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-map_metadata",
            "0",
            "-vf",
            scale,
            "-c:v",
            ("libx265" if codec == "hevc" else "libsvtav1"),
            "-preset",
            ("medium" if codec == "hevc" else preset),
            "-crf",
            (30 if codec == "hevc" else crf),
            "-pix_fmt",
            "yuv420p10le",
            *(
                [
                    "-tag:v",
                    "hvc1",
                    "-x265-params",
                    f"pools={threads}:frame-threads=2:log-level=error",
                ]
                if codec == "hevc"
                else ["-svtav1-params", f"lp={threads}:tune=0"]
            ),
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            "-ac",
            "2",
            "-metadata",
            "creation_time=" + date,
            "-movflags",
            "+faststart",
            pending,
        ]
        run(args)
        validation = validate_encoded(source, pending, before=before, codec=codec)
        pending.replace(target)
    return {
        "original_bytes": source.stat().st_size,
        "encoded_bytes": target.stat().st_size,
        **validation,
        "crf": 30 if codec == "hevc" else crf,
        "preset": "medium" if codec == "hevc" else preset,
    }
