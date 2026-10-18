"""Font selection regressions for the isolated historical renderer."""
from pathlib import Path
import unittest
from unittest import mock

from card_maker import LyricCard, resolve_font


class LegacyFontTests(unittest.TestCase):
    def test_explicit_html_font_is_rejected_before_background_is_opened(self):
        invalid = Path(__file__).resolve().parents[1] / "fonts/NotoSerifJP-Regular.otf"
        with self.assertRaisesRegex(ValueError, "HTML download pages are not fonts"):
            LyricCard("unused-background.png", font_path=invalid)

    def test_automatic_selection_skips_unloadable_font(self):
        with mock.patch("card_maker.FONT_CANDIDATES", ("bad.otf", "valid.ttc")), mock.patch(
            "card_maker.ImageFont.truetype", side_effect=[OSError("invalid font"), object()]
        ) as loader:
            self.assertEqual(resolve_font(), "valid.ttc")
        self.assertEqual(loader.call_args_list, [mock.call("bad.otf", 16), mock.call("valid.ttc", 16)])

    def test_no_valid_automatic_font_has_actionable_error(self):
        with mock.patch("card_maker.FONT_CANDIDATES", ("missing-font-for-test.ttf",)):
            with self.assertRaisesRegex(FileNotFoundError, "No font is downloaded automatically"):
                resolve_font()

    def test_explicit_font_failure_does_not_silently_substitute(self):
        with mock.patch("card_maker.ImageFont.truetype", side_effect=OSError("bad")) as loader:
            with self.assertRaises(ValueError):
                resolve_font("chosen.ttf")
        loader.assert_called_once_with("chosen.ttf", 16)
