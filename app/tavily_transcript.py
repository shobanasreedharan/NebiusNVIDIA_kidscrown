"""
Tavily-backed TranscriptProvider for KidsCrown.

Drop-in replacement for NullTranscriptProvider. ClassificationService.classify()
already wraps transcript_provider.get_transcript() in a try/except that handles
caching (transcript:{video_id}, transient-failure vs. confirmed-absence TTL
split) -- this class does NO caching of its own. It only answers "what's the
transcript for this video_id, right now" and signals outcome via return value
vs. exception so the existing cache logic in service.py classifies it correctly:

- Raise TavilyExtractError on anything that looks transient (network issue,
  no results AND no clear "this video has no captions" signal) -> service.py
  catches it and caches at transcript_failure_cache_ttl_seconds (short),
  so the next lookup retries instead of being stuck for a week.
- Return None (no exception) when Tavily successfully fetched the page but
  the video genuinely has no transcript -> service.py caches that at the
  full transcript_cache_ttl_seconds, since that fact won't change.
- Return the transcript text on success.
"""

from tavily import AsyncTavilyClient

from app.config import Settings
from app.transcript import TranscriptProvider


class TavilyExtractError(Exception):
    """Raised for anything that looks like a transient Tavily/network failure,
    as opposed to a confirmed 'this video has no transcript' result."""


class TavilyTranscriptProvider(TranscriptProvider):
    # Configures a Tavily client for transcript extraction.
    def __init__(self, settings: Settings):
        if not settings.tavily_api_key:
            raise ValueError("tavily_api_key is not configured")
        self._client = AsyncTavilyClient(api_key=settings.tavily_api_key)

    # Fetches and interprets the Tavily transcript response for a video.
    async def get_transcript(self, video_id: str) -> str | None:
        url = f"https://www.youtube.com/watch?v={video_id}"
        try:
            response = await self._client.extract(urls=[url], extract_depth="advanced")
        except Exception as exc:  # network error, auth error, timeout, etc.
            raise TavilyExtractError(str(exc)) from exc

        results = response.get("results") or []
        if not results:
            failed = response.get("failed_results") or []
            if failed:
                # Tavily itself flagged this fetch as failed (not "no captions") --
                # treat as transient rather than a confirmed absence.
                raise TavilyExtractError(f"Tavily failed to extract {url}: {failed}")
            # No results and no failure info either -- ambiguous, so default to
            # transient rather than risk caching a false "no transcript" for a week.
            raise TavilyExtractError(f"Tavily returned no results for {url}")

        raw_content = results[0].get("raw_content") or ""
        if "### Transcript" not in raw_content:
            # Page fetched fine, just genuinely no transcript/captions section --
            # this is a confirmed absence, safe to cache at the full TTL.
            return None

        _, transcript = raw_content.split("### Transcript", 1)
        return transcript.strip() or None
