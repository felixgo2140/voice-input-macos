import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from voice_input_core import (
    ConfigStore,
    ScreenBounds,
    deep_fill_missing,
    detect_language_from_texts,
    is_meaningful_transcript,
    join_transcript_parts,
    normalize_output_mode,
    panel_origin_for_caret,
    resolve_output_language,
    sanitize_model_output,
)


class FakeSecretStore:
    def __init__(self):
        self.values = {}

    def get(self, account):
        return self.values.get(account, "")

    def set(self, account, value):
        self.values[account] = value


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "config.json"
        self.secrets = FakeSecretStore()
        self.store = ConfigStore(self.path, secret_store=self.secrets)

    def tearDown(self):
        self.temporary.cleanup()

    def test_creates_default_config(self):
        config = self.store.load()
        self.assertEqual(config["output"]["mode"], "auto")
        self.assertEqual(config["recording"]["max_seconds"], 600)
        self.assertTrue(self.path.exists())

    def test_config_file_is_private(self):
        self.store.load()
        mode = stat.S_IMODE(self.path.stat().st_mode)
        self.assertEqual(mode, 0o600)

    def test_fills_new_defaults_without_overwriting_choices(self):
        self.path.write_text(
            json.dumps({"output": {"mode": "English"}}),
            encoding="utf-8",
        )
        config = self.store.load()
        self.assertEqual(config["output"]["mode"], "English")
        self.assertEqual(config["ui"]["caret_gap"], 52)

    def test_updates_output_mode_atomically(self):
        config = self.store.set_output_mode("zh")
        self.assertEqual(config["output"]["mode"], "中文")
        stored = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(stored["output"]["mode"], "中文")

    def test_migrates_plaintext_key_to_private_store(self):
        legacy_secret = "legacy-" + "secret"
        self.path.write_text(
            json.dumps(
                {
                    "asr": {
                        "api_key": legacy_secret,
                        "keychain_account": "asr-api-key",
                    }
                }
            ),
            encoding="utf-8",
        )
        config = self.store.load()
        self.assertEqual(self.secrets.values["asr-api-key"], legacy_secret)
        self.assertEqual(config["asr"]["api_key"], "")
        self.assertNotIn(
            legacy_secret, self.path.read_text(encoding="utf-8")
        )

    def test_saves_new_credentials_without_plaintext(self):
        self.store.load()
        config = self.store.save_credentials(
            {"asr": {"model": "custom-asr"}},
            asr_secret="new-secret",
        )
        self.assertEqual(config["asr"]["model"], "custom-asr")
        self.assertEqual(self.secrets.values["asr-api-key"], "new-secret")
        self.assertNotIn("new-secret", self.path.read_text(encoding="utf-8"))

    def test_blank_secret_keeps_existing_credential(self):
        self.secrets.values["llm-api-key"] = "existing"
        self.store.save_credentials({}, llm_secret=None)
        self.assertEqual(self.secrets.values["llm-api-key"], "existing")

    def test_invalid_json_shape_is_rejected(self):
        self.path.write_text("[]", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.store.load()


class LanguageTests(unittest.TestCase):
    def test_detects_chinese_context_with_english_terms(self):
        self.assertEqual(
            detect_language_from_texts(["发送消息给 Felix about API"]), "zh"
        )

    def test_detects_english_placeholder(self):
        self.assertEqual(
            detect_language_from_texts(["Type a message"]), "en"
        )

    def test_empty_context_is_unknown(self):
        self.assertIsNone(detect_language_from_texts(["", None]))

    def test_auto_uses_detected_english(self):
        self.assertEqual(
            resolve_output_language(
                {"mode": "auto", "fallback": "中文"}, "en"
            ),
            "English",
        )

    def test_auto_uses_chinese_fallback(self):
        self.assertEqual(
            resolve_output_language(
                {"mode": "auto", "fallback": "中文"}, None
            ),
            "中文",
        )

    def test_fixed_mode_ignores_context(self):
        self.assertEqual(
            resolve_output_language({"mode": "中文"}, "en"), "中文"
        )

    def test_aliases_are_normalized(self):
        self.assertEqual(normalize_output_mode("英文"), "English")
        self.assertEqual(normalize_output_mode("zh"), "中文")

    def test_invalid_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            normalize_output_mode("French")


class OutputTests(unittest.TestCase):
    def test_removes_markdown_wrapper(self):
        self.assertEqual(sanitize_model_output("```text\n你好\n```"), "你好")

    def test_removes_common_prefix(self):
        self.assertEqual(sanitize_model_output("整理后：测试。"), "测试。")

    def test_removes_single_line_quotes(self):
        self.assertEqual(sanitize_model_output("“测试。”"), "测试。")

    def test_preserves_multiline_quotes(self):
        value = '"第一行\n第二行\n第三行"'
        self.assertEqual(sanitize_model_output(value), value)

    def test_stream_marker_is_not_meaningful(self):
        self.assertFalse(is_meaningful_transcript("#"))
        self.assertFalse(is_meaningful_transcript("..."))

    def test_words_are_meaningful(self):
        self.assertTrue(is_meaningful_transcript("测试"))
        self.assertTrue(is_meaningful_transcript("hello"))

    def test_joins_english_chunks_with_space(self):
        self.assertEqual(join_transcript_parts(["hello", "world"]), "hello world")

    def test_joins_chinese_chunks_without_space(self):
        self.assertEqual(join_transcript_parts(["你好", "世界"]), "你好世界")


class PositionTests(unittest.TestCase):
    def test_places_panel_below_caret_in_ax_coordinates(self):
        origin = panel_origin_for_caret(
            (300, 200, 2, 20),
            (340, 170),
            [ScreenBounds(0, 0, 1440, 900)],
            900,
            gap=52,
        )
        self.assertEqual(origin, (300, 458))

    def test_flips_to_other_side_near_bottom(self):
        origin = panel_origin_for_caret(
            (300, 850, 2, 20),
            (340, 170),
            [ScreenBounds(0, 0, 1440, 900)],
            900,
            gap=20,
        )
        self.assertEqual(origin, (300, 70))

    def test_clamps_to_secondary_screen(self):
        origin = panel_origin_for_caret(
            (2500, 200, 2, 20),
            (340, 170),
            [
                ScreenBounds(0, 0, 1440, 900),
                ScreenBounds(1440, 0, 1280, 800),
            ],
            900,
            gap=52,
        )
        self.assertEqual(origin[0], 2380)
        self.assertGreaterEqual(origin[1], 0)

    def test_empty_screens_uses_safe_fallback(self):
        x, y = panel_origin_for_caret(
            (10, 10, 2, 20), (340, 170), [], 900
        )
        self.assertGreaterEqual(x, 0)
        self.assertGreaterEqual(y, 0)


class HelperTests(unittest.TestCase):
    def test_deep_fill_preserves_existing_value(self):
        target = {"a": {"b": 1}}
        changed = deep_fill_missing(target, {"a": {"b": 2, "c": 3}})
        self.assertTrue(changed)
        self.assertEqual(target, {"a": {"b": 1, "c": 3}})

    def test_environment_key_can_be_used_without_config_secret(self):
        from voice_input_core import configured_api_key

        with patch.dict(os.environ, {"VOICE_INPUT_TEST_KEY": "from-env"}):
            self.assertEqual(
                configured_api_key(
                    {
                        "api_key_env": "VOICE_INPUT_TEST_KEY",
                        "keychain_account": "",
                        "api_key": "",
                    }
                ),
                "from-env",
            )


if __name__ == "__main__":
    unittest.main()
