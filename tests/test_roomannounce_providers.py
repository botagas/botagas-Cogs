import asyncio

import pytest

from roomannounce.providers import MetadataProviderError, ProviderHub


class FakeBot:
    def __init__(self, services):
        self.services = services

    async def get_shared_api_tokens(self, service):
        return self.services.get(service, {})


class FakeResponse:
    def __init__(self, status, payload):
        self.status = status
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self):
        return self.payload


class InvalidJsonResponse(FakeResponse):
    async def json(self):
        raise ValueError("invalid JSON")


class FakeSession:
    def __init__(self, posts=None, gets=None):
        self.posts = list(posts or [])
        self.gets = list(gets or [])
        self.get_calls = []

    def post(self, url, **kwargs):
        return self.posts.pop(0)

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return self.gets.pop(0)


def test_igdb_exact_match_normalizes_cover_and_caches():
    session = FakeSession(
        posts=[
            FakeResponse(200, {"access_token": "token", "expires_in": 3600}),
            FakeResponse(
                200,
                [
                    {
                        "id": 1,
                        "name": "Halo Infinite",
                        "summary": "Master Chief returns.",
                        "url": "https://www.igdb.com/games/halo-infinite",
                        "cover": {"url": "//images.example/t_thumb/halo.jpg"},
                        "alternative_names": [{"name": "Halo 6"}],
                    }
                ],
            ),
        ]
    )
    hub = ProviderHub(FakeBot({"twitch": {"client_id": "id", "client_secret": "secret"}}), session)
    result = asyncio.run(hub.exact_igdb("Halo Infinite"))
    assert result["name"] == "Halo Infinite"
    assert result["image_url"] == "https://images.example/t_cover_big/halo.jpg"
    assert result["aliases"] == ["Halo 6"]

    cached = asyncio.run(hub.exact_igdb("Halo Infinite"))
    assert cached == result
    assert not session.posts


def test_igdb_missing_credentials_is_recoverable():
    hub = ProviderHub(FakeBot({}), FakeSession())
    with pytest.raises(MetadataProviderError, match="not configured"):
        asyncio.run(hub.search_igdb("Portal 2"))


def test_igdb_invalid_response_is_recoverable():
    session = FakeSession(
        posts=[
            FakeResponse(200, {"access_token": "token", "expires_in": 3600}),
            InvalidJsonResponse(200, None),
        ]
    )
    hub = ProviderHub(FakeBot({"twitch": {"client_id": "id", "client_secret": "secret"}}), session)
    with pytest.raises(MetadataProviderError, match="invalid response"):
        asyncio.run(hub.search_igdb("Portal 2"))


def test_steamgriddb_filters_unsafe_artwork():
    session = FakeSession(
        gets=[
            FakeResponse(200, {"data": [{"id": 10, "name": "Deep Rock Galactic"}]}),
            FakeResponse(200, {"data": [{"url": "https://images.example/drg.jpg"}]}),
        ]
    )
    hub = ProviderHub(FakeBot({"steamgriddb": {"api_key": "key"}}), session)
    result = asyncio.run(hub.steamgriddb_art("Deep Rock Galactic"))
    assert result == "https://images.example/drg.jpg"
    _, kwargs = session.get_calls[1]
    assert kwargs["params"]["nsfw"] == "false"
    assert kwargs["params"]["humor"] == "false"
    assert kwargs["params"]["epilepsy"] == "false"


def test_token_update_invalidation_keeps_other_provider_cache():
    hub = ProviderHub(FakeBot({}), FakeSession())
    hub._cache = {
        "igdb:halo": (float("inf"), {"name": "Halo"}),
        "steamgriddb:halo": (float("inf"), "https://example.com/halo.jpg"),
    }
    hub._twitch_token = "old-token"
    hub.invalidate("twitch")
    assert hub._twitch_token is None
    assert "igdb:halo" not in hub._cache
    assert "steamgriddb:halo" in hub._cache
