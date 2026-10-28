import asyncio
import html
import re
import time

import httpx

from app.config import Settings


class YouTubeError(RuntimeError):
    pass


class YouTubeUnavailableError(YouTubeError):
    """Raised when YouTube cannot be reached after one retry."""

_VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")

# search.list costs 100 quota units per call (default budget is 10,000/day),
# so identical searches are served from memory for a few minutes.
_SEARCH_TTL_SECONDS = 600
_SEARCH_CACHE: dict[tuple[str, int], tuple[float, list[dict[str, str]]]] = {}

_ISO_DURATION = re.compile(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")
_IDENTIFY_CACHE: dict[tuple[str, str], dict] = {}


def parse_iso_duration(value: str) -> int | None:
    match = _ISO_DURATION.fullmatch(value or "")
    if not match:
        return None
    hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", html.unescape(text or "").lower()).strip()


def extract_video_id(url: str) -> str:
    # Extracts and validates an 11-character YouTube video identifier.
    candidate = url.strip()
    if _VIDEO_ID_PATTERN.fullmatch(candidate):
        return candidate
    patterns = (
        r"(?:v=|/shorts/|/embed/|youtu\.be/)([A-Za-z0-9_-]{11})",
    )
    for pattern in patterns:
        match = re.search(pattern, candidate)
        if match:
            return match.group(1)
    raise ValueError("Could not find an 11-character YouTube video ID in the URL")


class YouTubeClient:
    # Retrieves video metadata through the official YouTube Data API.
    def __init__(self, settings: Settings):
        self.settings = settings
        connect = getattr(settings, "youtube_connect_timeout_seconds", 4.0)
        read = getattr(settings, "youtube_read_timeout_seconds", 8.0)
        pool = getattr(settings, "youtube_pool_timeout_seconds", 4.0)
        self.timeout = httpx.Timeout(connect=connect, read=read, write=read, pool=pool)
        self.identify_timings = {"youtube_search": 0.0, "video_metadata": 0.0}
        self._metadata_cache: dict[str, dict[str, str]] = {}

    async def _get_with_retry(self, client, url: str, params: dict) -> httpx.Response:
        for attempt in range(2):
            try:
                return await client.get(url, params=params)
            except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as exc:
                if attempt == 0:
                    await asyncio.sleep(0.5)
                    continue
                raise YouTubeUnavailableError("YouTube lookup failed after one retry") from exc
        raise AssertionError("unreachable")

    async def get_metadata(self, video_id: str) -> dict[str, str]:
        # Fetches title, description, channel name, and channel identifier for a video.
        cached = self._metadata_cache.get(video_id)
        if cached is not None:
            return dict(cached)
        if not self.settings.youtube_api_key:
            raise YouTubeError("YOUTUBE_API_KEY is not configured")
        params = {
            "part": "snippet,contentDetails,status",
            "id": video_id,
            "key": self.settings.youtube_api_key,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await self._get_with_retry(
                client, "https://www.googleapis.com/youtube/v3/videos", params
            )
        if response.status_code >= 400:
            raise YouTubeError(f"YouTube API returned HTTP {response.status_code}")
        items = response.json().get("items", [])
        if not items:
            raise YouTubeError("YouTube video was not found")
        snippet = items[0].get("snippet", {})
        content_details = items[0].get("contentDetails")
        status = items[0].get("status")
        content_rating = (content_details or {}).get("contentRating", {})
        age_restricted = (
            "unknown"
            if content_details is None or "ytRating" not in content_rating
            else "true" if content_rating["ytRating"] == "ytAgeRestricted" else "false"
        )
        made_for_kids = (
            "unknown"
            if status is None or "madeForKids" not in status
            else "true" if status["madeForKids"] is True else "false"
        )
        live = snippet.get("liveBroadcastContent", "unknown")
        duration = parse_iso_duration((content_details or {}).get("duration", ""))
        metadata = {
            "title": snippet.get("title", ""),
            "description": snippet.get("description", ""),
            "channel": snippet.get("channelTitle", ""),
            "channel_id": snippet.get("channelId", ""),
            "age_restricted": age_restricted,
            "made_for_kids": made_for_kids,
            "live": live if live in {"live", "upcoming", "none"} else "unknown",
            "duration_seconds": str(duration) if duration is not None else "",
        }
        self._metadata_cache[video_id] = metadata
        return dict(metadata)

    async def search_videos(self, query: str, max_results: int = 8) -> list[dict[str, str]]:
        # Searches embeddable videos. safeSearch is only a cheap first filter;
        # the family/child policy still decides via /classify when a result is tapped.
        if not self.settings.youtube_api_key:
            raise YouTubeError("YOUTUBE_API_KEY is not configured")
        cache_key = (query.strip().lower(), max_results)
        cached = _SEARCH_CACHE.get(cache_key)
        if cached and time.monotonic() - cached[0] < _SEARCH_TTL_SECONDS:
            return cached[1]
        params = {
            "part": "snippet",
            "q": query,
            "type": "video",
            "videoEmbeddable": "true",
            "safeSearch": "strict",
            "maxResults": max_results,
            "key": self.settings.youtube_api_key,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await self._get_with_retry(
                client, "https://www.googleapis.com/youtube/v3/search", params
            )
        if response.status_code >= 400:
            raise YouTubeError(f"YouTube search returned HTTP {response.status_code}")
        results = []
        for item in response.json().get("items", []):
            video_id = item.get("id", {}).get("videoId")
            if not video_id:
                continue
            snippet = item.get("snippet", {})
            thumbs = snippet.get("thumbnails", {})
            thumb = (thumbs.get("medium") or thumbs.get("default") or {}).get("url", "")
            results.append(
                {
                    "video_id": video_id,
                    "title": html.unescape(snippet.get("title", "")),
                    "channel": html.unescape(snippet.get("channelTitle", "")),
                    "channel_id": snippet.get("channelId", ""),
                    "thumbnail": thumb,
                }
            )
        _SEARCH_CACHE[cache_key] = (time.monotonic(), results)
        return results
    
    async def identify_video(
        self, title: str, channel: str, duration_seconds: int | None = None
    ) -> dict | None:
        # Finds the video ID for a title/channel seen on screen. Deliberately
        # unfiltered (no safeSearch/embeddable): the video is already playing,
        # so hiding it from the match would defeat the point.
        if not self.settings.youtube_api_key:
            raise YouTubeError("YOUTUBE_API_KEY is not configured")
        key = (_norm(title), _norm(channel))
        self.identify_timings = {"youtube_search": 0.0, "video_metadata": 0.0}
        if key in _IDENTIFY_CACHE:
            return _IDENTIFY_CACHE[key]
        search_params = {
            "part": "snippet",
            "q": f"{title} {channel}",
            "type": "video",
            "maxResults": 5,
            "key": self.settings.youtube_api_key,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            started = time.perf_counter()
            try:
                search = await self._get_with_retry(
                    client, "https://www.googleapis.com/youtube/v3/search", search_params
                )
            finally:
                self.identify_timings["youtube_search"] = time.perf_counter() - started
            if search.status_code >= 400:
                raise YouTubeError(f"YouTube search returned HTTP {search.status_code}")
            candidates = [
                item["id"]["videoId"]
                for item in search.json().get("items", [])
                if item.get("id", {}).get("videoId")
                and _norm(item["snippet"].get("title", "")) == key[0]
                and _norm(item["snippet"].get("channelTitle", "")) == key[1]
            ]
            if not candidates:
                return None
            started = time.perf_counter()
            try:
                details = await self._get_with_retry(
                    client,
                    "https://www.googleapis.com/youtube/v3/videos",
                    {
                        "part": "contentDetails",
                        "id": ",".join(candidates),
                        "key": self.settings.youtube_api_key,
                    },
                )
            finally:
                self.identify_timings["video_metadata"] = time.perf_counter() - started
        if details.status_code >= 400:
            raise YouTubeError(f"YouTube API returned HTTP {details.status_code}")
        durations = {
            item["id"]: parse_iso_duration(item.get("contentDetails", {}).get("duration", ""))
            for item in details.json().get("items", [])
        }
        match = None
        if duration_seconds:
            for vid in candidates:
                seconds = durations.get(vid)
                if seconds is not None and abs(seconds - duration_seconds) <= 2:
                    match = {"video_id": vid, "confidence": "high"}
                    break
        if match is None and len(candidates) == 1:
            match = {"video_id": candidates[0], "confidence": "medium"}
        if match is not None:
            _IDENTIFY_CACHE[key] = match
        return match
