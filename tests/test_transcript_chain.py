import asyncio

import pytest

from app.transcript import ChainedTranscriptProvider, TavilyTranscriptProvider


class StubProvider:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    async def get_transcript(self, video_id):
        self.calls.append(video_id)
        if self.error is not None:
            raise self.error
        return self.result


class StubTavilyClient:
    def __init__(self, raw_content=None, error=None):
        self.raw_content = raw_content
        self.error = error
        self.calls = 0

    async def extract(self, **kwargs):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return {"results": [{"raw_content": self.raw_content}]}


def tavily_provider(raw_content):
    provider = TavilyTranscriptProvider(api_key="test-key")
    provider._client = StubTavilyClient(raw_content)
    return provider


def test_tavily_transcript_is_used_without_calling_fallback():
    tavily = tavily_provider("Video page\n### Transcript\nTavily captions\n")
    youtube = StubProvider("YouTube captions")
    chain = ChainedTranscriptProvider([tavily, youtube])

    result = asyncio.run(chain.get_transcript("video-1"))

    assert result == "Tavily captions"
    assert youtube.calls == []


def test_tavily_none_falls_back_to_youtube_transcript():
    tavily = tavily_provider("Video page without rendered transcript")
    youtube = StubProvider("YouTube captions")
    chain = ChainedTranscriptProvider([tavily, youtube])

    result = asyncio.run(chain.get_transcript("video-2"))

    assert result == "YouTube captions"
    assert youtube.calls == ["video-2"]


def test_tavily_exception_falls_back_to_youtube_transcript(monkeypatch):
    tavily = tavily_provider("")
    client = StubTavilyClient(error=RuntimeError("Tavily unavailable"))
    tavily._client = client
    monkeypatch.setattr(TavilyTranscriptProvider, "_RETRY_DELAY_SECONDS", 0)
    youtube = StubProvider("YouTube captions")
    chain = ChainedTranscriptProvider([tavily, youtube])

    result = asyncio.run(chain.get_transcript("video-3"))

    assert result == "YouTube captions"
    assert client.calls == 2
    assert youtube.calls == ["video-3"]


def test_all_providers_unavailable_raise_final_provider_error():
    tavily = StubProvider(error=RuntimeError("Tavily unavailable"))
    youtube_error = ValueError("YouTube transcript unavailable")
    youtube = StubProvider(error=youtube_error)
    chain = ChainedTranscriptProvider([tavily, youtube])

    with pytest.raises(ValueError, match="YouTube transcript unavailable") as raised:
        asyncio.run(chain.get_transcript("video-4"))

    assert raised.value is youtube_error


def test_final_provider_none_confirms_transcript_absence():
    tavily = tavily_provider("Video page without rendered transcript")
    youtube = StubProvider(None)
    chain = ChainedTranscriptProvider([tavily, youtube])

    result = asyncio.run(chain.get_transcript("video-absence"))

    assert result is None
    assert youtube.calls == ["video-absence"]


def test_empty_tavily_transcript_is_none_and_falls_back():
    tavily = tavily_provider("Video page\n### Transcript\n \t \n")
    youtube = StubProvider("YouTube captions")
    chain = ChainedTranscriptProvider([tavily, youtube])

    result = asyncio.run(chain.get_transcript("video-5"))

    assert result == "YouTube captions"
    assert youtube.calls == ["video-5"]