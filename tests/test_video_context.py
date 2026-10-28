"""Video title / channel name in responses: looked up on demand, never stored.

No network calls: YouTube is a fake and the routes' lookups are patched.
"""

import asyncio
import os
from datetime import datetime, timezone
from types import SimpleNamespace

os.environ.setdefault("JWT_SECRET", "test-secret-" + "x" * 40)

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import Classification
from app.video_context import MAX_FETCHES_PER_CALL, get_video_context

SETTINGS = SimpleNamespace(ttl_for=lambda name: 3600)


class FakeYouTube:
    def __init__(self, fail=()):
        self.calls = []
        self.fail = set(fail)

    async def get_metadata(self, video_id):
        self.calls.append(video_id)
        if video_id in self.fail:
            raise RuntimeError("video unavailable")
        return {"title": f"Title {video_id}", "channel": f"Channel {video_id}", "channel_id": "UC1"}


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        yield db


def run(coro):
    return asyncio.run(coro)


def test_lookup_reads_the_cache_after_the_first_fetch(db_session):
    youtube = FakeYouTube()
    first = run(get_video_context(db_session, SETTINGS, ["ctxcache001"], youtube=youtube))
    second = run(get_video_context(db_session, SETTINGS, ["ctxcache001"], youtube=youtube))
    assert first == second == {"ctxcache001": {"title": "Title ctxcache001", "channel_name": "Channel ctxcache001"}}
    assert youtube.calls == ["ctxcache001"]  # the second call was a cache hit


def test_an_unavailable_video_comes_back_without_a_title_and_is_retried_later(db_session):
    youtube = FakeYouTube(fail={"ctxgone0001"})
    result = run(get_video_context(db_session, SETTINGS, ["ctxgone0001", "ctxfine0001"], youtube=youtube))
    assert result["ctxgone0001"] == {"title": None, "channel_name": None}
    assert result["ctxfine0001"]["title"] == "Title ctxfine0001"
    run(get_video_context(db_session, SETTINGS, ["ctxgone0001"], youtube=youtube))
    assert youtube.calls.count("ctxgone0001") == 2  # a failure isn't cached


def test_a_long_cold_queue_is_capped(db_session):
    ids = [f"ctxcap{i:05d}" for i in range(MAX_FETCHES_PER_CALL + 5)]
    youtube = FakeYouTube()
    result = run(get_video_context(db_session, SETTINGS, ids, youtube=youtube))
    assert len(youtube.calls) == MAX_FETCHES_PER_CALL
    assert sum(1 for v in result.values() if v["title"] is None) == 5


def test_duplicate_ids_are_looked_up_once(db_session):
    youtube = FakeYouTube()
    run(get_video_context(db_session, SETTINGS, ["ctxdupe0001"] * 4, youtube=youtube))
    assert youtube.calls == ["ctxdupe0001"]


# --- through the API -------------------------------------------------------------------------


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
        json={"parent_name": "Ana", "email": "ctx@example.com", "password": "correct-horse-battery", "family_name": "Rivera"},
    )
    headers = {"Authorization": f"Bearer {res.json()['access_token']}"}
    kid = client.post("/profiles", json={"name": "Leo", "age": 7}, headers=headers).json()
    return headers, kid["id"]


def add_row(Session, profile_id, video_id, status="ask_parent"):
    with Session() as db:
        row = Classification(
            profile_id=profile_id, video_id=video_id, channel_id="UCx", status=status,
            explanation="unsure", confidence=0.5, created_at=datetime.now(timezone.utc),
        )
        db.add(row)
        db.commit()
        return row.id


def test_queue_items_carry_title_and_channel_name(env, monkeypatch):
    client, Session = env
    headers, pid = signed_in_with_a_child(client)
    add_row(Session, pid, "queuevid001")
    add_row(Session, pid, "queuevid002")

    async def fake_context(db, settings, video_ids, youtube=None):
        assert sorted(video_ids) == ["queuevid001", "queuevid002"]  # one batched lookup
        return {"queuevid001": {"title": "Slime challenge", "channel_name": "ToyTown"}}

    monkeypatch.setattr("app.main.get_video_context", fake_context)
    items = {i["video_id"]: i for i in client.get(f"/profiles/{pid}/queue", headers=headers).json()}
    assert items["queuevid001"]["title"] == "Slime challenge"
    assert items["queuevid001"]["channel_name"] == "ToyTown"
    assert items["queuevid002"]["title"] is None and items["queuevid002"]["channel_name"] is None


def test_classify_response_carries_title_and_channel_name(env, monkeypatch):
    client, Session = env
    headers, pid = signed_in_with_a_child(client)
    row_id = add_row(Session, pid, "classifyvid1", status="allow")

    class FakeService:
        def __init__(self, db, settings):
            self.db = db

        async def classify(self, profile, url):
            return self.db.get(Classification, row_id), False

    async def fake_context(db, settings, video_ids, youtube=None):
        return {"classifyvid1": {"title": "Volcanoes for kids", "channel_name": "Curious Minds"}}

    monkeypatch.setattr("app.main.ClassificationService", FakeService)
    monkeypatch.setattr("app.main.get_video_context", fake_context)
    body = client.post(
        "/classify", json={"profile_id": pid, "youtube_url": "https://youtu.be/classifyvid1"}, headers=headers
    ).json()
    assert body["status"] == "allow" and body["cached"] is False
    assert body["title"] == "Volcanoes for kids" and body["channel_name"] == "Curious Minds"
