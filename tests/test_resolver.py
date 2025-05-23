"""Unit tests for ttl_pref_ls.resolver."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Dict

import pytest

from ttl_pref_ls import resolver

SAMPLE_TTL = """
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
<http://example.com/ns#Foo> skos:prefLabel "Foo"@en .
<http://example.com/ns#Bar> skos:prefLabel "Bar"@en .
"""


class _FakeResp:
    def __init__(self, status: int = 200, text: str = SAMPLE_TTL):
        self.status = status
        self._text = text

    async def text(self):
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeSession:
    def __init__(self, status: int = 200, text: str = SAMPLE_TTL):
        self._status = status
        self._text = text
        self.last_url: str | None = None
        self.last_hdrs: Dict[str, str] | None = None

    # In real aiohttp, `get` is sync and returns an *async* context manager.
    def get(self, url, *, headers=None, timeout=None):
        self.last_url = url
        self.last_hdrs = headers
        return _FakeResp(self._status, self._text)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_fetch_labels(monkeypatch):
    """_fetch_labels should parse remote Turtle and return prefLabel map."""

    def _fake_session_ctor(*args, **kwargs):
        return _FakeSession()

    # Monkey‑patch aiohttp module in resolver
    monkeypatch.setattr(
        resolver,
        "aiohttp",
        SimpleNamespace(ClientSession=_fake_session_ctor),
    )

    labels = await resolver._fetch_labels("http://example.com/ns")

    assert labels["http://example.com/ns#Foo"] == "Foo"
    assert labels["http://example.com/ns#Bar"] == "Bar"
