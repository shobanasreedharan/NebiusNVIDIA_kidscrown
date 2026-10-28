"""Transcript-unavailable safety net: only permissive verdicts get downgraded.

allow -> ask_parent (confidence capped), ask_parent stays, block STAYS block.
Uses fakes and an in-memory DB: no network calls, no Nebius credits.
"""

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Classification, Family, Profile
from app.service import ClassificationService


class FakeYouTube:
    async def get_metadata(self, video_id):
        return {"title": "t", "description": "d", "channel": "c", "channel_id": "UCfake"}


class FakeTranscripts:
    def __init__(self, text):
        self.text = text

    async def get_transcript(self, video_id):
        return self.text


class FakeModel:
    def __init__(self, status, confidence):
        self.status, self.confidence = status, confidence

    async def classify(self, policy, metadata, transcript, feedback):
        return {"status": self.status, "explanation": "Because policy.", "confidence": self.confidence}

    async def explain_channel_rule(self, action, channel_name, reason):
        return "rule"


@pytest.fixture()
def Session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def classify(Session, video_id, status, confidence, transcript):
    settings = SimpleNamespace(tavily_api_key=None, ttl_for=lambda name: 3600)
    with Session() as db:
        family = Family(name="F", policy_text="")
        profile = Profile(family=family, name="Kid", age=7, policy_text=f"policy for {video_id}")
        db.add(profile)
        db.commit()
        service = ClassificationService(
            db,
            settings,
            youtube=FakeYouTube(),
            model=FakeModel(status, confidence),
            transcript_provider=FakeTranscripts(transcript),
        )
        row, _ = asyncio.run(service.classify(profile, f"https://www.youtube.com/watch?v={video_id}"))
        queued = db.scalars(select(Classification).where(Classification.status == "ask_parent")).all()
        return row.status, row.confidence, row.explanation, row.transcript_available, len(queued)


def test_block_without_transcript_stays_block(Session):
    status, confidence, explanation, has_transcript, queued = classify(Session, "blockNoTr01", "block", 0.98, None)
    assert status == "block" and confidence == 0.98 and has_transcript is False
    assert "title and description" in explanation
    assert queued == 0  # the parent is not asked to re-decide their own "never"


def test_allow_without_transcript_is_downgraded(Session):
    status, confidence, explanation, has_transcript, queued = classify(Session, "allowNoTr01", "allow", 0.95, None)
    assert status == "ask_parent" and confidence == 0.69 and has_transcript is False
    assert "confidence is reduced" in explanation
    assert queued == 1


def test_ask_parent_without_transcript_stays_ask_parent_with_capped_confidence(Session):
    status, confidence, explanation, _, _ = classify(Session, "askNoTr0001", "ask_parent", 0.55, None)
    assert status == "ask_parent" and confidence == 0.55
    status, confidence, _, _, _ = classify(Session, "askNoTr0002", "ask_parent", 0.90, None)
    assert status == "ask_parent" and confidence == 0.69


@pytest.mark.parametrize("status,confidence", [("allow", 0.95), ("ask_parent", 0.6), ("block", 0.98)])
def test_with_a_transcript_verdicts_are_untouched(Session, status, confidence):
    got_status, got_conf, explanation, has_transcript, _ = classify(
        Session, f"hasTr{status[:3]}001", status, confidence, "some transcript"
    )
    assert (got_status, got_conf) == (status, confidence) and has_transcript is True
    assert "unavailable" not in explanation
