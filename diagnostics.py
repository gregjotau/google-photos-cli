"""Local prerequisites and capability checks, without contacting Google."""

import platform
import shutil
import subprocess
import sys
from pathlib import Path


def doctor(root):
    checks = {}
    problems = []
    for tool, args in {
        "avifenc": ["--version"],
        "exiftool": ["-ver"],
        "ffmpeg": ["-version"],
        "ffprobe": ["-version"],
    }.items():
        try:
            result = subprocess.run(
                [tool, *args], capture_output=True, text=True, timeout=15, check=True
            )
            checks[tool] = (result.stdout + result.stderr).strip()
        except (OSError, subprocess.SubprocessError) as error:
            problems.append(
                f"{tool}: {error}. Install native prerequisites; see README."
            )
    if "avifenc" in checks and "aom [enc" not in checks["avifenc"]:
        problems.append("avifenc must include the AOM encoder")
    if "ffmpeg" in checks:
        try:
            result = subprocess.run(
                ["ffmpeg", "-hide_banner", "-encoders"],
                capture_output=True,
                text=True,
                timeout=15,
                check=True,
            )
            for encoder in ("libx265", "aac"):
                if not any(
                    len(line.split()) > 1 and line.split()[1] == encoder
                    for line in result.stdout.splitlines()
                ):
                    problems.append(f"FFmpeg must include {encoder}")
        except (OSError, subprocess.SubprocessError) as error:
            problems.append(f"Could not inspect FFmpeg encoders: {error}")
    import PIL
    from PIL import features
    import pillow_heif

    checks.update(
        python=sys.version.split()[0],
        pillow=PIL.__version__,
        pillow_heif=pillow_heif.__version__,
        libheif=pillow_heif.libheif_info(),
    )
    if not features.check("avif"):
        problems.append("Pillow needs AVIF decoding support for output verification")
    chrome = next(
        (
            p
            for p in (
                shutil.which("google-chrome"),
                shutil.which("google-chrome-stable"),
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            )
            if p and Path(p).is_file()
        ),
        None,
    )
    checks["chrome"] = chrome
    if not chrome:
        problems.append(
            "Google Chrome was not found in the standard locations; install Chrome or verify its location"
        )
    if sys.platform == "win32":
        problems.append("Windows is unsupported (POSIX file locking is required)")
    return {
        "ready": not problems,
        "platform": platform.platform(),
        "data_dir": str(root),
        "free_disk_bytes": shutil.disk_usage(root).free,
        "checks": checks,
        "problems": problems,
        "cloud_requests": 0,
    }
