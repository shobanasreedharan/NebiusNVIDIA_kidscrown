"""Shared helpers for deterministic and robustness evals.

The eval suite intentionally keeps all support code in this package so the app
layers remain untouched and the tests stay offline by default.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db import Base
from app.models import Classification, Family, Profile
from app.service import ClassificationService
from app.policy import combine_policies, policy_hash


DEFAULT_METADATA = {
    "title": "Science video",
    "description": "A simple educational clip.",
    "channel": "Science For Kids",
    "channel_id": "UCscience123",
}


def make_in_memory_session() -> sessionmaker:
    """Create a SQLite session factory suitable for offline tests."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


class FakeYouTube:
    """Minimal metadata provider for deterministic logic tests."""

    def __init__(self, metadata: dict[str, str] | None = None):
        self.metadata = metadata or dict(DEFAULT_METADATA)
        self.calls = 0

    async def get_metadata(self, video_id: str) -> dict[str, str]:
        self.calls += 1
        return dict(self.metadata)


class FakeTranscriptProvider:
    """Provide a transcript or a failure mode to exercise the safety net."""

    def __init__(
        self,
        *,
        text: str | None = "science transcript",
        raises: Exception | None = None,
        sleep_seconds: float = 0.0,
    ):
        self.text = text
        self.raises = raises
        self.sleep_seconds = sleep_seconds

    async def get_transcript(self, video_id: str) -> str | None:
        if self.sleep_seconds:
            import asyncio

            await asyncio.sleep(self.sleep_seconds)
        if self.raises is not None:
            raise self.raises
        return self.text


class FakeModel:
    """Small model stub that records calls and returns scripted results."""

    def __init__(
        self,
        *,
        result: dict[str, Any] | None = None,
        status: str = "allow",
        confidence: float = 0.95,
        explanation: str = "Policy matches.",
        raise_error: Exception | None = None,
        malformed: dict[str, Any] | None = None,
    ):
        self.calls = 0
        self.result = result or {"status": status, "explanation": explanation, "confidence": confidence}
        self.raise_error = raise_error
        self.malformed = malformed

    async def classify(
        self,
        policy: str,
        metadata: dict[str, str],
        transcript: str | None,
        feedback: list[dict[str, str]],
    ) -> dict[str, Any]:
        self.calls += 1
        if self.raise_error is not None:
            raise self.raise_error
        if self.malformed is not None:
            return self.malformed
        return dict(self.result)

    async def explain_channel_rule(self, action: str, channel_name: str, reason: str | None) -> str:
        return f"{channel_name} is on the {action} list."


class RecordingCache:
    """Capture cache writes without persisting them to SQLite."""

    def __init__(self):
        self.data: dict[str, Any] = {}
        self.sets: list[tuple[str, Any, int]] = []

    def get(self, key: str):
        return self.data.get(key)

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        self.data[key] = value
        self.sets.append((key, value, ttl_seconds))


def safe_status_for(transcript: str | None, status: str, confidence: float, explanation: str) -> tuple[str, float, str]:
    """Mirror the production safety-net logic in app/service.py.

    This helper is intentionally kept separate from the service so the eval suite
    can catch drift if the app behavior changes without a corresponding test update.
    """
    if transcript is not None:
        return status, float(confidence), explanation
    if status == "block":
        return status, float(confidence), (
            explanation + " Decided from the title and description; the transcript was unavailable."
        )
    capped = min(float(confidence), 0.69)
    return "ask_parent", capped, explanation + " Transcript was unavailable, so confidence is reduced."


def build_profile(session_factory, *, family_policy: str = "", child_policy: str = "Allow science", family_name: str = "Demo Family", child_name: str = "Ava") -> Profile:
    """Create a family and profile with a known policy setup."""
    with session_factory() as db:
        family = Family(name=family_name, policy_text=family_policy)
        profile = Profile(family=family, name=child_name, age=7, policy_text=child_policy)
        db.add(family)
        db.add(profile)
        db.commit()
        db.refresh(profile)
        return profile


async def run_classification(
    *,
    profile: Profile,
    video_id: str,
    session,
    youtube: FakeYouTube | None = None,
    model: FakeModel | None = None,
    transcript_provider: FakeTranscriptProvider | None = None,
    settings: Settings | None = None,
):
    """Execute a single classification with the service's injectable dependencies."""
    service = ClassificationService(
        session,
        settings or Settings(),
        youtube=youtube or FakeYouTube(),
        model=model or FakeModel(),
        transcript_provider=transcript_provider or FakeTranscriptProvider(text="OK transcript"),
    )
    row, cached = await service.classify(profile, f"https://www.youtube.com/watch?v={video_id}")
    return row, cached, service


def policy_matrix() -> list[tuple[str, str, str]]:
    return [
        ("family only", "Block scary things.", ""),
        ("child only", "", "Allow science and animals."),
        ("merged", "Block scary things.", "Allow science and animals."),
    ]


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        records.append(json.loads(line))
    return records


def write_jsonl(path: str | Path, records: list[dict[str, Any]]) -> None:
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def dependency_detail() -> dict[str, str]:
    return {
        "policy_hash": "app.policy.policy_hash",
        "combine_policies": "app.policy.combine_policies",
        "safety_net": "app.service.ClassificationService.classify",
    }
