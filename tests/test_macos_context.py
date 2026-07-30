import unittest
from types import SimpleNamespace

from macos_context import InputContext, _rect_tuple, accessibility_is_trusted


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


if __name__ == "__main__":
    unittest.main()
