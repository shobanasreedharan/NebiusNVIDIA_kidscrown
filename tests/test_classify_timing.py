"""A slow or hanging transcript can't hold a check hostage, and every check logs where its time went.

Fakes and an in-memory database only: no network calls, no credits.
"""

import asyncio
import logging
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Family, Profile
from app.service import ClassificationService

FAILURE_TTL = 111
FULL_TTL = 999


def settings(timeout=0.05):
    ttls = {"transcript_failure": FAILURE_TTL, "transcript": FULL_TTL}
    return SimpleNamespace(tavily_api_key=None, transcript_timeout_seconds=timeout, ttl_for=lambda name: ttls.get(name, 3600))


class FakeYouTube:
    async def get_metadata(self, video_id):
        return {"title": "t", "description": "d", "channel": "c", "channel_id": "UCfake"}


class HangingTranscripts:
    async def get_transcript(self, video_id):
        await asyncio.sleep(30)
        return "never arrives"


class QuickTranscripts:
    async def get_transcript(self, video_id):
        return "some transcript"


class FakeModel:
    def __init__(self, status):
        self.status = status

    async def classify(self, policy, metadata, transcript, feedback):
        return {"status": self.status, "explanation": "Because policy.", "confidence": 0.9}

    async def explain_channel_rule(self, action, channel_name, reason):
        return "rule"


class RecordingCache:
    def __init__(self):
        self.sets = []

    def get(self, key):
        return None

    def set(self, key, value, ttl):
        self.sets.append((key, value, ttl))


@pytest.fixture()
def Session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def run_check(Session, video_id, status, transcripts, timeout=0.05):
    with Session() as db:
        profile = Profile(family=Family(name="F", policy_text=""), name="Kid", age=7, policy_text=f"policy {video_id}")
        db.add(profile)
        db.commit()
        service = ClassificationService(
            db, settings(timeout), youtube=FakeYouTube(), model=FakeModel(status), transcript_provider=transcripts
        )
        service.cache = RecordingCache()
        started = time.perf_counter()
        row, cached = asyncio.run(service.classify(profile, f"https://www.youtube.com/watch?v={video_id}"))
        return row, service.cache.sets, time.perf_counter() - started


def test_a_hanging_transcript_provider_times_out_and_the_check_still_finishes(Session):
    row, _, seconds = run_check(Session, "timeoutvid1", "allow", HangingTranscripts())
    assert seconds < 5  # not the 30 seconds the provider would take
    assert row.transcript_available is False
    assert row.status == "ask_parent"  # an allow without a transcript is downgraded, as always


def test_a_timeout_does_not_turn_a_block_into_a_question(Session):
    row, _, _ = run_check(Session, "timeoutvid2", "block", HangingTranscripts())
    assert row.status == "block"


def test_a_timeout_is_remembered_briefly_not_for_the_full_ttl(Session):
    _, sets, _ = run_check(Session, "timeoutvid3", "allow", HangingTranscripts())
    ttl_by_key = {key: ttl for key, _, ttl in sets}
    assert ttl_by_key["transcript:timeoutvid3"] == FAILURE_TTL


def test_a_transcript_that_arrives_in_time_is_used_and_cached_for_the_full_ttl(Session):
    row, sets, _ = run_check(Session, "quickvid001", "allow", QuickTranscripts())
    assert row.status == "allow" and row.transcript_available is True
    assert {key: ttl for key, _, ttl in sets}["transcript:quickvid001"] == FULL_TTL


def test_each_check_logs_where_its_time_went(Session, caplog):
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    run_check(Session, "logvid00001", "allow", QuickTranscripts())
    line = next(record.getMessage() for record in caplog.records if "logvid00001" in record.getMessage())
    assert "classified" in line and "metadata" in line and "transcript" in line and "model" in line and "total" in line


def test_a_timeout_is_named_in_the_log(Session, caplog):
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    run_check(Session, "logvid00002", "allow", HangingTranscripts())
    line = next(record.getMessage() for record in caplog.records if "logvid00002" in record.getMessage())
    assert "timed out" in line
