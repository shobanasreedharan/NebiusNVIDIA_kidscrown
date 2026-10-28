"""No network calls"""

import asyncio
import os
from datetime import datetime, timezone
from types import SimpleNamespace

os.environ.setdefault("JWT_SECRET", "test-secret-" + "x" * 40)

import pytest
import httpx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import Classification
from app.youtube import (
    YouTubeClient,
    YouTubeError,
    YouTubeUnavailableError,
    _IDENTIFY_CACHE,
    parse_iso_duration,
)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def clear_identify_cache():
    _IDENTIFY_CACHE.clear()
    yield
    _IDENTIFY_CACHE.clear()


@pytest.fixture()
def env():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app), Session
    app.dependency_overrides.clear()


def signed_in_with_a_child(client):
    res = client.post(
        "/auth/register",
        json={
            "parent_name": "Ana",
            "email": "kidtest@example.com",
            "password": "correct-horse-battery",
            "family_name": "Rivera",
        },
    )
    headers = {"Authorization": f"Bearer {res.json()['access_token']}"}
    kid = client.post("/profiles", json={"name": "Leo", "age": 7}, headers=headers).json()
    return headers, kid["id"]


class FakeResponse:
    def __init__(self, payload=None, status_code=200):
        self._payload = payload or {}
        self.status_code = status_code

    def json(self):
        return self._payload


class FakeAsyncClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, params=None):
        self.calls.append((url, params))
        if not self.responses:
            raise AssertionError("No fake response left for this client call")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def add_classification(Session, profile_id, video_id, status="allow"):
    with Session() as db:
        row = Classification(
            profile_id=profile_id,
            video_id=video_id,
            channel_id="UCx",
            status=status,
            explanation="test",
            confidence=0.75,
            transcript_available=False,
            decided_by_channel_rule=False,
            created_at=datetime.now(timezone.utc),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row


def make_search_items(*pairs):
    items = []
    for title, channel, video_id in pairs:
        items.append(
            {
                "id": {"videoId": video_id},
                "snippet": {"title": title, "channelTitle": channel},
            }
        )
    return {"items": items}


def make_video_details(**durations):
    return {"items": [{"id": vid, "contentDetails": {"duration": dur}} for vid, dur in durations.items()]}


def test_identify_video_exact_match_high_confidence(monkeypatch):
    fake = FakeAsyncClient(
        [
            FakeResponse(make_search_items(("Learning Colors", "Tiny Explorers", "abc12345678"))),
            FakeResponse(make_video_details(**{"abc12345678": "PT57M59S"})),
        ]
    )
    monkeypatch.setattr("app.youtube.httpx.AsyncClient", lambda *args, **kwargs: fake)

    result = run(
        YouTubeClient(SimpleNamespace(youtube_api_key="test-key")).identify_video(
            "Learning Colors", "Tiny Explorers", duration_seconds=3479
        )
    )

    assert result == {"video_id": "abc12345678", "confidence": "high"}
    assert len(fake.calls) == 2
    assert fake.calls[0][0] == "https://www.googleapis.com/youtube/v3/search"
    assert fake.calls[1][0] == "https://www.googleapis.com/youtube/v3/videos"
    assert fake.calls[1][1]["id"] == "abc12345678"


def test_identify_video_single_candidate_without_duration_is_medium(monkeypatch):
    fake = FakeAsyncClient(
        [
            FakeResponse(make_search_items(("Storytime", "Sunny Day", "zzzzzzzzzzz"))),
            FakeResponse(make_video_details(**{"zzzzzzzzzzz": "PT1H2M3S"})),
        ]
    )
    monkeypatch.setattr("app.youtube.httpx.AsyncClient", lambda *args, **kwargs: fake)

    result = run(
        YouTubeClient(SimpleNamespace(youtube_api_key="test-key")).identify_video(
            "Storytime", "Sunny Day"
        )
    )

    assert result == {"video_id": "zzzzzzzzzzz", "confidence": "medium"}


def test_identify_video_single_candidate_ignores_wrong_duration(monkeypatch):
    fake = FakeAsyncClient(
        [
            FakeResponse(make_search_items(("Kitchen Chaos", "Little Helpers", "abcdefghijk"))),
            FakeResponse(make_video_details(**{"abcdefghijk": "PT57M59S"})),
        ]
    )
    monkeypatch.setattr("app.youtube.httpx.AsyncClient", lambda *args, **kwargs: fake)

    result = run(
        YouTubeClient(SimpleNamespace(youtube_api_key="test-key")).identify_video(
            "Kitchen Chaos", "Little Helpers", duration_seconds=15
        )
    )

    assert result == {"video_id": "abcdefghijk", "confidence": "medium"}


def test_identify_video_multiple_candidates_without_duration_match_returns_none(monkeypatch):
    fake = FakeAsyncClient(
        [
            FakeResponse(
                make_search_items(
                    ("Puzzle Time", "Milo Club", "onecandidate"),
                    ("Puzzle Time", "Milo Club", "twocandidate"),
                )
            ),
            FakeResponse(
                make_video_details(**{"onecandidate": "PT5M", "twocandidate": "PT10M"})
            ),
        ]
    )
    monkeypatch.setattr("app.youtube.httpx.AsyncClient", lambda *args, **kwargs: fake)

    result = run(
        YouTubeClient(SimpleNamespace(youtube_api_key="test-key")).identify_video(
            "Puzzle Time", "Milo Club", duration_seconds=1234
        )
    )

    assert result is None


def test_identify_video_chooses_high_confidence_candidate_within_two_seconds(monkeypatch):
    fake = FakeAsyncClient(
        [
            FakeResponse(
                make_search_items(
                    ("Math Mix", "Bright Buddies", "vidA1234567"),
                    ("Math Mix", "Bright Buddies", "vidB1234567"),
                )
            ),
            FakeResponse(
                make_video_details(**{"vidA1234567": "PT10M", "vidB1234567": "PT12M"})
            ),
        ]
    )
    monkeypatch.setattr("app.youtube.httpx.AsyncClient", lambda *args, **kwargs: fake)

    result = run(
        YouTubeClient(SimpleNamespace(youtube_api_key="test-key")).identify_video(
            "Math Mix", "Bright Buddies", duration_seconds=720
        )
    )

    assert result == {"video_id": "vidB1234567", "confidence": "high"}


def test_identify_video_requires_matching_channel_before_videos_call(monkeypatch):
    fake = FakeAsyncClient([
        FakeResponse(make_search_items(("Same Title", "Correct Channel", "abc12345678"))),
    ])
    monkeypatch.setattr("app.youtube.httpx.AsyncClient", lambda *args, **kwargs: fake)

    result = run(
        YouTubeClient(SimpleNamespace(youtube_api_key="test-key")).identify_video(
            "Same Title", "Different Channel"
        )
    )

    assert result is None
    assert len(fake.calls) == 1


def test_identify_video_matches_case_punctuation_and_html_entities(monkeypatch):
    fake = FakeAsyncClient(
        [
            FakeResponse(make_search_items(("Space &amp; Stars!!!", "Kids &amp; Co.", "spacevid123"))),
            FakeResponse(make_video_details(**{"spacevid123": "PT45S"})),
        ]
    )
    monkeypatch.setattr("app.youtube.httpx.AsyncClient", lambda *args, **kwargs: fake)

    result = run(
        YouTubeClient(SimpleNamespace(youtube_api_key="test-key")).identify_video(
            "Space & Stars", "Kids & Co.", duration_seconds=45
        )
    )

    assert result == {"video_id": "spacevid123", "confidence": "high"}


def test_identify_video_caches_successes_only(monkeypatch):
    fake = FakeAsyncClient(
        [
            FakeResponse(make_search_items(("Cached Title", "Cached Channel", "cacheid123"))),
            FakeResponse(make_video_details(**{"cacheid123": "PT2M"})),
        ]
    )
    monkeypatch.setattr("app.youtube.httpx.AsyncClient", lambda *args, **kwargs: fake)
    client = YouTubeClient(SimpleNamespace(youtube_api_key="test-key"))

    first = run(client.identify_video("Cached Title", "Cached Channel", duration_seconds=120))
    second = run(client.identify_video("Cached Title", "Cached Channel", duration_seconds=120))

    assert first == {"video_id": "cacheid123", "confidence": "high"}
    assert second == first
    assert len(fake.calls) == 2


def test_identify_video_does_not_cache_none_result(monkeypatch):
    fake = FakeAsyncClient([
        FakeResponse({"items": []}),
        FakeResponse({"items": []}),
    ])
    monkeypatch.setattr("app.youtube.httpx.AsyncClient", lambda *args, **kwargs: fake)
    client = YouTubeClient(SimpleNamespace(youtube_api_key="test-key"))

    first = run(client.identify_video("Gone Title", "Gone Channel"))
    second = run(client.identify_video("Gone Title", "Gone Channel"))

    assert first is None
    assert second is None
    assert len(fake.calls) == 2


@pytest.mark.parametrize(
    "settings, responses, expected_error",
    [
        (SimpleNamespace(youtube_api_key=""), [], YouTubeError),
        (SimpleNamespace(youtube_api_key="test-key"), [FakeResponse(status_code=500)], YouTubeError),
        (
            SimpleNamespace(youtube_api_key="test-key"),
            [
                FakeResponse(make_search_items(("Bad Video", "Bad Channel", "badvideo123"))),
                FakeResponse(status_code=500),
            ],
            YouTubeError,
        ),
    ],
)
def test_identify_video_raises_for_missing_key_and_http_errors(monkeypatch, settings, responses, expected_error):
    fake = FakeAsyncClient(responses)
    monkeypatch.setattr("app.youtube.httpx.AsyncClient", lambda *args, **kwargs: fake)

    with pytest.raises(expected_error):
        run(YouTubeClient(settings).identify_video("Bad Video", "Bad Channel", duration_seconds=61))


def test_parse_iso_duration_handles_expected_values():
    assert parse_iso_duration("PT57M59S") == 3479
    assert parse_iso_duration("PT1H2M3S") == 3723
    assert parse_iso_duration("PT45S") == 45
    assert parse_iso_duration("P0D") is None
    assert parse_iso_duration("") is None


def test_identify_route_returns_match_and_context(env, monkeypatch):
    client, Session = env
    headers, pid = signed_in_with_a_child(client)
    add_classification(Session, pid, "matchvideo123", status="allow")

    class FakeYouTubeClient:
        def __init__(self, settings):
            self.settings = settings

        async def identify_video(self, title, channel, duration_seconds=None):
            assert title == "Star Lab"
            assert channel == "Curious Kids"
            assert duration_seconds == 300
            return {"video_id": "matchvideo123", "confidence": "high"}

        async def get_metadata(self, video_id):
            return {"title": "Star Lab", "channel": "Curious Kids", "channel_id": "UCx", "description": ""}

    class FakeService:
        def __init__(self, db, settings):
            self.db = db

        async def classify(self, profile, video_id):
            row = self.db.get(Classification, 1)
            return row, False

    async def fake_context(db, settings, video_ids, youtube=None):
        return {"matchvideo123": {"title": "Star Lab", "channel_name": "Curious Kids"}}

    monkeypatch.setattr("app.main.YouTubeClient", FakeYouTubeClient)
    monkeypatch.setattr("app.main.ClassificationService", FakeService)
    monkeypatch.setattr("app.main.get_video_context", fake_context)

    body = client.post(
        "/identify",
        json={"profile_id": pid, "title": "Star Lab", "channel": "Curious Kids", "duration_seconds": 300},
        headers=headers,
    ).json()

    assert body["identified"] is True
    assert body["video_id"] == "matchvideo123"
    assert body["match_confidence"] == "high"
    assert body["classification"]["status"] == "allow"
    assert body["classification"]["title"] == "Star Lab"
    assert body["classification"]["channel_name"] == "Curious Kids"


def test_identify_route_returns_identified_false_when_no_match(env, monkeypatch):
    client, _ = env
    headers, pid = signed_in_with_a_child(client)

    class FakeYouTubeClient:
        def __init__(self, settings):
            self.settings = settings

        async def identify_video(self, title, channel, duration_seconds=None):
            return None

    class ShouldNotConstruct:
        def __init__(self, *args, **kwargs):
            raise AssertionError("ClassificationService should not be constructed")

    monkeypatch.setattr("app.main.YouTubeClient", FakeYouTubeClient)
    monkeypatch.setattr("app.main.ClassificationService", ShouldNotConstruct)

    body = client.post(
        "/identify",
        json={"profile_id": pid, "title": "Missing", "channel": "Nope", "duration_seconds": 300},
        headers=headers,
    ).json()

    assert body["identified"] is False


def test_identify_route_returns_503_when_identify_video_raises_you_tube_error(env, monkeypatch):
    client, _ = env
    headers, pid = signed_in_with_a_child(client)

    class FakeYouTubeClient:
        def __init__(self, settings):
            self.settings = settings

        async def identify_video(self, title, channel, duration_seconds=None):
            raise YouTubeError("search failed")

    monkeypatch.setattr("app.main.YouTubeClient", FakeYouTubeClient)

    res = client.post(
        "/identify",
        json={"profile_id": pid, "title": "Broken", "channel": "Broken Channel", "duration_seconds": 33},
        headers=headers,
    )

    assert res.status_code == 503
    assert "search failed" in res.json()["detail"]


def test_identify_connect_timeout_retries_then_returns_clean_503(env, monkeypatch, caplog):
    client, _ = env
    headers, pid = signed_in_with_a_child(client)
    fake = FakeAsyncClient(
        [
            httpx.ConnectTimeout("connection timed out"),
            httpx.ConnectTimeout("connection timed out"),
        ]
    )

    async def no_delay(_seconds):
        return None

    monkeypatch.setattr("app.youtube.httpx.AsyncClient", lambda *args, **kwargs: fake)
    monkeypatch.setattr("app.youtube.asyncio.sleep", no_delay)
    monkeypatch.setattr("app.main.get_settings", lambda: SimpleNamespace(
        youtube_api_key="test-key",
        youtube_identify_total_timeout_seconds=12.0,
        classify_identify_requests_per_family_per_hour=30,
    ))

    with caplog.at_level("WARNING", logger="uvicorn.error"):
        response = client.post(
            "/identify",
            json={"profile_id": pid, "title": "Timed out title", "channel": "Channel"},
            headers=headers,
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "youtube_lookup_unavailable"}
    assert len(fake.calls) == 2
    assert "title='Timed out title'" in caplog.text
    assert "error=ConnectTimeout" in caplog.text


def test_identify_connect_timeout_retry_success_returns_200(env, monkeypatch, caplog):
    client, Session = env
    headers, pid = signed_in_with_a_child(client)
    video_id = "retryvid001"
    row = add_classification(Session, pid, video_id, status="allow")
    fake = FakeAsyncClient(
        [
            httpx.ConnectTimeout("connection timed out"),
            FakeResponse(make_search_items(("Retry Story", "Bright Channel", video_id))),
            FakeResponse(make_video_details(**{video_id: "PT5M"})),
            FakeResponse(
                {
                    "items": [
                        {
                            "snippet": {
                                "title": "Retry Story",
                                "description": "A story",
                                "channelTitle": "Bright Channel",
                                "channelId": "UCretry",
                                "liveBroadcastContent": "none",
                            },
                            "contentDetails": {
                                "duration": "PT5M",
                                "contentRating": {"ytRating": "ytGeneral"},
                            },
                            "status": {"madeForKids": True},
                        }
                    ]
                }
            ),
        ]
    )

    class FakeService:
        def __init__(self, db, settings):
            self.db = db
            self.last_timings = {"redis": 0.0, "transcript": 0.0, "model": 0.0}

        async def classify(self, profile, requested_video_id):
            assert requested_video_id == video_id
            return self.db.get(Classification, row.id), False

    async def no_delay(_seconds):
        return None

    monkeypatch.setattr("app.youtube.httpx.AsyncClient", lambda *args, **kwargs: fake)
    monkeypatch.setattr("app.youtube.asyncio.sleep", no_delay)
    monkeypatch.setattr("app.main.get_settings", lambda: SimpleNamespace(
        youtube_api_key="test-key", youtube_identify_total_timeout_seconds=12.0,
        classify_identify_requests_per_family_per_hour=30,
        ttl_for=lambda _kind: 3600,
    ))
    monkeypatch.setattr("app.main.ClassificationService", FakeService)

    with caplog.at_level("INFO", logger="uvicorn.error"):
        response = client.post(
            "/identify",
            json={
                "profile_id": pid,
                "title": "Retry Story",
                "channel": "Bright Channel",
                "duration_seconds": 300,
            },
            headers=headers,
        )

    assert response.status_code == 200, response.text
    assert response.json()["identified"] is True
    assert len(fake.calls) == 4
    assert "youtube_search" in caplog.text
    assert "video_metadata" in caplog.text
    assert "transcript" in caplog.text and "model" in caplog.text and "total" in caplog.text


def test_identify_route_rejects_profile_from_other_family(env):
    client, _ = env
    headers1, pid = signed_in_with_a_child(client)
    res2 = client.post(
        "/auth/register",
        json={
            "parent_name": "Basil",
            "email": "other@example.com",
            "password": "correct-horse-battery",
            "family_name": "Other Family",
        },
    )
    headers2 = {"Authorization": f"Bearer {res2.json()['access_token']}"}

    res = client.post(
        "/identify",
        json={"profile_id": pid, "title": "Any", "channel": "Demo", "duration_seconds": 10},
        headers=headers2,
    )

    assert res.status_code == 404


def test_identify_route_requires_auth(env):
    client, _ = env
    res = client.post(
        "/identify",
        json={"profile_id": 1, "title": "No auth", "channel": "Demo", "duration_seconds": 10},
    )
    assert res.status_code == 401


def test_identify_route_rejects_empty_title(env):
    client, _ = env
    headers, pid = signed_in_with_a_child(client)

    res = client.post(
        "/identify",
        json={"profile_id": pid, "title": "", "channel": "Demo", "duration_seconds": 10},
        headers=headers,
    )

    assert res.status_code == 422


def test_identify_route_passes_bare_video_id_to_classify(env, monkeypatch):
    client, Session = env
    headers, pid = signed_in_with_a_child(client)
    row = add_classification(Session, pid, "bareid12345", status="allow")
    seen = {}

    class FakeYouTubeClient:
        def __init__(self, settings):
            self.settings = settings

        async def identify_video(self, title, channel, duration_seconds=None):
            return {"video_id": "bareid12345", "confidence": "medium"}

        async def get_metadata(self, video_id):
            return {"title": "Bare video", "channel": "Bare channel", "channel_id": "UCx", "description": ""}

    class FakeService:
        def __init__(self, db, settings):
            self.db = db

        async def classify(self, profile, video_id):
            seen["video_id"] = video_id
            return self.db.get(Classification, row.id), False

    async def fake_context(db, settings, video_ids, youtube=None):
        return {"bareid12345": {"title": "Bare video", "channel_name": "Bare channel"}}

    monkeypatch.setattr("app.main.YouTubeClient", FakeYouTubeClient)
    monkeypatch.setattr("app.main.ClassificationService", FakeService)
    monkeypatch.setattr("app.main.get_video_context", fake_context)

    body = client.post(
        "/identify",
        json={"profile_id": pid, "title": "Bare video", "channel": "Bare channel"},
        headers=headers,
    ).json()

    assert seen["video_id"] == "bareid12345"
    assert body["identified"] is True
    assert body["video_id"] == "bareid12345"
