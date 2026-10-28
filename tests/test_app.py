from pathlib import Path
import os

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db import Base
from app.models import Classification, Family, Profile
from app.service import ClassificationService
from app.transcript import ChainedTranscriptProvider, YoutubeTranscriptApiProvider

os.environ["JWT_SECRET"] = "test-secret-" + "x" * 40


def test_health_and_profile_crud(tmp_path: Path, monkeypatch):
    database = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database}")

    from app import config
    from app import db
    config.get_settings.cache_clear()
    db.engine.dispose()
    db.engine = db.create_engine(f"sqlite:///{database}", connect_args={"check_same_thread": False})
    db.SessionLocal.configure(bind=db.engine)

    from app.main import app
    db.Base.metadata.create_all(bind=db.engine)
    client = TestClient(app)

    assert client.get("/health").json() == {"status": "ok"}
    registered = client.post(
        "/auth/register",
        json={
            "parent_name": "Parent",
            "email": "parent@example.com",
            "password": "correct-horse-battery",
            "family_name": "Family",
            "family_policy_text": "Allow science; block violence.",
        },
    )
    assert registered.status_code == 201
    headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
    created = client.post(
        "/profiles",
        json={"name": "Ava", "age": 7, "policy_text": "Allow science; block violence."},
        headers=headers,
    )
    assert created.status_code == 201
    profile = created.json()
    assert profile["policy_source"] == "custom"

    updated = client.put(
        f"/profiles/{profile['id']}",
        json={"policy_text": "Allow animals; block violence."},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["policy_text"] != profile["policy_text"]

    rule = client.post(
        f"/profiles/{profile['id']}/channel-rules",
        json={"channel_id": "channel-1", "channel_name": "Science Kids", "action": "allow"},
        headers=headers,
    )
    assert rule.status_code == 201
    duplicate_rule = client.post(
        f"/profiles/{profile['id']}/channel-rules",
        json={"channel_id": "channel-1", "action": "block"},
        headers=headers,
    )
    assert duplicate_rule.status_code == 400
    lookup = client.get(
        f"/profiles/{profile['id']}/channel-rules/lookup",
        params={"channel_id": "channel-1"},
        headers=headers,
    )
    assert lookup.status_code == 200
    assert lookup.json()["action"] == "allow"
    assert client.delete(
        f"/profiles/{profile['id']}/channel-rules/{rule.json()['id']}"
    , headers=headers).status_code == 204

    session = db.SessionLocal()
    try:
        queued = Classification(
            profile_id=profile["id"],
            video_id="queued-video",
            status="ask_parent",
            explanation="Needs a parent decision.",
            confidence=0.5,
        )
        session.add(queued)
        session.commit()
        session.refresh(queued)
        queued_id = queued.id
    finally:
        session.close()

    queue_response = client.get(f"/profiles/{profile['id']}/queue", headers=headers)
    assert queue_response.status_code == 200
    assert queue_response.json()[0]["video_id"] == "queued-video"
    resolved = client.post(
        f"/queue/{queued_id}/resolve", json={"decision": "approve", "comment": "Reviewed"}
        , headers=headers
    )
    assert resolved.status_code == 200
    assert resolved.json()["decision"] == "approve"
    assert client.get(f"/profiles/{profile['id']}/queue", headers=headers).json() == []


class FakeYouTube:
    calls = 0

    async def get_metadata(self, video_id: str) -> dict[str, str]:
        self.calls += 1
        return {"title": "Science", "description": "A lesson", "channel": "Kids"}


class FakeModel:
    calls = 0

    async def classify(self, policy, metadata, transcript, feedback):
        self.calls += 1
        return {"status": "allow", "explanation": "Science content.", "confidence": 0.95}

    async def explain_channel_rule(self, action, channel_name, reason):
        return f"{channel_name} is on the {action} list."


class FakeTranscript:
    async def get_transcript(self, video_id: str) -> str:
        return "A science lesson."


def test_classification_cache_avoids_second_model_call(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'service.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    family = Family(name="Family", policy_text="")
    profile = Profile(family=family, name="Ava", age=7, policy_text="Allow science")
    session.add(profile)
    session.commit()
    session.refresh(profile)
    youtube = FakeYouTube()
    model = FakeModel()
    service = ClassificationService(
        session, Settings(), youtube=youtube, model=model, transcript_provider=FakeTranscript()
    )

    import asyncio

    first, first_cached = asyncio.run(service.classify(profile, "https://youtu.be/dQw4w9WgXcQ"))
    second, second_cached = asyncio.run(service.classify(profile, "https://youtu.be/dQw4w9WgXcQ"))

    assert first.status == second.status == "allow"
    assert first_cached is False
    assert second_cached is True
    assert model.calls == 1
    assert youtube.calls == 1


def test_default_transcript_chain_works_without_tavily_key(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'provider.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    service = ClassificationService(
        session,
        Settings(tavily_api_key=None),
        youtube=FakeYouTube(),
        model=FakeModel(),
    )

    assert len(service.transcript_provider.providers) == 1
    assert isinstance(service.transcript_provider.providers[0], YoutubeTranscriptApiProvider)
