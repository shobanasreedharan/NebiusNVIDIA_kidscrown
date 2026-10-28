import asyncio
from collections.abc import Sequence
from typing import Protocol


class TranscriptProvider(Protocol):
    async def get_transcript(self, video_id: str) -> str | None:
        ...


class NullTranscriptProvider:
    # Represents a transcript source that always reports no transcript.
    async def get_transcript(self, video_id: str) -> str | None:
        return None


class YoutubeTranscriptApiProvider:
    """Unofficial transcript fetch via youtube-transcript-api.

    No longer the sole/default provider after the Week 1 checkpoint found
    Tavily avoids the IP-blocking this hit at scale -- but Tavily's own
    "no transcript" result turned out to be ambiguous (see
    TavilyTranscriptProvider below), so this is back in play as the second
    link in ChainedTranscriptProvider: only consulted when Tavily can't
    confirm one way or the other, to actually settle the question. Still
    carries the IP-blocking risk that motivated moving off it as the
    primary/sole provider -- see ChainedTranscriptProvider's docstring for
    that tradeoff.

    Contract with the cache-TTL split in service.py:
    - Returns None (no exception) when the library confirms there is
      genuinely no transcript for this video -> cached at the long TTL.
    - Raises on anything else (network error, rate limit, IP block,
      unexpected library error, or exceeding timeout_seconds) -> caller
      catches this and caches at the short "transcript_failure" TTL,
      since it may resolve on retry. No internal retry here (see
      get_transcript below for why).
    """

    def __init__(self, languages: tuple[str, ...] = ("en",), timeout_seconds: float = 10.0):
        self.languages = list(languages)
        self.timeout_seconds = timeout_seconds

    # Runs the blocking transcript library without blocking the event loop.
    async def get_transcript(self, video_id: str) -> str | None:
        # No retry here, deliberately: this is already the IP-block-prone
        # fallback and the last link in the chain -- an immediate retry
        # after a failure is more likely to worsen blocking than help, and
        # a genuine miss here just falls through to the cache's short-TTL
        # failure path, retried fresh on the next real request instead.
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._fetch_sync, video_id),
                timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            raise TimeoutError(
                f"youtube-transcript-api fetch for {video_id} exceeded "
                f"{self.timeout_seconds}s"
            ) from exc

    # Fetches and normalizes transcript snippets in synchronous worker code.
    def _fetch_sync(self, video_id: str) -> str | None:
        # Imported lazily so the package (and the rest of the app) doesn't
        # hard-depend on youtube-transcript-api being installed unless this
        # provider is actually used.
        from youtube_transcript_api import YouTubeTranscriptApi
        from youtube_transcript_api._errors import (
            NoTranscriptFound,
            TranscriptsDisabled,
            VideoUnavailable,
        )

        try:
            api = YouTubeTranscriptApi()
            fetched = api.fetch(video_id, languages=self.languages)
            text = " ".join(snippet.text for snippet in fetched)
            return text or None
        except (NoTranscriptFound, TranscriptsDisabled, VideoUnavailable):
            # Genuine, confirmed absence of a transcript for this video.
            return None
        # Anything else (network error, IP block, rate limit, library
        # version drift, etc.) is intentionally NOT caught here — it
        # propagates so service.py treats it as transient.


class TavilyExtractError(Exception):
    """Raised for anything that looks like a transient Tavily/network failure,
    as opposed to a confirmed 'this video has no transcript' result."""


class TavilyTranscriptProvider:
    """Transcript fetch via the Tavily extract API.

    Fast and doesn't hit the IP-blocking that made YoutubeTranscriptApiProvider
    unreliable at scale -- but its "no transcript" result is AMBIGUOUS, not a
    confirmed absence. Confirmed directly (Week 1 checkpoint, 2026-09-18):
    for some videos, Tavily's page fetch returns YouTube's collapsed
    "Show transcript" placeholder (literally "NaN / NaN" in the raw HTML)
    instead of the actual caption text -- identical result, identical
    ~0.01s response time on repeated calls, so this is a systematic gap in
    what Tavily's fetch renders for certain videos, not request-to-request
    flakiness worth retrying. There is no way from this provider alone to
    tell "video genuinely has no captions" apart from "Tavily didn't
    render them."

    Because of that, DO NOT use this as the sole/standalone transcript_provider
    in service.py -- its None would get cached as a confirmed long-TTL absence
    by the cache logic there, permanently misrepresenting videos Tavily merely
    failed to render. Use it as the first link in ChainedTranscriptProvider
    (below) instead, so a second provider gets to actually confirm absence
    before it's treated as one.

    Contract (for use inside a chain):
    - Returns transcript text on success.
    - Returns None when Tavily fetched the page but didn't render a
      transcript section -- ambiguous, NOT a confirmed absence.
    - Raises TavilyExtractError on anything that looks like a genuine
      fetch failure (network/auth error, timeout, or an empty response
      with no results and no failure info either) -- after one retry.

    One retry (not a full backoff loop) on failure: a transient Tavily
    blip that gives up immediately would fall through to
    YoutubeTranscriptApiProvider in the chain, spending that provider's
    limited IP-block "budget" on what might've just been a one-off
    hiccup. A single quick retry protects the riskier fallback from being
    triggered unnecessarily, without the complexity of real backoff/jitter
    logic that a handful of hackathon-scale requests doesn't need.
    """

    _TIMEOUT_SECONDS = 10.0
    _RETRY_DELAY_SECONDS = 1.0
    _MAX_ATTEMPTS = 2  # one retry -- see docstring above

    def __init__(self, api_key: str):
        # Configures a lazily initialized Tavily transcript client.
        if not api_key:
            raise ValueError("tavily_api_key is not configured")
        self._api_key = api_key
        self._client = None  # lazily constructed, see _get_client

    # Lazily imports and creates the Tavily client when first needed.
    def _get_client(self):
        # Imported lazily, same reasoning as the youtube-transcript-api
        # import above: don't hard-depend on tavily-python unless this
        # provider is actually used.
        if self._client is None:
            from tavily import AsyncTavilyClient

            self._client = AsyncTavilyClient(api_key=self._api_key)
        return self._client

    # Extracts transcript text from Tavily while distinguishing ambiguous misses.
    async def get_transcript(self, video_id: str) -> str | None:
        client = self._get_client()
        url = f"https://www.youtube.com/watch?v={video_id}"
        response = await self._extract_with_retry(client, url)

        results = response.get("results") or []
        if not results:
            failed = response.get("failed_results") or []
            if failed:
                raise TavilyExtractError(f"Tavily failed to extract {url}: {failed}")
            raise TavilyExtractError(f"Tavily returned no results for {url}")

        raw_content = results[0].get("raw_content") or ""
        if "### Transcript" not in raw_content:
            # Ambiguous -- see class docstring. NOT a confirmed absence.
            return None

        _, transcript = raw_content.split("### Transcript", 1)
        return transcript.strip() or None

    # Performs the bounded Tavily extraction retry flow.
    async def _extract_with_retry(self, client, url: str) -> dict:
        last_exc: Exception | None = None
        for attempt in range(1, self._MAX_ATTEMPTS + 1):
            try:
                return await asyncio.wait_for(
                    client.extract(urls=[url], extract_depth="advanced"),
                    timeout=self._TIMEOUT_SECONDS,
                )
            except Exception as exc:  # network error, auth error, timeout, etc.
                last_exc = exc
                if attempt < self._MAX_ATTEMPTS:
                    await asyncio.sleep(self._RETRY_DELAY_SECONDS)
        raise TavilyExtractError(
            f"Tavily extract for {url} failed after {self._MAX_ATTEMPTS} attempts: {last_exc}"
        ) from last_exc


class ChainedTranscriptProvider:
    """Tries each provider in order, falling through when a result is
    ambiguous or the provider itself fails.

    Only the LAST provider's None is treated as a confirmed absence -- every
    earlier provider's None means "inconclusive, try the next one," not
    "no transcript." This matters specifically because TavilyTranscriptProvider's
    None is ambiguous (see its docstring); pairing it with
    YoutubeTranscriptApiProvider as the final link means an actual "no
    captions" answer from the library settles the question instead of
    Tavily's ambiguous miss being cached as if it had.

    Tradeoff, stated plainly rather than assumed: this reintroduces
    YoutubeTranscriptApiProvider's IP-blocking risk for whatever fraction
    of videos Tavily can't confirm -- the Week 1 checkpoint measured that
    fraction at roughly 30-70% depending on video bucket, which is not a
    rare edge case. It also adds a second sequential network round-trip
    (Tavily, then the library) to service.py's live /classify path for
    exactly those videos.

    Contract with service.py's cache-TTL split (same shape as a single
    provider): returns None only when the final provider confirms absence
    -> cached at the long TTL. Raises if every provider either raised or
    was itself inconclusive with nothing left to fall through to -> cached
    at the short "transcript_failure" TTL. A provider raising mid-chain
    does not abort the chain -- the next provider still gets a chance.
    """

    def __init__(self, providers: Sequence[TranscriptProvider]):
        # Validates and stores the ordered transcript provider chain.
        if not providers:
            raise ValueError("ChainedTranscriptProvider needs at least one provider")
        self.providers = tuple(providers)

    # Tries providers in order until text or a confirmed absence is available.
    async def get_transcript(self, video_id: str) -> str | None:
        last_error: Exception | None = None
        for i, provider in enumerate(self.providers):
            is_last = i == len(self.providers) - 1
            try:
                result = await provider.get_transcript(video_id)
            except Exception as exc:  # noqa: BLE001 -- deliberately broad, see docstring
                last_error = exc
                continue  # this provider failed outright -- try the next one
            if result is not None:
                return result
            if is_last:
                # Only the final provider gets to claim confirmed absence.
                return None
            # Non-final provider returned None (inconclusive) -- fall through
            # to the next provider rather than accepting it as an answer.

        # Every provider either raised, or was a non-final provider that
        # returned an inconclusive None with nothing left after it to
        # confirm one way or the other -- treat the whole chain as
        # transient rather than risk caching a false absence.
        raise last_error or RuntimeError(
            f"All transcript providers were inconclusive for {video_id}"
        )
