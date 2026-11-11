import json, subprocess, tempfile, hashlib
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


def encode(source, target, timestamp, crf=35, preset=8, threads=4, codec="hevc"):
    source, target = Path(source), Path(target)
    if target.exists():
        raise RuntimeError("Video output already exists")
    before = probe(source)
    v = next(s for s in before["streams"] if s["codec_type"] == "video")
    fps = float(Fraction(v.get("avg_frame_rate", "30/1") or "30/1"))
    scale = "scale=w='min(iw,if(gte(iw,ih),1920,1080))':h='min(ih,if(gte(iw,ih),1080,1920))':force_original_aspect_ratio=decrease:force_divisible_by=2"
    if fps > 30.01:
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
        after = probe(pending)
        nv = next(s for s in after["streams"] if s["codec_type"] == "video")
        if nv["codec_name"] != codec:
            raise RuntimeError("Unexpected video codec")
        dur = float(before["format"]["duration"])
        ndur = float(after["format"]["duration"])
        if abs(dur - ndur) > max(0.25, 1 / max(fps, 1)):
            raise RuntimeError("Encoded duration mismatch")
        if any(s["codec_type"] == "audio" for s in before["streams"]) and not any(
            s["codec_type"] == "audio" for s in after["streams"]
        ):
            raise RuntimeError("Audio lost")
        run(["ffmpeg", "-v", "error", "-nostdin", "-i", pending, "-f", "null", "-"])
        pending.replace(target)
    return {
        "original_bytes": source.stat().st_size,
        "encoded_bytes": target.stat().st_size,
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "duration": ndur,
        "width": nv["width"],
        "height": nv["height"],
        "codec": codec,
        "crf": 30 if codec == "hevc" else crf,
        "preset": "medium" if codec == "hevc" else preset,
    }
