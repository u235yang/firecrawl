import importlib.util
import logging
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


ENGINE_PATH = (
    Path(__file__).parents[2]
    / "infra"
    / "docker"
    / "searxng"
    / "engines"
    / "exa.py"
)


def load_engine():
    searx_module = types.ModuleType("searx")
    utils_module = types.ModuleType("searx.utils")
    utils_module.html_to_text = lambda value: value
    searx_module.utils = utils_module

    with patch.dict(
        sys.modules,
        {"searx": searx_module, "searx.utils": utils_module},
    ):
        spec = importlib.util.spec_from_file_location("test_exa_engine", ENGINE_PATH)
        module = importlib.util.module_from_spec(spec)
        module.logger = logging.getLogger("test.searxng.exa")
        spec.loader.exec_module(module)
        return module


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class ExaEngineTest(unittest.TestCase):
    def test_setup_requires_environment_secret(self):
        engine = load_engine()

        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(engine.setup({"api_key_env": "EXA_API_KEY"}))

    def test_request_reads_secret_from_environment_only(self):
        engine = load_engine()

        with patch.dict(os.environ, {"EXA_API_KEY": "test-secret"}, clear=True):
            self.assertTrue(engine.setup({"api_key_env": "EXA_API_KEY"}))

        params = {"headers": {}}
        engine.request("firecrawl", params)

        self.assertEqual(params["url"], "https://api.exa.ai/search")
        self.assertEqual(params["method"], "POST")
        self.assertEqual(params["headers"]["x-api-key"], "test-secret")
        self.assertNotIn("test-secret", params["url"])
        self.assertNotIn("test-secret", str(params["json"]))
        self.assertEqual(params["json"]["query"], "firecrawl")

    def test_response_maps_valid_results_and_skips_incomplete_items(self):
        engine = load_engine()
        response = FakeResponse(
            {
                "results": [
                    {
                        "url": "https://example.com/result",
                        "title": "Example",
                        "text": "Result text",
                    },
                    {"title": "Missing URL"},
                ]
            }
        )

        self.assertEqual(
            engine.response(response),
            [
                {
                    "url": "https://example.com/result",
                    "title": "Example",
                    "content": "Result text",
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
