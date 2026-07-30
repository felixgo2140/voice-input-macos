import unittest
from unittest.mock import patch

from macos_context import InputContext
from voice_input import (
    ESCAPE_KEY_CODE,
    RETURN_KEY_CODES,
    RIGHT_OPTION_KEY_CODE,
    is_plain_enter_event,
    paste_result_to_context,
    prefer_external_input_context,
    right_option_transition,
    status_with_icon,
)


class HotkeyTests(unittest.TestCase):
    def test_right_option_fires_only_on_down_edge(self):
        self.assertEqual(
            right_option_transition(RIGHT_OPTION_KEY_CODE, True, False),
            (True, True),
        )
        self.assertEqual(
            right_option_transition(RIGHT_OPTION_KEY_CODE, True, True),
            (False, True),
        )
        self.assertEqual(
            right_option_transition(RIGHT_OPTION_KEY_CODE, False, True),
            (False, False),
        )

    def test_other_option_key_does_not_fire(self):
        self.assertEqual(
            right_option_transition(58, True, False), (False, False)
        )

    def test_plain_enter_helper_does_not_accept_modifiers(self):
        key = next(iter(RETURN_KEY_CODES))
        self.assertTrue(is_plain_enter_event(key, 0))
        self.assertFalse(is_plain_enter_event(key, 1 << 17))
        self.assertFalse(is_plain_enter_event(key, 1 << 20))

    def test_escape_and_right_option_are_different(self):
        self.assertNotEqual(ESCAPE_KEY_CODE, RIGHT_OPTION_KEY_CODE)

    def test_status_icon_is_not_duplicated(self):
        self.assertEqual(status_with_icon("已完成", "✅"), "✅ 已完成")
        self.assertEqual(status_with_icon("✅ 已完成", "✅"), "✅ 已完成")


class ContextTests(unittest.TestCase):
    def test_keeps_previous_external_context_when_panel_is_focused(self):
        previous = InputContext(pid=12)
        captured = InputContext(pid=99)
        self.assertIs(
            prefer_external_input_context(captured, previous, 99), previous
        )

    def test_accepts_new_external_context(self):
        captured = InputContext(pid=12)
        self.assertIs(
            prefer_external_input_context(captured, None, 99), captured
        )

    @patch("voice_input.paste_text")
    @patch("voice_input.restore_input_focus", return_value=True)
    def test_writeback_restores_focus_then_pastes(self, restore, paste):
        context = InputContext(element=None, pid=12)
        self.assertTrue(
            paste_result_to_context(
                context, "hello", restore_clipboard=False, focus_timeout=0
            )
        )
        restore.assert_called_once_with(context)
        paste.assert_called_once_with("hello", False)

    @patch("voice_input.copy_text")
    @patch("voice_input.activate_context_application", return_value=False)
    @patch("voice_input.restore_input_focus", return_value=False)
    def test_failed_focus_copies_for_manual_retry(
        self, _restore, _activate, copy
    ):
        context = InputContext(element=None, pid=12)
        self.assertFalse(paste_result_to_context(context, "hello"))
        copy.assert_called_once_with("hello")


if __name__ == "__main__":
    unittest.main()
