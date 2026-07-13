import asyncio
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import aiohttp

from .models import game_names_match, normalize_game_name


class MetadataProviderError(RuntimeError):
    pass


class ProviderHub:
    POSITIVE_TTL = 7 * 24 * 60 * 60
    NEGATIVE_TTL = 60 * 60

    def __init__(self, bot, session: aiohttp.ClientSession):
        self.bot = bot
        self.session = session
        self._cache: Dict[str, tuple[float, Any]] = {}
        self._twitch_token: Optional[str] = None
        self._twitch_token_expiry = 0.0

    def _cached(self, key: str):
        cached = self._cache.get(key)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        self._cache.pop(key, None)
        return None

    def _store(self, key: str, value: Any) -> Any:
        ttl = self.POSITIVE_TTL if value else self.NEGATIVE_TTL
        self._cache[key] = (time.monotonic() + ttl, value)
        return value

    def invalidate(self, service: Optional[str] = None) -> None:
        if service in (None, "twitch"):
            self._twitch_token = None
            self._twitch_token_expiry = 0.0
            self._cache = {
                key: value for key, value in self._cache.items() if not key.startswith("igdb:")
            }
        if service in (None, "steamgriddb"):
            self._cache = {
                key: value
                for key, value in self._cache.items()
                if not key.startswith("steamgriddb:")
            }

    async def _twitch_credentials(self) -> tuple[str, str]:
        tokens = await self.bot.get_shared_api_tokens("twitch")
        client_id = tokens.get("client_id")
        client_secret = tokens.get("client_secret")
        if not client_id or not client_secret:
            raise MetadataProviderError("IGDB credentials are not configured.")
        return client_id, client_secret

    async def _get_twitch_token(self) -> tuple[str, str]:
        client_id, client_secret = await self._twitch_credentials()
        if self._twitch_token and self._twitch_token_expiry > time.monotonic() + 60:
            return client_id, self._twitch_token
        try:
            async with self.session.post(
                "https://id.twitch.tv/oauth2/token",
                params={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "grant_type": "client_credentials",
                },
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status != 200:
                    raise MetadataProviderError(
                        f"Twitch authentication failed ({response.status})."
                    )
                try:
                    data = await response.json()
                except (TypeError, ValueError) as exc:
                    raise MetadataProviderError(
                        "Twitch authentication returned an invalid response."
                    ) from exc
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise MetadataProviderError("Twitch authentication failed.") from exc
        if not isinstance(data, dict) or not data.get("access_token"):
            raise MetadataProviderError("Twitch authentication returned an invalid response.")
        self._twitch_token = data["access_token"]
        self._twitch_token_expiry = time.monotonic() + int(data.get("expires_in", 3600))
        return client_id, self._twitch_token

    async def search_igdb(self, query: str) -> List[Dict[str, Any]]:
        cache_key = f"igdb:{normalize_game_name(query)}"
        cached = self._cached(cache_key)
        if cached is not None:
            return cached
        client_id, token = await self._get_twitch_token()
        safe_query = query.replace("\\", "").replace('"', "")[:100]
        body = (
            "fields id,name,summary,url,cover.url,alternative_names.name; "
            f'search "{safe_query}"; where version_parent = null; limit 10;'
        )
        try:
            async with self.session.post(
                "https://api.igdb.com/v4/games",
                headers={"Client-ID": client_id, "Authorization": f"Bearer {token}"},
                data=body,
                timeout=aiohttp.ClientTimeout(total=12),
            ) as response:
                if response.status == 429:
                    raise MetadataProviderError("IGDB is rate limited; try again shortly.")
                if response.status != 200:
                    raise MetadataProviderError(f"IGDB lookup failed ({response.status}).")
                try:
                    payload = await response.json()
                except (TypeError, ValueError) as exc:
                    raise MetadataProviderError("IGDB returned an invalid response.") from exc
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            self._store(cache_key, [])
            raise MetadataProviderError("IGDB lookup timed out.") from exc

        if not isinstance(payload, list):
            self._store(cache_key, [])
            raise MetadataProviderError("IGDB returned an invalid response.")

        results = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            cover = (item.get("cover") or {}).get("url") or ""
            if cover.startswith("//"):
                cover = "https:" + cover
            cover = cover.replace("t_thumb", "t_cover_big")
            aliases = [entry.get("name") for entry in item.get("alternative_names", [])]
            results.append(
                {
                    "id": item.get("id"),
                    "name": item.get("name") or "",
                    "aliases": [alias for alias in aliases if alias],
                    "description": item.get("summary") or "",
                    "image_url": cover,
                    "url": item.get("url") or "",
                    "source": "IGDB",
                }
            )
        return self._store(cache_key, results)

    async def exact_igdb(self, query: str) -> Optional[Dict[str, Any]]:
        for item in await self.search_igdb(query):
            if game_names_match(query, [item["name"], *item.get("aliases", [])]):
                return item
        return None

    async def steamgriddb_art(self, query: str) -> Optional[str]:
        cache_key = f"steamgriddb:{normalize_game_name(query)}"
        cached = self._cached(cache_key)
        if cached is not None:
            return cached or None
        tokens = await self.bot.get_shared_api_tokens("steamgriddb")
        api_key = tokens.get("api_key")
        if not api_key:
            raise MetadataProviderError("SteamGridDB credentials are not configured.")
        headers = {"Authorization": f"Bearer {api_key}"}
        try:
            async with self.session.get(
                f"https://www.steamgriddb.com/api/v2/search/autocomplete/{quote(query, safe='')}",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status != 200:
                    raise MetadataProviderError(f"SteamGridDB search failed ({response.status}).")
                try:
                    search_payload = await response.json()
                except (TypeError, ValueError) as exc:
                    raise MetadataProviderError(
                        "SteamGridDB search returned an invalid response."
                    ) from exc
                if not isinstance(search_payload, dict) or not isinstance(
                    search_payload.get("data", []), list
                ):
                    raise MetadataProviderError("SteamGridDB search returned an invalid response.")
                games = search_payload.get("data", [])
            game = next(
                (item for item in games if game_names_match(query, [item.get("name")])), None
            )
            if not game:
                return self._store(cache_key, "") or None
            async with self.session.get(
                f"https://www.steamgriddb.com/api/v2/heroes/game/{game['id']}",
                headers=headers,
                params={
                    "types": "static",
                    "nsfw": "false",
                    "humor": "false",
                    "epilepsy": "false",
                    "limit": 1,
                },
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status != 200:
                    return self._store(cache_key, "") or None
                try:
                    art_payload = await response.json()
                except (TypeError, ValueError) as exc:
                    raise MetadataProviderError(
                        "SteamGridDB artwork returned an invalid response."
                    ) from exc
                if not isinstance(art_payload, dict) or not isinstance(
                    art_payload.get("data", []), list
                ):
                    raise MetadataProviderError(
                        "SteamGridDB artwork returned an invalid response."
                    )
                art = art_payload.get("data", [])
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            self._store(cache_key, "")
            raise MetadataProviderError("SteamGridDB lookup timed out.") from exc
        return self._store(cache_key, art[0].get("url", "") if art else "") or None
