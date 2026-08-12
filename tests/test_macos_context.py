import unittest
from types import SimpleNamespace
from unittest.mock import patch

from macos_context import (
    InputContext,
    _rect_tuple,
    accessibility_is_trusted,
    capture_input_context,
)


class RectTests(unittest.TestCase):
    def test_converts_nsrect_shape(self):
        rect = SimpleNamespace(
            origin=SimpleNamespace(x=1, y=2),
            size=SimpleNamespace(width=3, height=4),
        )
        self.assertEqual(_rect_tuple(rect), (1.0, 2.0, 3.0, 4.0))

    def test_invalid_rect_is_none(self):
        self.assertIsNone(_rect_tuple("invalid"))

    def test_input_context_defaults_are_safe(self):
        context = InputContext()
        self.assertIsNone(context.element)
        self.assertIsNone(context.pid)
        self.assertIsNone(context.caret_frame)

    def test_accessibility_probe_returns_bool(self):
        self.assertIsInstance(accessibility_is_trusted(), bool)

    @patch("macos_context.get_caret_frame", side_effect=ImportError("missing"))
    @patch("macos_context.collect_context_texts", return_value=["Type here"])
    @patch("macos_context._element_pid", return_value=42)
    @patch("macos_context.focused_element", return_value=object())
    def test_caret_failure_does_not_block_context_capture(
        self, _focused, _pid, _texts, _caret
    ):
        context = capture_input_context()
        self.assertEqual(context.pid, 42)
        self.assertEqual(context.detected_language, "en")
        self.assertIsNone(context.caret_frame)

    @patch("macos_context.focused_element", side_effect=RuntimeError("denied"))
    def test_focus_failure_returns_empty_context(self, _focused):
        self.assertEqual(capture_input_context(), InputContext())


if __name__ == "__main__":
    unittest.main()
