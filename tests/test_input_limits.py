import os

os.environ.setdefault("JWT_SECRET", "test-secret-" + "x" * 40)

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import (
    MAX_BLOCKED_WORD_CHARS,
    MAX_BLOCKED_WORD_ITEMS,
    MAX_CHANNEL_RULE_NOTE_CHARS,
    MAX_CHILD_POLICY_CHARS,
    MAX_EFFECTIVE_POLICY_CHARS,
    MAX_FAMILY_POLICY_CHARS,
    Settings,
)
from app.db import Base, get_db
from app.main import app
from app import main as main_module
from app.models import Family, Profile
from app.policy import CHILD_HEADER, FAMILY_HEADER, EffectivePolicyTooLongError, combine_policies
from app.schemas import ChannelRuleCreate, FamilyUpdate, ProfileCreate, RegisterRequest


@pytest.fixture()
def api():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_db():
        with Session() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    main_module._family_request_times.clear()
    yield TestClient(app), Session
    main_module._family_request_times.clear()
    app.dependency_overrides.clear()
    engine.dispose()
def _register(client, email="limits@example.com", family_policy=""):
    response = client.post(
        "/auth/register",
        json={
            "parent_name": "Parent",
            "email": email,
            "password": "correct-horse-battery",
            "family_name": "Family",
            "family_policy_text": family_policy,
        },
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_schema_limits_accept_exact_values_and_reject_one_over():
    RegisterRequest(
        parent_name="Parent",
        email="a@example.com",
        password="correct-horse-battery",
        family_name="Family",
        family_policy_text="f" * MAX_FAMILY_POLICY_CHARS,
    )
    FamilyUpdate(policy_text="f" * MAX_FAMILY_POLICY_CHARS)
    ProfileCreate(
        name="Kid",
        age=7,
        policy_text="c" * MAX_CHILD_POLICY_CHARS,
    )
    ChannelRuleCreate(
        channel_id="UC123",
        action="block",
        reason="n" * MAX_CHANNEL_RULE_NOTE_CHARS,
    )
    FamilyUpdate(
        blocked_words=[
            f"{index:03d}" + "x" * (MAX_BLOCKED_WORD_CHARS - 3)
            for index in range(MAX_BLOCKED_WORD_ITEMS)
        ]
    )

    invalid = (
        lambda: RegisterRequest(
            parent_name="Parent",
            email="a@example.com",
            password="correct-horse-battery",
            family_name="Family",
            family_policy_text="f" * (MAX_FAMILY_POLICY_CHARS + 1),
        ),
        lambda: FamilyUpdate(policy_text="f" * (MAX_FAMILY_POLICY_CHARS + 1)),
        lambda: ProfileCreate(
            name="Kid", age=7, policy_text="c" * (MAX_CHILD_POLICY_CHARS + 1)
        ),
        lambda: ChannelRuleCreate(
            channel_id="UC123",
            action="block",
            reason="n" * (MAX_CHANNEL_RULE_NOTE_CHARS + 1),
        ),
        lambda: FamilyUpdate(
            blocked_words=["bad"] * (MAX_BLOCKED_WORD_ITEMS + 1)
        ),
        lambda: FamilyUpdate(blocked_words=["x" * (MAX_BLOCKED_WORD_CHARS + 1)]),
    )
    for create_invalid in invalid:
        with pytest.raises(ValidationError):
            create_invalid()


def test_combine_policies_accepts_exact_combined_limit_and_rejects_one_over():
    fixed_length = len(FAMILY_HEADER) + 1 + 2 + len(CHILD_HEADER) + 1
    family_length = MAX_EFFECTIVE_POLICY_CHARS - fixed_length - 1000
    child_length = 1000
    assert len(combine_policies("f" * family_length, "c" * child_length)) == MAX_EFFECTIVE_POLICY_CHARS
    with pytest.raises(EffectivePolicyTooLongError):
        combine_policies("f" * (family_length + 1), "c" * child_length)


def test_classify_rejects_oversized_combined_policy_with_422_before_lookup(api):
    client, Session = api
    headers = _register(client, family_policy="")
    profile_response = client.post(
        "/profiles", json={"name": "Kid", "age": 7, "policy_text": "Child rules"}, headers=headers
    )
    profile_id = profile_response.json()["id"]
    with Session() as db:
        profile = db.get(Profile, profile_id)
        profile.family.policy_text = "f" * 2500
        profile.policy_text = "c" * 2000
        db.commit()

    response = client.post(
        "/classify",
        json={"profile_id": profile_id, "youtube_url": "https://youtu.be/dQw4w9WgXcQ"},
        headers=headers,
    )
    assert response.status_code == 422
    assert "combined family and child policy" in response.json()["detail"]
    assert "4,500" in response.json()["detail"]


def test_request_body_middleware_allows_limit_and_rejects_one_over(api):
    client, _ = api
    exact = client.post("/health", content=b"x" * (64 * 1024))
    oversized = client.post("/health", content=b"x" * (64 * 1024 + 1))
    assert exact.status_code == 405
    assert oversized.status_code == 413
    assert oversized.json() == {"detail": "request_body_too_large"}


def test_classify_and_identify_share_family_rate_limit(api, monkeypatch):
    client, _ = api
    headers = _register(client)
    monkeypatch.setattr(
        main_module,
        "get_settings",
        lambda: Settings(_env_file=None, classify_identify_requests_per_family_per_hour=2),
    )

    class NoMatchYouTube:
        def __init__(self, settings):
            pass

        async def identify_video(self, title, channel, duration_seconds=None):
            return None

    monkeypatch.setattr(main_module, "YouTubeClient", NoMatchYouTube)
    profile_response = client.post("/profiles", json={"name": "Kid", "age": 7}, headers=headers)
    profile_id = profile_response.json()["id"]

    for _ in range(2):
        response = client.post(
            "/identify",
            json={"profile_id": profile_id, "title": "A video", "channel": "A channel"},
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json() == {
            "identified": False,
            "video_id": None,
            "match_confidence": None,
            "classification": None,
        }

    limited = client.post(
        "/classify",
        json={"profile_id": profile_id, "youtube_url": "https://youtu.be/dQw4w9WgXcQ"},
        headers=headers,
    )
    assert limited.status_code == 429
    assert "hourly video-check limit" in limited.json()["detail"]
