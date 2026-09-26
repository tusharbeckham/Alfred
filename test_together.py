#!/usr/bin/env python3
"""Tests for together_cli module."""

import json
import time
import unittest
from unittest.mock import MagicMock, patch

import together_cli


class TestTogetherModelRegistry(unittest.TestCase):
    """Test model registry and fallback lists."""

    def test_curated_text_models_present(self):
        expected_text_models = [
            "zai-org/GLM-5.3",
            "zai-org/GLM-5.3-Flash",
            "zai-org/GLM-5.2",
            "moonshotai/Kimi-K3",
            "MiniMaxAI/MiniMax-M3",
            "thinkingmachines/Inkling",
            "deepseek-ai/DeepSeek-V4.1-Flash",
            "deepseek-ai/DeepSeek-V4-Pro-0813",
            "deepseek-ai/DeepSeek-V4-Flash-0731",
            "deepseek-ai/DeepSeek-V4-Pro",
            "meta-models/Muse-Glimmer-30B",
            "Qwen/Qwen3.8-2.4T-A95B",
            "Qwen/Qwen3.8-Flash",
            "Qwen/Qwen3.7-Plus",
            "google/gemma-4-31B-it",
            "nvidia/nemotron-3-ultra-550b-a55b",
        ]
        for m in expected_text_models:
            self.assertIn(m, together_cli.FALLBACK_TEXT_MODELS)

    def test_curated_image_models_present(self):
        expected_image_models = [
            "black-forest-labs/FLUX.2-flex",
            "black-forest-labs/FLUX.2-pro",
            "black-forest-labs/FLUX.2-dev",
            "black-forest-labs/FLUX.1-pro",
            "black-forest-labs/FLUX-Kontext-pro",
            "google/nano-banana-pro",
            "google/nano-banana",
        ]
        for m in expected_image_models:
            self.assertIn(m, together_cli.FALLBACK_IMAGE_MODELS)


class TestModelListingAndCache(unittest.TestCase):
    """Test live model fetching, 1-hour cache, and fallback."""

    def setUp(self):
        # Reset cache before tests
        together_cli._models_cache = []
        together_cli._models_cache_timestamp = 0.0

    @patch("together_cli.get_api_key", return_value="fake_key")
    @patch("together_cli.requests.get")
    def test_list_models_live_success_and_cache(self, mock_get, mock_key):
        fake_api_response = MagicMock()
        fake_api_response.status_code = 200
        fake_api_response.json.return_value = {
            "data": [
                {"id": "test-org/model-alpha"},
                {"id": "test-org/model-beta"},
            ]
        }
        mock_get.return_value = fake_api_response

        # First call fetches from API
        models = together_cli.list_models()
        self.assertEqual(models, ["test-org/model-alpha", "test-org/model-beta"])
        self.assertEqual(mock_get.call_count, 1)

        # Second call within 1 hour should use cache
        models2 = together_cli.list_models()
        self.assertEqual(models2, ["test-org/model-alpha", "test-org/model-beta"])
        self.assertEqual(mock_get.call_count, 1)  # Not called again

        # Force refresh should bypass cache
        models3 = together_cli.list_models(force_refresh=True)
        self.assertEqual(models3, ["test-org/model-alpha", "test-org/model-beta"])
        self.assertEqual(mock_get.call_count, 2)

    @patch("together_cli.get_api_key", return_value="")
    def test_list_models_fallback_when_no_key(self, mock_key):
        models = together_cli.list_models()
        # Should fallback to curated list
        for m in together_cli.FALLBACK_TEXT_MODELS:
            self.assertIn(m, models)
        for m in together_cli.FALLBACK_IMAGE_MODELS:
            self.assertIn(m, models)

    @patch("together_cli.get_api_key", return_value="fake_key")
    @patch("together_cli.requests.get", side_effect=Exception("Connection error"))
    def test_list_models_fallback_on_network_error(self, mock_get, mock_key):
        models = together_cli.list_models()
        self.assertTrue(len(models) > 0)
        self.assertIn("deepseek-ai/DeepSeek-V4.1-Flash", models)

    def test_group_models_by_family(self):
        sample = [
            "deepseek-ai/DeepSeek-V4.1-Flash",
            "deepseek-ai/DeepSeek-V4-Pro",
            "Qwen/Qwen3.8-Flash",
            "standalone-model",
        ]
        grouped = together_cli.group_models_by_family(sample)
        self.assertIn("deepseek-ai", grouped)
        self.assertIn("Qwen", grouped)
        self.assertIn("other", grouped)
        self.assertEqual(len(grouped["deepseek-ai"]), 2)
        self.assertEqual(grouped["other"], ["standalone-model"])


class TestChatCompletion(unittest.TestCase):
    """Test chat() function with mocking and error handling."""

    @patch("together_cli.get_api_key", return_value="")
    def test_chat_without_api_key(self, mock_key):
        res = together_cli.chat("Hello")
        self.assertFalse(res["ok"])
        self.assertIn("TOGETHER_API_KEY is not set", res["error"])

    @patch("together_cli.get_api_key", return_value="test_key")
    @patch("together_cli.requests.post")
    def test_chat_success(self, mock_post, mock_key):
        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = {
            "model": "deepseek-ai/DeepSeek-V4.1-Flash",
            "choices": [
                {"message": {"role": "assistant", "content": "Hello! How can I help you today?"}}
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 12},
        }
        fake_resp.text = json.dumps(fake_resp.json.return_value)
        mock_post.return_value = fake_resp

        res = together_cli.chat("Hi", model="deepseek-ai/DeepSeek-V4.1-Flash")
        self.assertTrue(res["ok"])
        self.assertEqual(res["text"], "Hello! How can I help you today?")
        self.assertEqual(res["model"], "deepseek-ai/DeepSeek-V4.1-Flash")
        self.assertEqual(res["usage"]["prompt_tokens"], 10)

    @patch("together_cli.get_api_key", return_value="test_key")
    @patch("together_cli.requests.post")
    def test_chat_model_404_handled_gracefully(self, mock_post, mock_key):
        fake_resp = MagicMock()
        fake_resp.status_code = 404
        fake_resp.json.return_value = {"error": {"message": "model not found"}}
        fake_resp.text = "model not found"
        mock_post.return_value = fake_resp

        res = together_cli.chat("Hi", model="nonexistent/model")
        self.assertFalse(res["ok"])
        self.assertTrue(res.get("not_found"))
        self.assertIn("404", res["error"])


class TestImageGeneration(unittest.TestCase):
    """Test generate_image() targeting /v1/images/generations."""

    @patch("together_cli.get_api_key", return_value="")
    def test_generate_image_without_api_key(self, mock_key):
        res = together_cli.generate_image("A futuristic city")
        self.assertFalse(res["ok"])
        self.assertIn("TOGETHER_API_KEY is not set", res["error"])

    @patch("together_cli.get_api_key", return_value="test_key")
    @patch("together_cli.requests.post")
    def test_generate_image_success(self, mock_post, mock_key):
        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = {
            "data": [{"url": "https://api.together.xyz/images/sample123.png"}]
        }
        fake_resp.text = json.dumps(fake_resp.json.return_value)
        mock_post.return_value = fake_resp

        res = together_cli.generate_image("A cute cat", model="black-forest-labs/FLUX.2-pro")
        self.assertTrue(res["ok"])
        self.assertEqual(res["url"], "https://api.together.xyz/images/sample123.png")

        # Verify endpoint called
        called_url = mock_post.call_args[0][0]
        self.assertTrue(called_url.endswith("/images/generations"))


class TestConfigManagement(unittest.TestCase):
    """Test loading and saving configuration."""

    def test_save_and_load_config(self):
        together_cli.save_config({"default_model": "Qwen/Qwen3.8-Flash", "temperature": 0.5})
        cfg = together_cli.load_config()
        self.assertEqual(cfg.get("default_model"), "Qwen/Qwen3.8-Flash")
        self.assertEqual(cfg.get("temperature"), 0.5)


if __name__ == "__main__":
    unittest.main()
