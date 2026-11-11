"""Synthetic local codec integration tests; never access Google or private media."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from converter import convert, metadata
from video_codec import encode, probe, remux_original_quality


@unittest.skipUnless(
    all(shutil.which(x) for x in ("avifenc", "exiftool")),
    "Native image tools unavailable",
)
class Images(unittest.TestCase):
    def test_heic_and_jpeg_preserve_capture_and_oriented_dimensions(self):
        from PIL import Image
        from pillow_heif import register_heif_opener

        register_heif_opener()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for suffix in (".jpg", ".heic"):
                with self.subTest(suffix=suffix):
                    src, out = root / ("source" + suffix), root / (suffix[1:] + ".avif")
                    image = Image.new("RGB", (64, 32), (45, 110, 180))
                    orientation = image.getexif()
                    orientation[274] = 6
                    # HEIF presentation uses container transforms. Author both
                    # correctly via its encoder, rather than editing EXIF alone.
                    image.save(src, exif=orientation.tobytes())
                    result = convert(
                        src,
                        out,
                        speed=10,
                        threads=1,
                        capture_time=(1700000000123, 3600000),
                        gps=(-12.5, 8.25),
                        live_still=True,
                    )
                    self.assertEqual(result["display_size"], (32, 64))
                    with Image.open(out) as im:
                        self.assertEqual(im.size, (32, 64))
                    self.assertEqual(metadata(out)["OffsetTimeOriginal"], "+01:00")
                    self.assertAlmostEqual(metadata(out)["GPSLatitude"], 12.5)


@unittest.skipUnless(
    all(shutil.which(x) for x in ("ffmpeg", "ffprobe")),
    "Native video tools unavailable",
)
class Video(unittest.TestCase):
    def test_silent_and_audio_video_decode_and_remux_without_stream_changes(self):
        encoders = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        if "libx265" not in encoders:
            self.skipTest("FFmpeg lacks libx265")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for audio in (False, True):
                with self.subTest(audio=audio):
                    src, out, remux = [
                        root / f"{name}-{audio}.mp4"
                        for name in ("source", "encoded", "remux")
                    ]
                    args = [
                        "ffmpeg",
                        "-v",
                        "error",
                        "-f",
                        "lavfi",
                        "-i",
                        "testsrc2=size=64x96:rate=60:duration=1",
                    ]
                    if audio:
                        args += [
                            "-f",
                            "lavfi",
                            "-i",
                            "sine=frequency=440:duration=1",
                            "-c:a",
                            "aac",
                        ]
                    subprocess.run(
                        args + ["-c:v", "mpeg4", str(src)],
                        check=True,
                        capture_output=True,
                    )
                    result = encode(src, out, 1700000000000, threads=1)
                    self.assertEqual(result["codec"], "hevc")
                    streams = probe(out)["streams"]
                    self.assertEqual(
                        any(s["codec_type"] == "audio" for s in streams), audio
                    )
                    self.assertEqual(
                        next(s for s in streams if s["codec_type"] == "video")[
                            "avg_frame_rate"
                        ],
                        "30/1",
                    )
                    remux_original_quality(out, remux)
                    self.assertTrue(remux.is_file())
