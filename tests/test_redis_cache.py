import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.config import Settings
from app.redis_cache import RedisCachedTranscriptProvider, seconds_until_end_of_day
from app.models import Family, Profile
from app.service import ClassificationService
from evals.common import make_in_memory_session


class FakeRedis:
    def __init__(self):
        self.data, self.ttls = {}, {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, ex=None):
        self.data[key] = value
        self.ttls[key] = ex


class DeadRedis:
    async def get(self, key):
        raise ConnectionError("down")

    async def set(self, key, value, ex=None):
        raise ConnectionError("down")


class RecoveringRedis:
    def __init__(self):
        self.get_calls = 0
        self.set_calls = 0
        self.recovered = False

    async def get(self, key):
        self.get_calls += 1
        if not self.recovered:
            raise ConnectionError("unavailable")
        return "recovered"

    async def set(self, key, value, ex=None):
        self.set_calls += 1


def test_redis_circuit_skips_calls_for_60_seconds_then_probes(monkeypatch, caplog):
    now = [100.0]
    monkeypatch.setattr("app.redis_cache.time.monotonic", lambda: now[0])
    redis = RecoveringRedis()
    inner = Inner("transcript")
    provider = make(inner, redis)

    with caplog.at_level("WARNING", logger="app.redis_cache"):
        assert asyncio.run(provider.get_transcript("breaker-video")) == "transcript"
        assert asyncio.run(provider.get_transcript("breaker-video")) == "transcript"
        assert asyncio.run(provider.get_transcript("breaker-video")) == "transcript"

    assert redis.get_calls == 1 and redis.set_calls == 0
    assert inner.calls == 3
    assert caplog.text.count("Redis get failed; circuit open") == 1

    now[0] += 61
    redis.recovered = True
    assert asyncio.run(provider.get_transcript("breaker-video")) == "recovered"
    assert redis.get_calls == 2


def test_redis_enabled_default_tracks_configured_url(monkeypatch):
    monkeypatch.delenv("REDIS_ENABLED", raising=False)
    assert Settings(_env_file=None, redis_url=None).redis_enabled is False
    assert Settings(_env_file=None, redis_url="redis://localhost:6379").redis_enabled is True
    assert Settings(
        _env_file=None, redis_url="redis://localhost:6379", redis_enabled=False
    ).redis_is_enabled is False


def test_redis_disabled_does_not_create_client_or_log_errors(monkeypatch, caplog):
    Session = make_in_memory_session()
    with Session() as db:
        profile = Profile(
            family=Family(name="Family", policy_text=""),
            name="Ava",
            age=8,
            policy_text="Allow science",
        )
        db.add(profile)
        db.commit()
        monkeypatch.setattr(
            "app.service.get_redis_client",
            lambda *args, **kwargs: pytest.fail("Redis client must not be created when disabled"),
        )
        with caplog.at_level("WARNING", logger="app.redis_cache"):
            service = ClassificationService(
                db,
                Settings(
                    redis_url="redis://localhost:6379",
                    redis_enabled=False,
                    tavily_api_key="test-key",
                ),
            )
        assert not any(
            isinstance(provider, RedisCachedTranscriptProvider)
            for provider in getattr(service.transcript_provider, "providers", ())
        )
        assert "Redis" not in caplog.text


class Inner:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, 0

    async def get_transcript(self, video_id):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


def make(inner, r):
    return RedisCachedTranscriptProvider(inner, r, "America/Chicago")


def test_second_call_skips_inner():
    inner, r = Inner("hello"), FakeRedis()
    p = make(inner, r)
    assert asyncio.run(p.get_transcript("abc")) == "hello"
    assert asyncio.run(p.get_transcript("abc")) == "hello"
    assert inner.calls == 1


def test_ambiguous_none_is_cached_and_returns_none():
    inner, r = Inner(None), FakeRedis()
    p = make(inner, r)
    assert asyncio.run(p.get_transcript("abc")) is None
    assert asyncio.run(p.get_transcript("abc")) is None
    assert inner.calls == 1


def test_errors_are_not_cached():
    inner, r = Inner(error=RuntimeError("boom")), FakeRedis()
    p = make(inner, r)
    for _ in range(2):
        try:
            asyncio.run(p.get_transcript("abc"))
        except RuntimeError:
            pass
    assert inner.calls == 2 and not r.data


def test_redis_down_falls_through():
    inner = Inner("hello")
    p = make(inner, DeadRedis())
    assert asyncio.run(p.get_transcript("abc")) == "hello"
    assert inner.calls == 1


def test_ttl_is_time_left_today():
    now = datetime(2026, 10, 3, 23, 0, tzinfo=ZoneInfo("America/Chicago"))
    assert seconds_until_end_of_day("America/Chicago", now) == 3600