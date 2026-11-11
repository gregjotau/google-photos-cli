#!/usr/bin/env python3
"""Native AVIF encoding with physical orientation and metadata verification."""

import hashlib
import json
import math
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
TAGS = [
    "DateTimeOriginal",
    "CreateDate",
    "OffsetTimeOriginal",
    "Make",
    "Model",
    "ExposureTime",
    "FNumber",
    "FocalLength",
    "ISO",
    "GPSLatitude",
    "GPSLongitude",
]


def run(*args):
    p = subprocess.run([str(a) for a in args], capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError(p.stderr or p.stdout)
    return p.stdout


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def metadata(path):
    return json.loads(run("exiftool", "-j", "-n", *["-EXIF:" + t for t in TAGS], path))[
        0
    ]


def atomic_json(path, value):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(path)


def convert(
    source, output, quality=45, speed=6, threads=4, capture_time=None, gps=None
):
    from PIL import Image, ImageOps

    source, output = Path(source).resolve(), Path(output).resolve()
    if source == output or output.exists():
        raise RuntimeError("Output exists; refusing to overwrite it")
    before = metadata(source)
    if not before.get("DateTimeOriginal") and capture_time is None:
        raise RuntimeError(
            "No embedded capture date; cloud/Takeout date needs reconciliation first"
        )
    with Image.open(source) as im:
        if getattr(im, "n_frames", 1) != 1 and im.format != "MPO":
            raise RuntimeError("Animated media needs explicit frame preservation")
        # MPO portrait/depth auxiliaries stay in the original backup; encode its primary still.
        im.seek(0)
        if im.mode in ("P", "1"):
            im = im.convert(
                "RGBA" if im.info.get("transparency") is not None else "RGB"
            )
        if im.mode not in ("RGB", "RGBA", "L", "LA"):
            raise RuntimeError(
                f"Unsupported color mode {im.mode}; avoid implicit color conversion"
            )
        # Google Photos ignores irot on some AVIFs. Normalize actual pixels first.
        oriented = ImageOps.exif_transpose(im)
        expected_size = oriented.size
        with tempfile.TemporaryDirectory(dir=output.parent) as td:
            png = Path(td) / "oriented.png"
            # This is a temporary encoder input: favor fast lossless PNG writes.
            oriented.save(png, compress_level=1, icc_profile=im.info.get("icc_profile"))
            pending = Path(td) / "encoded.avif"
            run(
                "avifenc",
                "-c",
                "aom",
                "-q",
                quality,
                "-s",
                speed,
                "-d",
                10,
                "-y",
                "420",
                "-j",
                threads,
                png,
                pending,
            )
            # Preserve EXIF/XMP/ICC; dimensions and orientation describe normalized pixels.
            run(
                "exiftool",
                "-n",
                "-overwrite_original",
                "-TagsFromFile",
                source,
                "-EXIF:All",
                "-XMP:All",
                "-ICC_Profile",
                "-Orientation=1",
                "-XMP-tiff:Orientation=1",
                f"-ExifImageWidth={expected_size[0]}",
                f"-ExifImageHeight={expected_size[1]}",
                pending,
            )
            if capture_time is not None:
                from datetime import datetime, timezone, timedelta

                ts, offset = capture_time
                date = datetime.fromtimestamp(
                    ts / 1000, timezone(timedelta(milliseconds=offset))
                )
                dt = date.strftime("%Y:%m:%d %H:%M:%S")
                zone = date.strftime("%z")
                zone = zone[:3] + ":" + zone[3:]
                run(
                    "exiftool",
                    "-n",
                    "-overwrite_original",
                    f"-DateTimeOriginal={dt}",
                    f"-CreateDate={dt}",
                    f"-OffsetTimeOriginal={zone}",
                    f"-OffsetTimeDigitized={zone}",
                    f"-SubSecTimeOriginal={int(ts) % 1000:03d}",
                    pending,
                )
                before.update(
                    DateTimeOriginal=dt, CreateDate=dt, OffsetTimeOriginal=zone
                )
            if gps is not None:
                lat, lon = gps
                run(
                    "exiftool",
                    "-n",
                    "-overwrite_original",
                    f"-GPSLatitude={abs(lat)}",
                    f"-GPSLongitude={abs(lon)}",
                    "-GPSLatitudeRef=" + ("N" if lat >= 0 else "S"),
                    "-GPSLongitudeRef=" + ("E" if lon >= 0 else "W"),
                    pending,
                )
                before.update(GPSLatitude=abs(lat), GPSLongitude=abs(lon))
            after = metadata(pending)
            for key in TAGS:
                if before.get(key) != after.get(key) and not (
                    isinstance(before.get(key), (int, float))
                    and isinstance(after.get(key), (int, float))
                    and math.isclose(
                        before[key], after[key], rel_tol=1e-7, abs_tol=1e-8
                    )
                ):
                    raise RuntimeError(f"Metadata mismatch: {key}")
            with Image.open(pending) as check:
                check.load()
                if check.size != expected_size:
                    raise RuntimeError("Decoded AVIF dimensions mismatch")
            pending.replace(output)
    return {
        "original_bytes": source.stat().st_size,
        "avif_bytes": output.stat().st_size,
        "original_sha256": digest(source),
        "avif_sha256": digest(output),
        "display_size": expected_size,
        "metadata": {t: before[t] for t in TAGS if t in before},
    }
