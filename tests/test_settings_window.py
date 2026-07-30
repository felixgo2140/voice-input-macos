import unittest

from settings_window import SERVICE_CATALOG, service_choices


class ServiceChoicesTests(unittest.TestCase):
    def test_catalog_entries_are_complete(self):
        for section_name in ("asr", "llm"):
            choices = service_choices(section_name)
            self.assertTrue(choices)
            for choice in choices:
                self.assertTrue(choice["provider"])
                self.assertTrue(choice["base_url"].startswith("https://"))
                self.assertTrue(choice["models"])

    def test_current_model_is_preserved_as_a_selection(self):
        choices = service_choices(
            "llm",
            {
                "provider": "DeepSeek",
                "base_url": "https://api.deepseek.com",
                "model": "future-model",
            },
        )
        deepseek = next(
            choice for choice in choices
            if choice["provider"] == "DeepSeek"
        )
        self.assertEqual(deepseek["models"][0], "future-model")
        self.assertIn("deepseek-chat", deepseek["models"])

    def test_legacy_provider_is_preserved_as_a_selection(self):
        choices = service_choices(
            "asr",
            {
                "provider": "公司内网 ASR",
                "base_url": "https://speech.example.test/v1/",
                "model": "company-asr",
            },
        )
        legacy = choices[-1]
        self.assertEqual(legacy["provider"], "公司内网 ASR")
        self.assertEqual(
            legacy["base_url"],
            "https://speech.example.test/v1",
        )
        self.assertEqual(legacy["models"], ("company-asr",))

    def test_unknown_section_is_rejected(self):
        with self.assertRaises(ValueError):
            service_choices("embedding")

    def test_choices_do_not_mutate_the_catalog(self):
        service_choices(
            "llm",
            {
                "provider": "DeepSeek",
                "model": "temporary-model",
            },
        )
        self.assertNotIn(
            "temporary-model",
            SERVICE_CATALOG["llm"][0]["models"],
        )


if __name__ == "__main__":
    unittest.main()
