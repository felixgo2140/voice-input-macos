import unittest
from types import SimpleNamespace
from unittest.mock import patch

from macos_context import InputContext
from voice_input import (
    ESCAPE_KEY_CODE,
    RETURN_KEY_CODES,
    RIGHT_OPTION_KEY_CODE,
    concise_error_message,
    is_plain_enter_event,
    paste_result_to_context,
    prefer_external_input_context,
    preview_error_is_fatal,
    right_option_transition,
    run_audio_smoke_test,
    start_optional_escape_monitor,
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

    def test_native_audio_errors_are_concise(self):
        self.assertEqual(
            concise_error_message(
                OSError("cannot load _soundfile_data/libsndfile_arm64.dylib")
            ),
            "音频编码组件加载失败，请更新应用",
        )

    def test_asr_quota_error_is_concise_and_stops_preview(self):
        error = RuntimeError("429 code 1113 余额不足或无可用资源包")
        self.assertEqual(
            concise_error_message(error),
            "语音识别服务额度不足，请检查服务账户或更换模型服务",
        )
        self.assertTrue(preview_error_is_fatal(error))

    def test_temporary_network_error_does_not_stop_preview_immediately(self):
        self.assertFalse(preview_error_is_fatal(TimeoutError("timed out")))

    def test_missing_accessibility_does_not_abort_startup(self):
        def unavailable():
            raise RuntimeError("permission missing")

        self.assertFalse(start_optional_escape_monitor(unavailable))

    def test_available_escape_monitor_reports_ready(self):
        self.assertTrue(start_optional_escape_monitor(lambda: None))

    def test_audio_smoke_test_loads_device_and_stream(self):
        events = []

        class Stream:
            def start(self):
                events.append("start")

            def stop(self):
                events.append("stop")

            def close(self):
                events.append("close")

        fake_sounddevice = SimpleNamespace(
            query_devices=lambda kind: {
                "name": "Test Microphone",
                "default_samplerate": 48_000,
                "max_input_channels": 1,
            },
            InputStream=lambda **_kwargs: Stream(),
        )
        fake_soundfile = SimpleNamespace(
            write=lambda *_args, **_kwargs: None,
            read=lambda *_args, **_kwargs: ([0.0] * 320, 16_000),
        )
        fake_numpy = SimpleNamespace(
            zeros=lambda *_args, **_kwargs: [0.0] * 320
        )
        with patch("voice_input.time.sleep"):
            self.assertEqual(
                run_audio_smoke_test(
                    open_stream=True,
                    sounddevice_module=fake_sounddevice,
                    soundfile_module=fake_soundfile,
                    numpy_module=fake_numpy,
                ),
                0,
            )
        self.assertEqual(events, ["start", "stop", "close"])

    def test_audio_smoke_test_reports_load_failure(self):
        fake_sounddevice = SimpleNamespace(
            query_devices=lambda kind: (_ for _ in ()).throw(
                OSError("missing PortAudio")
            )
        )
        fake_soundfile = SimpleNamespace()
        fake_numpy = SimpleNamespace()
        self.assertEqual(
            run_audio_smoke_test(
                sounddevice_module=fake_sounddevice,
                soundfile_module=fake_soundfile,
                numpy_module=fake_numpy,
            ),
            1,
        )


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
