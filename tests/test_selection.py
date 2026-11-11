import contextlib
from datetime import date, datetime
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from selection import Selection, year_value


def fixture(
    key="old",
    captured="2026-01-01T00:30:00+00:00",
    offset=0,
    filename="IMG_1234.JPG",
    video=False,
    favorite=False,
    archived=False,
):
    row = [None] * 14
    row[0], row[13] = key, archived
    a = {}
    if video:
        a["76647426"] = [1000]
    if favorite:
        a["163238866"] = [1]
    row.append(a)
    e = [None] * 20
    e[0], e[2], e[3], e[4], e[5], e[19] = (
        key,
        filename,
        int(datetime.fromisoformat(captured).timestamp() * 1000),
        offset,
        2_000_000,
        [["album-a"]],
    )
    return row, [e]


class Filters(unittest.TestCase):
    def test_capture_offset_decides_year_at_boundary(self):
        row, ext = fixture(offset=-3_600_000)
        self.assertTrue(Selection(years=[2025]).matches(row, ext))
        self.assertFalse(Selection(years=[2026]).matches(row, ext))

    def test_current_and_multiple_years(self):
        self.assertEqual(year_value("current"), datetime.now().year)
        row, ext = fixture()
        self.assertTrue(Selection(years=[2024, 2026]).matches(row, ext))
        self.assertFalse(Selection(years=[2024, 2025]).matches(row, ext))

    def test_inclusive_dates_and_combined_filters(self):
        row, ext = fixture(video=True)
        selection = Selection(
            from_date=date(2026, 1, 1),
            to_date=date(2026, 1, 1),
            media="videos",
            albums=["album-b", "album-a"],
            filename="img_*.jpg",
            min_bytes=2_000_000,
        )
        self.assertTrue(selection.matches(row, ext))
        self.assertFalse(Selection(media="images").matches(row, ext))
        self.assertFalse(Selection(min_bytes=2_000_001).matches(row, ext))
        self.assertFalse(Selection(albums=["album-b"]).matches(row, ext))

    def test_favorites_archive_and_invalid_range(self):
        row, ext = fixture(favorite=True, archived=True)
        self.assertFalse(Selection(exclude_favorites=True).matches(row, ext))
        self.assertFalse(Selection(exclude_archived=True).matches(row, ext))
        with self.assertRaises(ValueError):
            Selection(from_date=date(2026, 2, 1), to_date=date(2026, 1, 1))
        with self.assertRaises(ValueError):
            Selection(limit=0)

    def test_limit_applies_after_filtering(self):
        rows, entries = [], {}
        for key, year in [("a", 2024), ("b", 2026), ("c", 2026)]:
            row, ext = fixture(key=key, captured=f"{year}-06-01T00:00:00+00:00")
            rows.append(row)
            entries[key] = ext
        result = Selection(years=[2026], limit=1).select(
            {"items": rows}, {"items": entries}
        )
        self.assertEqual([r[0] for r in result], ["b"])


class DryRun(unittest.TestCase):
    def test_dry_run_overrides_apply_without_contacting_google(self):
        spec = importlib.util.spec_from_file_location(
            "cli_test", Path(__file__).parents[1] / "google-photos-cli.py"
        )
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        row, ext = fixture()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "inventory-api.json").write_text(
                json.dumps({"account": "example", "complete": True, "items": [row]})
            )
            (root / "catalog-api.json").write_text(
                json.dumps({"account": "example", "items": {"old": ext}})
            )
            with (
                patch.object(
                    sys,
                    "argv",
                    [
                        "cli",
                        "--data-dir",
                        td,
                        "run",
                        "--apply",
                        "--dry-run",
                        "--year",
                        "2026",
                    ],
                ),
                patch(
                    "photos_api.API",
                    side_effect=AssertionError("Dry run contacted Google"),
                ),
                patch.dict(os.environ),
                contextlib.redirect_stdout(io.StringIO()) as output,
            ):
                cli.main()
            self.assertIn('"cloud_changes": false', output.getvalue())
            self.assertFalse((root / "work-api").exists())


if __name__ == "__main__":
    unittest.main()
