# SPDX-License-Identifier: AGPL-3.0-or-later
"""SearXNG engine for the Exa Search API.

The credential is read from the environment variable named by
``api_key_env`` in ``settings.yml``.  It is never stored in the YAML file.
"""

from __future__ import annotations

import os
import typing as t

from searx.utils import html_to_text

if t.TYPE_CHECKING:
    from searx.extended_types import SXNG_Response
    from searx.search.processors import OnlineParams


about = {
    "website": "https://exa.ai/",
    "wikidata_id": None,
    "official_api_documentation": "https://docs.exa.ai/reference/search",
    "use_official_api": True,
    "require_api_key": True,
    "results": "JSON",
}

categories = ["general", "web"]
paging = False
safesearch = False
time_range_support = False

base_url = "https://api.exa.ai/search"
api_key_env = "EXA_API_KEY"
results_per_page = 10
text_max_characters = 1500

_api_key = ""


def setup(engine_settings: dict[str, t.Any]) -> bool:
    """Load the API key from the configured environment variable."""

    global _api_key  # pylint: disable=global-statement

    env_name = str(engine_settings.get("api_key_env", api_key_env)).strip()
    if not env_name:
        logger.error("Exa API key environment variable name is not configured.")
        return False

    _api_key = os.environ.get(env_name, "").strip()
    if not _api_key:
        logger.error("Exa API key environment variable is not set.")
        return False

    return True


def request(query: str, params: "OnlineParams") -> None:
    """Build an authenticated Exa Search API request."""

    params["url"] = base_url
    params["method"] = "POST"
    params["headers"]["x-api-key"] = _api_key
    params["headers"]["Content-Type"] = "application/json"
    params["json"] = {
        "query": query,
        "numResults": results_per_page,
        "contents": {
            "text": {
                "maxCharacters": text_max_characters,
            }
        },
    }


def response(resp: "SXNG_Response") -> list[dict[str, t.Any]]:
    """Convert Exa results to SearXNG's main-result shape."""

    data = resp.json()
    results: list[dict[str, t.Any]] = []

    for item in data.get("results", []):
        url = item.get("url")
        title = item.get("title")
        if not url or not title:
            continue

        content = item.get("text") or item.get("summary") or ""
        results.append(
            {
                "url": url,
                "title": html_to_text(str(title)),
                "content": html_to_text(str(content)),
            }
        )

    return results
