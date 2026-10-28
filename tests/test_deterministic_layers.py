import asyncio
import json
import os

os.environ.setdefault("JWT_SECRET", "test-secret-" + "x" * 40)

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.cache import Cache
from app.config import Settings
from app.db import Base, ensure_columns, get_db
from app.main import app
from app.models import ChannelRule, Classification, Family, Feedback, Profile
from app.service import ClassificationService
from app.youtube import YouTubeClient
from app import rules as rule_module
from evals.common import FakeModel, FakeTranscriptProvider, FakeYouTube


class RecordingTranscriptProvider(FakeTranscriptProvider):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.calls = 0

    async def get_transcript(self, video_id):
        self.calls += 1
        return await super().get_transcript(video_id)


@pytest.fixture()
def layer_env():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_db():
        with Session() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    response = client.post(
        "/auth/register",
        json={
            "parent_name": "Parent",
            "email": "rules@example.com",
            "password": "correct-horse-battery",
            "family_name": "Family",
        },
    )
    assert response.status_code == 201, response.text
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    kid = client.post("/profiles", json={"name": "Ava", "age": 8}, headers=headers).json()
    yield client, Session, headers, kid
    app.dependency_overrides.clear()
    engine.dispose()


def _seed_answer(Session, profile_id, video_id, channel_id, decision):
    with Session() as db:
        row = Classification(
            profile_id=profile_id,
            video_id=video_id,
            channel_id=channel_id,
            status="ask_parent",
            explanation="Review",
            confidence=0.5,
        )
        db.add(row)
        db.flush()
        db.add(Feedback(classification_id=row.id, decision=decision))
        Cache(db).set(
            f"metadata:{video_id}",
            {"title": video_id, "description": "", "channel": "Learning Place", "channel_id": channel_id},
            3600,
        )
        db.commit()


def test_parent_answer_is_profile_scoped_and_short_circuits(layer_env):
    _client, Session, _headers, kid = layer_env
    with Session() as db:
        profile = db.get(Profile, kid["id"])
        other = Profile(family_id=profile.family_id, name="Ben", age=10, policy_text=profile.policy_text)
        db.add(other)
        db.commit()
        db.refresh(other)
        other_id = other.id

    _seed_answer(Session, kid["id"], "parentvid01", "UCparent", "approve")
    model = FakeModel()
    transcript = RecordingTranscriptProvider(text="unused")
    with Session() as db:
        profile = db.get(Profile, kid["id"])
        service = ClassificationService(
            db,
            Settings(),
            youtube=FakeYouTube(),
            model=model,
            transcript_provider=transcript,
        )
        first, cached = asyncio.run(
            service.classify(profile, "https://www.youtube.com/watch?v=parentvid01")
        )
        second, _ = asyncio.run(
            service.classify(profile, "https://www.youtube.com/watch?v=parentvid01")
        )
        assert first.status == "allow"
        assert first.explanation == "You already decided this video."
        assert first.decided_by == "parent_answer"
        assert first.confidence == 1.0 and not cached
        assert second.id == first.id
        assert model.calls == 0
        assert transcript.calls == 0
        count = db.scalar(
            select(func.count(Classification.id)).where(
                Classification.profile_id == kid["id"],
                Classification.video_id == "parentvid01",
                Classification.decided_by == "parent_answer",
            )
        )
        assert count == 1

    scoped_model = FakeModel()
    scoped_transcript = RecordingTranscriptProvider(text="transcript")
    with Session() as db:
        profile = db.get(Profile, other_id)
        result, _ = asyncio.run(
            ClassificationService(
                db,
                Settings(),
                youtube=FakeYouTube(),
                model=scoped_model,
                transcript_provider=scoped_transcript,
            ).classify(profile, "https://www.youtube.com/watch?v=parentvid01")
        )
        assert result.decided_by == "model"
        assert scoped_model.calls == 1
        assert scoped_transcript.calls == 1


def test_channel_rule_suggestions_threshold_unanimity_and_existing_rule(layer_env):
    client, Session, headers, kid = layer_env
    profile_id = kid["id"]
    for index, decision in enumerate(("approve", "approve"), 1):
        _seed_answer(Session, profile_id, f"answer{index:07d}", "UCsuggested", decision)

    url = f"/profiles/{profile_id}/channel-rule-suggestions"
    assert client.get(url, headers=headers).json() == []

    second_auth = client.post(
        "/auth/register",
        json={
            "parent_name": "Other",
            "email": "other-rules@example.com",
            "password": "correct-horse-battery",
            "family_name": "Other Family",
        },
    )
    other_headers = {"Authorization": f"Bearer {second_auth.json()['access_token']}"}
    other_kid = client.post(
        "/profiles", json={"name": "Ben", "age": 7}, headers=other_headers
    ).json()
    assert client.get(
        f"/profiles/{other_kid['id']}/channel-rule-suggestions", headers=headers
    ).status_code == 404

    _seed_answer(Session, profile_id, "answer000003", "UCsuggested", "approve")
    suggestions = client.get(url, headers=headers).json()
    assert suggestions == [
        {"channel_id": "UCsuggested", "channel_name": "Learning Place", "action": "allow", "answers": 3}
    ]

    response = client.post(
        f"/profiles/{profile_id}/channel-rules",
        json={"channel_id": "UCsuggested", "channel_name": "Learning Place", "action": "allow"},
        headers=headers,
    )
    assert response.status_code == 201
    assert client.get(url, headers=headers).json() == []


def test_channel_rule_suggestions_exclude_mixed_decisions(layer_env):
    client, Session, headers, kid = layer_env
    for index, decision in enumerate(("approve", "approve", "block"), 1):
        _seed_answer(Session, kid["id"], f"mixed{index:07d}", "UCmixed", decision)
    response = client.get(
        f"/profiles/{kid['id']}/channel-rule-suggestions", headers=headers
    )
    assert response.status_code == 200
    assert response.json() == []


def test_youtube_age_restriction_short_circuits_without_duplicate_rows(layer_env):
    _client, Session, _headers, kid = layer_env
    model = FakeModel()
    transcript = RecordingTranscriptProvider(text="unused")
    youtube = FakeYouTube(
        metadata={
            "title": "Age restricted video",
            "description": "",
            "channel": "Channel",
            "channel_id": "UCrestricted",
            "age_restricted": "true",
            "made_for_kids": "false",
            "live": "none",
            "duration_seconds": "120",
        }
    )
    with Session() as db:
        profile = db.get(Profile, kid["id"])
        service = ClassificationService(
            db, Settings(), youtube=youtube, model=model, transcript_provider=transcript
        )
        first, _ = asyncio.run(
            service.classify(profile, "https://www.youtube.com/watch?v=ageVideo001")
        )
        second, _ = asyncio.run(
            service.classify(profile, "https://www.youtube.com/watch?v=ageVideo001")
        )
        assert first.status == second.status == "block"
        assert first.explanation == "YouTube marks this video as age-restricted."
        assert first.decided_by == "youtube_age_restricted"
        assert first.confidence == 1.0 and second.id == first.id
        assert model.calls == 0 and transcript.calls == 0


def test_missing_youtube_signals_do_not_block(layer_env):
    _client, Session, _headers, kid = layer_env
    model = FakeModel()
    transcript = RecordingTranscriptProvider(text="evidence")
    with Session() as db:
        profile = db.get(Profile, kid["id"])
        row, _ = asyncio.run(
            ClassificationService(
                db,
                Settings(),
                youtube=FakeYouTube(metadata={"title": "Tamil கல்வி", "channel": "கல்வி"}),
                model=model,
                transcript_provider=transcript,
            ).classify(profile, "https://www.youtube.com/watch?v=missSignl01")
        )
        assert row.decided_by == "model"
        assert model.calls == 1 and transcript.calls == 1


def test_youtube_metadata_fields_are_normalized(monkeypatch):
    response = type(
        "Response",
        (),
        {
            "status_code": 200,
            "json": lambda self: {
                "items": [
                    {
                        "snippet": {
                            "title": "Live title",
                            "description": "Details",
                            "channelTitle": "Channel",
                            "channelId": "UCmetadata",
                            "liveBroadcastContent": "upcoming",
                        },
                        "contentDetails": {
                            "duration": "PT1H2M3S",
                            "contentRating": {"ytRating": "ytAgeRestricted"},
                        },
                        "status": {"madeForKids": True},
                    }
                ]
            },
        },
    )()

    class FakeAsyncClient:
        def __init__(self, timeout):
            self.params = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url, params):
            self.params = params
            return response

    monkeypatch.setattr("app.youtube.httpx.AsyncClient", FakeAsyncClient)
    client = YouTubeClient(Settings(youtube_api_key="test"))
    metadata = asyncio.run(client.get_metadata("ageVideo001"))
    assert metadata["age_restricted"] == "true"
    assert metadata["made_for_kids"] == "true"
    assert metadata["live"] == "upcoming"
    assert metadata["duration_seconds"] == "3723"


@pytest.mark.parametrize(
    "title,channel,blocked_words,expected_word",
    [
        ("A FUCK tutorial", "Channel", None, "fuck"),
        ("A lesson", "Bad Word Academy", '["bad word"]', "bad word"),
    ],
)
def test_blocked_word_rules_short_circuit(title, channel, blocked_words, expected_word, layer_env):
    _client, Session, _headers, kid = layer_env
    model = FakeModel()
    transcript = RecordingTranscriptProvider(text="unused")
    youtube = FakeYouTube(
        metadata={
            "title": title,
            "description": "Should not be searched",
            "channel": channel,
            "channel_id": "UCwords",
        }
    )
    with Session() as db:
        family = db.get(Profile, kid["id"]).family
        family.blocked_words = blocked_words
        db.commit()
        profile = db.get(Profile, kid["id"])
        row, _ = asyncio.run(
            ClassificationService(
                db,
                Settings(default_never_words_enabled=blocked_words is None),
                youtube=youtube,
                model=model,
                transcript_provider=transcript,
            ).classify(profile, "https://www.youtube.com/watch?v=wordVideo01")
        )
        assert row.status == "block" and row.decided_by == "word_rule"
        assert expected_word in row.explanation
        assert row.confidence == 1.0
        assert model.calls == 0 and transcript.calls == 0


@pytest.mark.parametrize("title", ["class", "Essex", "தமிழ் கல்வி"])
def test_blocked_word_matching_uses_whole_unicode_tokens(title, layer_env):
    _client, Session, _headers, kid = layer_env
    model = FakeModel()
    transcript = RecordingTranscriptProvider(text="safe transcript")
    with Session() as db:
        profile = db.get(Profile, kid["id"])
        profile.family.blocked_words = json.dumps(["ass"])
        db.commit()
        row, _ = asyncio.run(
            ClassificationService(
                db,
                Settings(default_never_words_enabled=False),
                youtube=FakeYouTube(
                    metadata={"title": title, "description": "", "channel": "", "channel_id": ""}
                ),
                model=model,
                transcript_provider=transcript,
                ).classify(profile, "https://www.youtube.com/watch?v=wholeWord01")
        )
        assert row.decided_by == "model" and row.status == "allow"
        assert model.calls == 1 and transcript.calls == 1


def test_removing_blocked_word_rechecks_instead_of_reusing_rule_row(layer_env):
    _client, Session, _headers, kid = layer_env
    model = FakeModel()
    transcript = RecordingTranscriptProvider(text="ordinary transcript")
    youtube = FakeYouTube(
        metadata={"title": "BAD-word tutorial", "description": "", "channel": "", "channel_id": ""}
    )
    with Session() as db:
        profile = db.get(Profile, kid["id"])
        service = ClassificationService(
            db,
            Settings(default_never_words_enabled=False),
            youtube=youtube,
            model=model,
            transcript_provider=transcript,
        )
        original, _ = asyncio.run(
            service.classify(profile, "https://www.youtube.com/watch?v=removeWrd01")
        )
        profile.family.blocked_words = json.dumps(["bad word"])
        db.commit()
        blocked, _ = asyncio.run(
            service.classify(profile, "https://www.youtube.com/watch?v=removeWrd01")
        )
        profile.family.blocked_words = None
        db.commit()
        second, cached = asyncio.run(
            service.classify(profile, "https://www.youtube.com/watch?v=removeWrd01")
        )
        assert original.decided_by == "model"
        assert blocked.decided_by == "word_rule"
        assert second.decided_by == "model" and second.status == "allow"
        assert cached is False and model.calls == 2 and transcript.calls == 1


@pytest.mark.parametrize(
    "video_id,url_suffix,metadata,format_rules,expected_status",
    [
        (
            "shortVid001",
            "shorts/shortVid001",
            {"live": "none", "duration_seconds": "30"},
            {"shorts": "ask_parent"},
            "ask_parent",
        ),
        (
            "liveVideo01",
            "watch?v=liveVideo01",
            {"live": "upcoming", "duration_seconds": "60"},
            {"live": "block"},
            "block",
        ),
        (
            "longVideo01",
            "watch?v=longVideo01",
            {"live": "none", "duration_seconds": "301"},
            {"long_video_action": "ask_parent", "long_video_minutes": 5},
            "ask_parent",
        ),
    ],
)
def test_format_rules_decide_without_model_or_transcript(
    video_id, url_suffix, metadata, format_rules, expected_status, layer_env
):
    _client, Session, _headers, kid = layer_env
    model = FakeModel()
    transcript = RecordingTranscriptProvider(text="unused")
    details = {"title": "Ordinary video", "description": "", "channel": "", "channel_id": ""}
    details.update(metadata)
    with Session() as db:
        profile = db.get(Profile, kid["id"])
        profile.format_rules = json.dumps(format_rules)
        db.commit()
        row, _ = asyncio.run(
            ClassificationService(
                db,
                Settings(default_never_words_enabled=False),
                youtube=FakeYouTube(metadata=details),
                model=model,
                transcript_provider=transcript,
            ).classify(profile, f"https://www.youtube.com/{url_suffix}")
        )
        assert row.status == expected_status
        assert row.decided_by == "format_rule" and row.confidence == 1.0
        assert model.calls == 0 and transcript.calls == 0


def test_format_defaults_and_threshold_leave_video_to_model(layer_env):
    _client, Session, _headers, kid = layer_env
    model = FakeModel()
    transcript = RecordingTranscriptProvider(text="normal transcript")
    with Session() as db:
        profile = db.get(Profile, kid["id"])
        profile.format_rules = json.dumps(
            {"shorts": "ask_parent", "live": "block", "long_video_action": "block"}
        )
        db.commit()
        row, _ = asyncio.run(
            ClassificationService(
                db,
                Settings(default_never_words_enabled=False),
                youtube=FakeYouTube(
                    metadata={
                        "title": "A Short title but not a Shorts URL",
                        "description": "",
                        "channel": "",
                        "channel_id": "",
                        "live": "none",
                        "duration_seconds": "3600",
                    }
                ),
                model=model,
                transcript_provider=transcript,
            ).classify(profile, "https://www.youtube.com/watch?v=ordinary001")
        )
        assert row.decided_by == "model"
        assert model.calls == 1 and transcript.calls == 1


def test_family_and_profile_rule_settings_are_normalized_and_returned(layer_env):
    client, _Session, headers, kid = layer_env
    family = client.put(
        "/family",
        json={"blocked_words": [" Bad  Word ", "bad word", "MixedCase"]},
        headers=headers,
    )
    assert family.status_code == 200
    assert family.json()["blocked_words"] == ["bad word", "mixedcase"]
    updated = client.put(
        f"/profiles/{kid['id']}",
        json={
            "format_rules": {
                "shorts": "ask_parent",
                "live": "block",
                "long_video_action": "model",
                "long_video_minutes": 45,
            }
        },
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["format_rules"] == {
        "shorts": "ask_parent",
        "live": "block",
        "long_video_action": "model",
        "long_video_minutes": 45,
    }


def test_trusted_channel_is_opt_in_and_short_circuits(layer_env, monkeypatch, tmp_path):
    _client, Session, _headers, kid = layer_env
    monkeypatch.setattr(rule_module, "TRUSTED_CHANNELS", {"bands": {"6–8": ["UCreviewed"]}})
    model = FakeModel()
    transcript = RecordingTranscriptProvider(text="unused")
    metadata = {
        "title": "A calm drawing lesson",
        "description": "",
        "channel": "Reviewed Channel",
        "channel_id": "UCreviewed",
        "age_restricted": "false",
        "live": "none",
        "duration_seconds": "120",
    }
    with Session() as db:
        profile = db.get(Profile, kid["id"])
        row, _ = asyncio.run(
            ClassificationService(
                db,
                Settings(trusted_channels_enabled=True),
                youtube=FakeYouTube(metadata=metadata),
                model=model,
                transcript_provider=transcript,
            ).classify(profile, "https://www.youtube.com/watch?v=trustVid001")
        )
        assert row.status == "allow" and row.decided_by == "trusted_channel"
        assert row.explanation == "From a channel on KidsCrown's trusted children's list."
        assert model.calls == 0 and transcript.calls == 0


def test_pre_model_precedence_order(layer_env, monkeypatch, tmp_path):
    _client, Session, _headers, kid = layer_env
    monkeypatch.setattr(rule_module, "TRUSTED_CHANNELS", {"bands": {"6–8": ["UCpriority"]}})
    model = FakeModel()
    transcript = RecordingTranscriptProvider(text="unused")
    video_id = "priority001"
    youtube = FakeYouTube(
        metadata={
            "title": "ass adventure",
            "description": "",
            "channel": "Priority Channel",
            "channel_id": "UCpriority",
            "age_restricted": "true",
            "live": "none",
            "duration_seconds": "30",
        }
    )
    with Session() as db:
        profile = db.get(Profile, kid["id"])
        profile.family.blocked_words = json.dumps(["ass"])
        profile.format_rules = json.dumps({"shorts": "block"})
        channel_rule = ChannelRule(
            profile_id=profile.id, channel_id="UCpriority", action="block"
        )
        parent_row = Classification(
            profile_id=profile.id,
            video_id=video_id,
            channel_id="UCpriority",
            status="ask_parent",
            explanation="Queue item",
            confidence=0.5,
        )
        db.add_all([channel_rule, parent_row])
        db.flush()
        parent_feedback = Feedback(classification_id=parent_row.id, decision="approve")
        db.add(parent_feedback)
        db.commit()
        service = ClassificationService(
            db,
            Settings(trusted_channels_enabled=True, default_never_words_enabled=False),
            youtube=youtube,
            model=model,
            transcript_provider=transcript,
        )
        url = f"https://www.youtube.com/shorts/{video_id}"

        decisions = []
        decisions.append(asyncio.run(service.classify(profile, url))[0].decided_by)
        db.delete(channel_rule)
        db.commit()
        decisions.append(asyncio.run(service.classify(profile, url))[0].decided_by)
        db.delete(parent_feedback)
        db.commit()
        decisions.append(asyncio.run(service.classify(profile, url))[0].decided_by)

        Cache(db).set(
            f"metadata:{video_id}", {**youtube.metadata, "age_restricted": "false"}, 3600
        )
        decisions.append(asyncio.run(service.classify(profile, url))[0].decided_by)
        Cache(db).set(
            f"metadata:{video_id}",
            {**youtube.metadata, "title": "Gentle drawing lesson", "age_restricted": "false"},
            3600,
        )
        decisions.append(asyncio.run(service.classify(profile, url))[0].decided_by)
        profile.format_rules = json.dumps({"shorts": "model"})
        db.commit()
        decisions.append(asyncio.run(service.classify(profile, url))[0].decided_by)

        assert decisions == [
            "channel_rule",
            "parent_answer",
            "youtube_age_restricted",
            "word_rule",
            "format_rule",
            "trusted_channel",
        ]
        assert model.calls == 0 and transcript.calls == 0


def test_ensure_columns_upgrades_old_sqlite_database_idempotently(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE classifications (id INTEGER PRIMARY KEY)")
        connection.exec_driver_sql("CREATE TABLE families (id INTEGER PRIMARY KEY)")
        connection.exec_driver_sql("CREATE TABLE profiles (id INTEGER PRIMARY KEY)")

    ensure_columns(engine)
    ensure_columns(engine)
    from sqlalchemy import inspect

    inspector = inspect(engine)
    assert {column["name"] for column in inspector.get_columns("classifications")} >= {
        "decided_by",
        "policy_hash",
    }
    assert "blocked_words" in {
        column["name"] for column in inspector.get_columns("families")
    }
    assert "format_rules" in {
        column["name"] for column in inspector.get_columns("profiles")
    }
    engine.dispose()


def test_disabled_word_layer_and_untrusted_channel_fall_through(layer_env, monkeypatch):
    _client, Session, _headers, kid = layer_env
    monkeypatch.setattr(rule_module, "TRUSTED_CHANNELS", {"bands": {"6–8": ["UCtrusted"]}})
    model = FakeModel()
    transcript = RecordingTranscriptProvider(text="evidence")
    with Session() as db:
        profile = db.get(Profile, kid["id"])
        profile.family.blocked_words = json.dumps(["risky phrase"])
        db.commit()
        row, _ = asyncio.run(
            ClassificationService(
                db,
                Settings(
                    default_never_words_enabled=False,
                    trusted_channels_enabled=True,
                    disabled_rule_layers="word_rule",
                ),
                youtube=FakeYouTube(
                    metadata={
                        "title": "risky phrase",
                        "description": "",
                        "channel": "Unreviewed",
                        "channel_id": "UCliptrusted",
                    }
                ),
                model=model,
                transcript_provider=transcript,
            ).classify(profile, "https://www.youtube.com/watch?v=disabled001")
        )
        assert row.decided_by == "model"
        assert model.calls == 1 and transcript.calls == 1


def test_queue_response_reports_decision_source_for_new_and_legacy_rows(layer_env):
    client, Session, headers, kid = layer_env
    with Session() as db:
        model_row = Classification(
            profile_id=kid["id"],
            video_id="queueModel01",
            status="ask_parent",
            explanation="Model result",
            confidence=0.5,
        )
        legacy_channel_row = Classification(
            profile_id=kid["id"],
            video_id="queueRule001",
            status="ask_parent",
            explanation="Old channel rule",
            confidence=1.0,
            decided_by_channel_rule=True,
        )
        db.add_all([model_row, legacy_channel_row])
        db.commit()
    response = client.get(f"/profiles/{kid['id']}/queue", headers=headers)
    assert response.status_code == 200
    by_id = {row["video_id"]: row for row in response.json()}
    assert by_id["queueModel01"]["decided_by"] == "model"
    assert by_id["queueModel01"]["decided_by_channel_rule"] is False
    assert by_id["queueRule001"]["decided_by"] == "channel_rule"
    assert by_id["queueRule001"]["decided_by_channel_rule"] is True
