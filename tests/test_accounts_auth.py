"""Accounts, auth, family/child profiles, ownership isolation, policy layering.

Run from the backend root:  JWT_SECRET=$(openssl rand -hex 32) pytest tests/
Uses an in-memory SQLite DB (via a get_db override) and a fake model, so it
makes no network calls and spends no Nebius credits.
"""

import asyncio
import os
from types import SimpleNamespace

os.environ.setdefault("JWT_SECRET", "test-secret-" + "x" * 40)

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import ChannelRule, Classification, Feedback, Profile
from app.policy import combine_policies
from app.policy_templates import AGE_BANDS, template_for_age
from app.service import ClassificationService

VIDEO_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


@pytest.fixture()
def env(monkeypatch):
    async def no_context(db, settings, video_ids, youtube=None):
        return {}  # never call YouTube from these tests

    monkeypatch.setattr("app.main.get_video_context", no_context)
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
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


def register(client, email="ana@example.com", family="Rivera", policy="No scary stuff."):
    res = client.post(
        "/auth/register",
        json={
            "parent_name": "Ana",
            "email": email,
            "password": "correct-horse-battery",
            "family_name": family,
            "family_policy_text": policy,
        },
    )
    assert res.status_code == 201, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}, res.json()


def add_kid(client, headers, name="Leo", age=7, **extra):
    res = client.post("/profiles", json={"name": name, "age": age, **extra}, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


# --- auth -------------------------------------------------------------------


def test_register_login_me(env):
    client, _ = env
    headers, data = register(client, email="  Ana@Example.COM ")
    assert data["user"]["email"] == "ana@example.com"
    assert data["user"]["family"]["name"] == "Rivera"
    assert "password" not in str(data).lower().replace("password_hash", "")  # nothing leaks
    me = client.get("/me", headers=headers).json()
    assert me["name"] == "Ana" and me["family"]["policy_text"] == "No scary stuff."

    ok = client.post("/auth/login", json={"email": "ANA@example.com", "password": "correct-horse-battery"})
    assert ok.status_code == 200 and ok.json()["access_token"]
    bad = client.post("/auth/login", json={"email": "ana@example.com", "password": "wrong-password"})
    unknown = client.post("/auth/login", json={"email": "nobody@example.com", "password": "wrong-password"})
    assert bad.status_code == unknown.status_code == 401
    assert bad.json() == unknown.json()  # no user enumeration


def test_register_validation_and_duplicate_email(env):
    client, _ = env
    register(client)
    dup = client.post(
        "/auth/register",
        json={"parent_name": "B", "email": "ANA@example.com", "password": "another-password", "family_name": "X"},
    )
    assert dup.status_code == 409
    weak = client.post(
        "/auth/register",
        json={"parent_name": "B", "email": "b@example.com", "password": "short", "family_name": "X"},
    )
    assert weak.status_code == 422
    bad_email = client.post(
        "/auth/register",
        json={"parent_name": "B", "email": "not-an-email", "password": "long-enough-pw", "family_name": "X"},
    )
    assert bad_email.status_code == 422


def test_protected_routes_require_a_valid_token(env):
    client, _ = env
    for method, path in [("get", "/me"), ("get", "/profiles"), ("get", "/family"), ("get", "/policy-templates")]:
        assert getattr(client, method)(path).status_code == 401
    assert client.get("/me", headers={"Authorization": "Bearer garbage"}).status_code == 401
    assert client.get("/health").status_code == 200


def test_expired_token_rejected(env, monkeypatch):
    client, _ = env
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "-1")
    headers, _ = register(client)
    assert client.get("/me", headers=headers).status_code == 401


# --- family & child profiles ------------------------------------------------


def test_kid_defaults_to_age_template(env):
    client, _ = env
    headers, _ = register(client)
    kid = add_kid(client, headers, age=7)
    assert kid["policy_source"] == "template"
    assert kid["policy_text"] == template_for_age(7).policy_text


def test_policy_source_is_derived_from_the_text(env):
    client, _ = env
    headers, _ = register(client)
    custom = add_kid(client, headers, name="A", age=7, policy_text="Only science and animals.")
    assert custom["policy_source"] == "custom"
    same_as_template = add_kid(client, headers, name="B", age=7, policy_text=template_for_age(7).policy_text)
    assert same_as_template["policy_source"] == "template"


def test_every_age_has_a_template(env):
    client, _ = env
    headers, _ = register(client)
    for age in range(3, 18):
        res = client.get(f"/policy-templates/{age}", headers=headers)
        assert res.status_code == 200 and res.json()["policy_text"]
    assert client.get("/policy-templates/2", headers=headers).status_code == 422
    assert client.get("/policy-templates/18", headers=headers).status_code == 422
    assert len(client.get("/policy-templates", headers=headers).json()) == len(AGE_BANDS)


def test_max_five_kids_per_family(env):
    client, _ = env
    headers, _ = register(client)
    for i in range(5):
        add_kid(client, headers, name=f"Kid{i}")
    sixth = client.post("/profiles", json={"name": "Six", "age": 8}, headers=headers)
    assert sixth.status_code == 400
    # the cap is per family, not global
    other_headers, _ = register(client, email="bo@example.com", family="Chen")
    add_kid(client, other_headers, name="Mei")
    assert len(client.get("/profiles", headers=other_headers).json()) == 1


def test_age_change_follows_template_but_not_custom(env):
    client, _ = env
    headers, _ = register(client)
    on_template = add_kid(client, headers, name="T", age=8)
    on_custom = add_kid(client, headers, name="C", age=8, policy_text="My own rules.")

    t = client.put(f"/profiles/{on_template['id']}", json={"age": 9}, headers=headers).json()
    assert t["age"] == 9 and t["policy_source"] == "template"
    assert t["policy_text"] == template_for_age(9).policy_text

    c = client.put(f"/profiles/{on_custom['id']}", json={"age": 9}, headers=headers).json()
    assert c["policy_text"] == "My own rules." and c["policy_source"] == "custom"


def test_editing_template_makes_it_custom(env):
    client, _ = env
    headers, _ = register(client)
    kid = add_kid(client, headers, age=10)
    edited = client.put(
        f"/profiles/{kid['id']}", json={"policy_text": "Science only."}, headers=headers
    ).json()
    assert edited["policy_source"] == "custom"


def test_family_policy_update_and_profile_update_me(env):
    client, _ = env
    headers, _ = register(client)
    fam = client.put("/family", json={"policy_text": "", "name": "Rivera-Chen"}, headers=headers).json()
    assert fam["policy_text"] == "" and fam["name"] == "Rivera-Chen"
    assert client.put("/me", json={"name": "Ana R."}, headers=headers).json()["name"] == "Ana R."


# --- ownership isolation ----------------------------------------------------


def seed_ask_parent(Session, profile_id):
    with Session() as db:
        row = Classification(
            profile_id=profile_id, video_id="abcdefghijk", status="ask_parent",
            explanation="unsure", confidence=0.5,
        )
        db.add(row)
        db.add(ChannelRule(profile_id=profile_id, channel_id="UC1", action="block"))
        db.commit()
        return row.id


def test_families_cannot_touch_each_others_children(env):
    client, Session = env
    a_headers, _ = register(client, email="a@example.com", family="A")
    b_headers, _ = register(client, email="b@example.com", family="B")
    kid = add_kid(client, a_headers, name="AKid")
    cls_id = seed_ask_parent(Session, kid["id"])
    pid = kid["id"]

    forbidden = [
        client.get(f"/profiles/{pid}", headers=b_headers),
        client.put(f"/profiles/{pid}", json={"name": "hacked"}, headers=b_headers),
        client.delete(f"/profiles/{pid}", headers=b_headers),
        client.get(f"/profiles/{pid}/queue", headers=b_headers),
        client.get(f"/profiles/{pid}/channel-rules", headers=b_headers),
        client.get(f"/profiles/{pid}/channel-rules/lookup", params={"channel_id": "UC1"}, headers=b_headers),
        client.post(f"/profiles/{pid}/channel-rules", json={"channel_id": "UC9", "action": "allow"}, headers=b_headers),
        client.post("/classify", json={"profile_id": pid, "youtube_url": VIDEO_URL}, headers=b_headers),
        client.post(f"/queue/{cls_id}/resolve", json={"decision": "approve"}, headers=b_headers),
    ]
    assert [r.status_code for r in forbidden] == [404] * len(forbidden)
    # indistinguishable from a genuinely missing id
    missing = client.get("/profiles/99999", headers=b_headers)
    assert missing.json() == forbidden[0].json()
    # B's list doesn't include A's kid; A's data is untouched
    assert client.get("/profiles", headers=b_headers).json() == []
    assert client.get(f"/profiles/{pid}", headers=a_headers).json()["name"] == "AKid"
    with Session() as db:
        assert db.get(Classification, cls_id).status == "ask_parent"


def test_owner_can_resolve_and_feedback_is_recorded(env):
    client, Session = env
    headers, _ = register(client)
    kid = add_kid(client, headers)
    cls_id = seed_ask_parent(Session, kid["id"])
    res = client.post(f"/queue/{cls_id}/resolve", json={"decision": "approve", "comment": "fine"}, headers=headers)
    assert res.status_code == 200
    assert client.get(f"/profiles/{kid['id']}/queue", headers=headers).json() == []


def test_deleting_a_child_cascades(env):
    client, Session = env
    headers, _ = register(client)
    kid = add_kid(client, headers)
    cls_id = seed_ask_parent(Session, kid["id"])
    client.post(f"/queue/{cls_id}/resolve", json={"decision": "block"}, headers=headers)
    assert client.delete(f"/profiles/{kid['id']}", headers=headers).status_code == 204
    with Session() as db:
        assert db.scalars(select(Profile)).all() == []
        assert db.scalars(select(Classification)).all() == []
        assert db.scalars(select(ChannelRule)).all() == []
        assert db.scalars(select(Feedback)).all() == []


# --- policy layering --------------------------------------------------------


def test_combine_policies():
    assert combine_policies("", "child rules") == "child rules"  # no family policy -> unchanged
    both = combine_policies("family rules", "child rules")
    assert both.index("family rules") < both.index("child rules")
    assert "takes precedence" in both


class FakeYouTube:
    async def get_metadata(self, video_id):
        return {"title": "t", "description": "d", "channel": "c", "channel_id": "UCfake"}


class FakeTranscripts:
    async def get_transcript(self, video_id):
        return "some transcript"


class FakeModel:
    def __init__(self):
        self.policies = []

    async def classify(self, policy, metadata, transcript, feedback):
        self.policies.append(policy)
        return {"status": "allow", "explanation": "ok", "confidence": 0.9}

    async def explain_channel_rule(self, action, channel_name, reason):
        return "rule"


def run_classify(Session, profile_id, model):
    settings = SimpleNamespace(tavily_api_key=None, ttl_for=lambda name: 3600)
    with Session() as db:
        profile = db.get(Profile, profile_id)
        service = ClassificationService(
            db, settings, youtube=FakeYouTube(), model=model, transcript_provider=FakeTranscripts()
        )
        return asyncio.run(service.classify(profile, VIDEO_URL))


def test_model_sees_combined_policy_and_family_edits_invalidate_cache(env):
    client, Session = env
    headers, _ = register(client, policy="FAMILY-RULE-ONE")
    kid = add_kid(client, headers, policy_text="CHILD-RULE-ONE")
    model = FakeModel()

    _, cached = run_classify(Session, kid["id"], model)
    assert cached is False and len(model.policies) == 1
    assert "FAMILY-RULE-ONE" in model.policies[0] and "CHILD-RULE-ONE" in model.policies[0]

    _, cached = run_classify(Session, kid["id"], model)  # same policy -> cache hit, no model call
    assert cached is True and len(model.policies) == 1

    client.put("/family", json={"policy_text": "FAMILY-RULE-TWO"}, headers=headers)
    _, cached = run_classify(Session, kid["id"], model)  # family edit -> re-classify
    assert cached is False and len(model.policies) == 2 and "FAMILY-RULE-TWO" in model.policies[1]

    client.put(f"/profiles/{kid['id']}", json={"policy_text": "CHILD-RULE-TWO"}, headers=headers)
    _, cached = run_classify(Session, kid["id"], model)  # child edit -> re-classify
    assert cached is False and len(model.policies) == 3 and "CHILD-RULE-TWO" in model.policies[2]


class StatusByPolicyModel:
    """Returns allow under policy A text, block under anything else."""

    def __init__(self):
        self.calls = 0

    async def classify(self, policy, metadata, transcript, feedback):
        self.calls += 1
        if "POLICY-A" in policy:
            return {"status": "allow", "explanation": "allowed under A", "confidence": 0.91}
        return {"status": "block", "explanation": "blocked under B", "confidence": 0.92}

    async def explain_channel_rule(self, action, channel_name, reason):
        return "rule"


def test_policy_edit_cycle_a_b_a_reuses_the_matching_row_not_the_latest(env):
    # Regression: after A → B → A, a cache hit for A must not return B's row.
    client, Session = env
    headers, _ = register(client, policy="")
    kid = add_kid(client, headers, policy_text="POLICY-A")
    model = StatusByPolicyModel()

    row_a, cached = run_classify(Session, kid["id"], model)
    assert cached is False and row_a.status == "allow" and model.calls == 1

    client.put(f"/profiles/{kid['id']}", json={"policy_text": "POLICY-B"}, headers=headers)
    row_b, cached = run_classify(Session, kid["id"], model)
    assert cached is False and row_b.status == "block" and model.calls == 2

    client.put(f"/profiles/{kid['id']}", json={"policy_text": "POLICY-A"}, headers=headers)
    row_again, cached = run_classify(Session, kid["id"], model)
    assert cached is True and model.calls == 2
    assert row_again.status == "allow"
    assert row_again.id == row_a.id
    assert row_again.explanation == "allowed under A"
