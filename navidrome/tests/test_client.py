from types import SimpleNamespace

import pytest

from navidrome.client import NavidromeClient, validate_base_url


def test_validate_base_url_requires_https_by_default():
    assert validate_base_url("https://music.example.com/") == "https://music.example.com"
    with pytest.raises(ValueError):
        validate_base_url("http://music.example.com")


def test_validate_base_url_allows_explicit_http_but_never_embedded_credentials():
    assert validate_base_url("http://192.168.1.5:4533", allow_http=True) == "http://192.168.1.5:4533"
    with pytest.raises(ValueError):
        validate_base_url("https://admin:secret@music.example.com")


def test_subsonic_auth_uses_salted_token_not_plaintext_password():
    client = NavidromeClient(SimpleNamespace(), "https://music.example.com", "bot", "top-secret")
    params = client._auth_params()
    assert params["u"] == "bot"
    assert params["t"]
    assert params["s"]
    assert "p" not in params
    assert "top-secret" not in params.values()


@pytest.mark.asyncio
async def test_missing_cover_art_never_builds_an_external_authenticated_url():
    client = NavidromeClient(SimpleNamespace(), "https://music.example.com", "bot", "secret")
    assert await client.cover_art(None) is None
