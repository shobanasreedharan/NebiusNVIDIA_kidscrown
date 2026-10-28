"""Deterministic evals for the classification pipeline.

These tests intentionally use the real service logic with fake metadata/model/
transcript dependencies so our offline suite is cheap and reproducible.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.config import Settings
from app.models import ChannelRule, Classification, Family, Feedback, Profile
from app.nebius import ModelError
from app.policy import CHILD_HEADER, FAMILY_HEADER, combine_policies, policy_hash
from app.service import ClassificationService
from evals.common import FakeModel, FakeTranscriptProvider, FakeYouTube, safe_status_for


@pytest.fixture()
def session_factory():
    from evals.common import make_in_memory_session

    return make_in_memory_session()


def _seed_profile(session_factory, *, family_policy: str = "", child_policy: str = "Allow science", family_name: str = "Demo Family", child_name: str = "Ava"):
    with session_factory() as db:
        family = Family(name=family_name, policy_text=family_policy)
        profile = Profile(family=family, name=child_name, age=7, policy_text=child_policy)
        db.add(family)
        db.add(profile)
        db.commit()
        db.refresh(profile)
        return profile


class FeedbackRecordingModel(FakeModel):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.feedback_contexts = []
        self.policy_contexts = []

    async def classify(self, policy, metadata, transcript, feedback):
        self.feedback_contexts.append([dict(item) for item in feedback])
        self.policy_contexts.append(policy)
        return await super().classify(policy, metadata, transcript, feedback)


@pytest.mark.parametrize("status, transcript, expected_status, expected_conf", [
    ("allow", "truth", "allow", 0.95),
    ("ask_parent", "truth", "ask_parent", 0.66),
    ("block", "truth", "block", 0.95),
    ("allow", None, "ask_parent", 0.69),
    ("ask_parent", None, "ask_parent", 0.66),
    ("block", None, "block", 0.95),
])
def test_safety_net_matrix_matches_service(session_factory, status, transcript, expected_status, expected_conf):
    """Ensure the eval helper and the production service use the same rule set."""
    model_confidence = 0.95 if status in {"allow", "block"} else 0.66
    model = FakeModel(status=status, confidence=model_confidence)
    with session_factory() as db:
        family = Family(name="Family", policy_text="")
        profile = Profile(family=family, name="Kid", age=7, policy_text="Allow science")
        db.add(family)
        db.add(profile)
        db.commit()
        db.refresh(profile)
        service = ClassificationService(
            db,
            Settings(),
            youtube=FakeYouTube(),
            model=model,
            transcript_provider=FakeTranscriptProvider(text=transcript),
        )
        row, _ = asyncio.run(service.classify(profile, "https://www.youtube.com/watch?v=abc12345678"))
        assert row.status == expected_status
        assert row.confidence == pytest.approx(expected_conf)

    helper_status, helper_conf, helper_explanation = safe_status_for(transcript, status, model_confidence, "Policy says this is fine.")
    assert helper_status == expected_status
    assert helper_conf == pytest.approx(expected_conf)
    if transcript is None:
        assert "Transcript was unavailable" in helper_explanation or "Decided from the title and description" in helper_explanation


def test_channel_rule_short_circuit_and_override(session_factory):
    """Rules are deterministic and outrank stale classification cache entries."""
    with session_factory() as db:
        family = Family(name="Family", policy_text="")
        profile = Profile(family=family, name="Ava", age=7, policy_text="Allow science")
        db.add(family)
        db.add(profile)
        db.commit()
        db.refresh(profile)

        model = FakeModel(status="allow", confidence=0.90)
        service = ClassificationService(
            db,
            Settings(),
            youtube=FakeYouTube(metadata={"title": "Science", "description": "Learn", "channel": "Study Time", "channel_id": "channel-9"}),
            model=model,
            transcript_provider=FakeTranscriptProvider(text="Some science text"),
        )

        first, _ = asyncio.run(service.classify(profile, "https://www.youtube.com/watch?v=abc12345678"))
        assert first.status == "allow"
        assert model.calls == 1

        from app.models import ChannelRule

        db.add(ChannelRule(profile_id=profile.id, channel_id="channel-9", channel_name="Study Time", action="block", reason="Family rule"))
        db.commit()

        second, _ = asyncio.run(service.classify(profile, "https://www.youtube.com/watch?v=abc12345678"))
        assert second.status == "block"
        assert second.decided_by_channel_rule is True
        assert model.calls == 1

        db.query(ChannelRule).filter_by(profile_id=profile.id, channel_id="channel-9").one().action = "allow"
        db.commit()
        changed, _ = asyncio.run(service.classify(profile, "https://www.youtube.com/watch?v=abc12345678"))
        assert changed.status == "allow"
        assert changed.decided_by_channel_rule is True
        assert model.calls == 1

        db.query(ChannelRule).filter_by(profile_id=profile.id, channel_id="channel-9").delete()
        db.commit()
        third, _ = asyncio.run(service.classify(profile, "https://www.youtube.com/watch?v=abc12345678"))
        assert third.status == "allow"
        assert model.calls == 2
        fourth, cached = asyncio.run(service.classify(profile, "https://www.youtube.com/watch?v=abc12345678"))
        assert fourth.id == third.id
        assert cached is True
        assert model.calls == 2


def test_explicit_channel_allow_overrides_model_block(session_factory):
    with session_factory() as db:
        family = Family(name="Family", policy_text="")
        profile = Profile(family=family, name="Ava", age=7, policy_text="Block gaming")
        db.add_all([family, profile])
        db.commit()
        db.refresh(profile)
        db.add(ChannelRule(profile_id=profile.id, channel_id="UCscience123", action="allow"))
        db.commit()

        model = FakeModel(status="block")
        service = ClassificationService(
            db,
            Settings(),
            youtube=FakeYouTube(),
            model=model,
            transcript_provider=FakeTranscriptProvider(text="unsafe transcript"),
        )

        row, cached = asyncio.run(service.classify(profile, "https://www.youtube.com/watch?v=abc12345678"))

        assert row.status == "allow"
        assert row.decided_by_channel_rule is True
        assert cached is False
        assert model.calls == 0


def test_channel_rules_are_scoped_to_the_matching_profile(session_factory):
    with session_factory() as db:
        family = Family(name="Family", policy_text="")
        profile_with_rule = Profile(family=family, name="Ava", age=7, policy_text="Allow science")
        profile_without_rule = Profile(family=family, name="Ben", age=10, policy_text="Block science")
        db.add_all([family, profile_with_rule, profile_without_rule])
        db.commit()
        db.refresh(profile_with_rule)
        db.refresh(profile_without_rule)
        db.add(ChannelRule(profile_id=profile_with_rule.id, channel_id="UCscience123", action="block"))
        db.commit()

        model = FakeModel(status="allow")
        service = ClassificationService(
            db,
            Settings(),
            youtube=FakeYouTube(),
            model=model,
            transcript_provider=FakeTranscriptProvider(text="science transcript"),
        )
        video_url = "https://www.youtube.com/watch?v=abc12345678"

        ruled_row, _ = asyncio.run(service.classify(profile_with_rule, video_url))
        other_profile_row, _ = asyncio.run(service.classify(profile_without_rule, video_url))

        assert ruled_row.status == "block"
        assert ruled_row.decided_by_channel_rule is True
        assert other_profile_row.status == "allow"
        assert other_profile_row.decided_by_channel_rule is False
        assert model.calls == 1


def test_policy_layering_and_hash_integrity(session_factory):
    """Changing the family child policy should alter the policy hash; renaming a child should not."""
    with session_factory() as db:
        family = Family(name="Family", policy_text="Block scary content.")
        profile = Profile(family=family, name="Ava", age=7, policy_text="Allow science and animals.")
        db.add(family)
        db.add(profile)
        db.commit()
        db.refresh(profile)

        combined = combine_policies(family.policy_text, profile.policy_text)
        assert FAMILY_HEADER in combined
        assert CHILD_HEADER in combined
        assert combined.index(FAMILY_HEADER) < combined.index(CHILD_HEADER)
        assert profile.policy_hash == policy_hash(combined)

        family.policy_text = "Block scary and violent content."
        db.commit()
        assert profile.policy_hash != policy_hash(combined)

        old_hash = profile.policy_hash
        profile.name = "Ava renamed"
        db.commit()
        assert profile.policy_hash == old_hash

        assert combine_policies(None, "Allow science") == "Allow science"
        assert combine_policies("Block scary.", None) == "Block scary."
        assert combine_policies("", "") == ""


def test_transcript_failure_uses_short_ttl_and_returns_result(session_factory):
    """A provider exception is cached as a transient transcript failure and the check still yields a verdict."""
    with session_factory() as db:
        family = Family(name="Family", policy_text="")
        profile = Profile(family=family, name="Kid", age=7, policy_text="Allow science")
        db.add(family)
        db.add(profile)
        db.commit()
        db.refresh(profile)

        class RecordingCache:
            def __init__(self):
                self.calls = []

            def get(self, key):
                return None

            def set(self, key, value, ttl):
                self.calls.append((key, value, ttl))

        service = ClassificationService(
            db,
            Settings(transcript_failure_cache_ttl_seconds=11, transcript_cache_ttl_seconds=99),
            youtube=FakeYouTube(),
            model=FakeModel(status="allow", confidence=0.90),
            transcript_provider=FakeTranscriptProvider(raises=TimeoutError("slow fetch")),
        )
        service.cache = RecordingCache()
        row, _ = asyncio.run(service.classify(profile, "https://www.youtube.com/watch?v=xyz12345678"))
        assert row.status == "ask_parent"
        assert row.transcript_available is False
        assert any(key == "transcript:xyz12345678" for key, _, _ in service.cache.calls)
        assert any(ttl == 11 for _, _, ttl in service.cache.calls)


def test_no_duplicate_classification_rows_on_repeat_cache_hit(session_factory):
    """Repeated identical calls should not duplicate final rows."""
    with session_factory() as db:
        family = Family(name="Family", policy_text="")
        profile = Profile(family=family, name="Ava", age=7, policy_text="Allow science")
        db.add(family)
        db.add(profile)
        db.commit()
        db.refresh(profile)

        service = ClassificationService(
            db,
            Settings(),
            youtube=FakeYouTube(),
            model=FakeModel(status="allow", confidence=0.90),
            transcript_provider=FakeTranscriptProvider(text="Some transcript"),
        )
        row_one, cached_one = asyncio.run(service.classify(profile, "https://www.youtube.com/watch?v=def12345678"))
        row_two, cached_two = asyncio.run(service.classify(profile, "https://www.youtube.com/watch?v=def12345678"))
        rows = db.scalars(select(Classification).where(Classification.video_id == "def12345678")).all()
        assert len(rows) == 1
        assert cached_one is False
        assert cached_two is True
        assert row_one.id == row_two.id


def test_feedback_recent_items_are_limited_to_ten(session_factory):
    """Only the newest feedback entries should be passed to the model."""
    with session_factory() as db:
        family = Family(name="Family", policy_text="")
        profile = Profile(family=family, name="Ava", age=7, policy_text="Allow science")
        db.add(family)
        db.add(profile)
        db.commit()
        db.refresh(profile)

        service = ClassificationService(db, Settings(), youtube=FakeYouTube(), model=FakeModel(), transcript_provider=FakeTranscriptProvider(text="Some transcript"))
        for idx in range(15):
            service.db.add(Classification(profile_id=profile.id, video_id=f"video-{idx}", status="ask_parent", explanation="needs review", confidence=0.5, policy_hash="hash"))
        service.db.commit()
        details = service._recent_feedback(profile.id)
        assert len(details) <= 10


def test_new_feedback_does_not_change_cached_result_yet(session_factory):
    """This xfail documents the current gap rather than asserting a behavior we intend to keep."""
    with session_factory() as db:
        family = Family(name="Family", policy_text="")
        profile = Profile(family=family, name="Ava", age=7, policy_text="Allow science")
        db.add(family)
        db.add(profile)
        db.commit()
        db.refresh(profile)

        model = FakeModel(status="allow", confidence=0.90)
        service = ClassificationService(
            db,
            Settings(),
            youtube=FakeYouTube(),
            model=model,
            transcript_provider=FakeTranscriptProvider(text="Some transcript"),
        )
        first, _ = asyncio.run(service.classify(profile, "https://www.youtube.com/watch?v=aaa12345678"))
        assert first.status == "allow"

        # This is the desired future behavior: a fresh feedback decision should invalidate the cache for the same video and policy.
        db.add(Feedback(classification_id=first.id, decision="block", comment="Parent blocked it."))
        service.db.commit()
        model.result = {"status": "block", "explanation": "Updated from feedback.", "confidence": 0.95}
        second, cached = asyncio.run(service.classify(profile, "https://www.youtube.com/watch?v=aaa12345678"))
        assert second.status == "block"
        assert cached is False
        assert model.calls == 2


def test_model_failure_injection_propagates_and_never_silently_allows(session_factory):
    """Invalid model output or a failed model call is surfaced; the service does not hide it behind an allow."""
    with session_factory() as db:
        family = Family(name="Family", policy_text="")
        profile = Profile(family=family, name="Ava", age=7, policy_text="Allow science")
        db.add(family)
        db.add(profile)
        db.commit()
        db.refresh(profile)

        service = ClassificationService(
            db,
            Settings(),
            youtube=FakeYouTube(),
            model=FakeModel(raise_error=ModelError("bad model")),
            transcript_provider=FakeTranscriptProvider(text="Some transcript"),
        )
        with pytest.raises(ModelError):
            asyncio.run(service.classify(profile, "https://www.youtube.com/watch?v=xyz98765432"))

        malformed = {"status": "allow", "confidence": "not-a-number"}
        service.model = FakeModel(malformed=malformed)
        with pytest.raises((KeyError, TypeError, ValueError, RuntimeError)):
            asyncio.run(service.classify(profile, "https://www.youtube.com/watch?v=qwe98765432"))


@pytest.mark.parametrize("policy_scope", ["family", "child"])
def test_policy_change_prevents_classification_cache_reuse(session_factory, policy_scope):
    with session_factory() as db:
        family = Family(name="Family", policy_text="Family policy A")
        profile = Profile(family=family, name="Ava", age=7, policy_text="Child policy A")
        db.add_all([family, profile])
        db.commit()
        db.refresh(profile)

        model = FakeModel(status="allow", confidence=0.9)
        service = ClassificationService(
            db,
            Settings(),
            youtube=FakeYouTube(),
            model=model,
            transcript_provider=FakeTranscriptProvider(text="Transcript"),
        )
        video_url = "https://www.youtube.com/watch?v=policy1234567"
        first, first_cached = asyncio.run(service.classify(profile, video_url))
        old_policy_hash = profile.policy_hash
        model.result = {"status": "block", "explanation": "Updated policy.", "confidence": 0.95}

        if policy_scope == "family":
            family.policy_text = "Family policy B"
        else:
            profile.policy_text = "Child policy B"
        db.commit()

        second, second_cached = asyncio.run(service.classify(profile, video_url))

        assert profile.policy_hash != old_policy_hash
        assert first.status == "allow"
        assert second.status == "block"
        assert first_cached is False
        assert second_cached is False
        assert model.calls == 2


def test_different_profile_does_not_reuse_another_profiles_classification(session_factory):
    with session_factory() as db:
        family = Family(name="Family", policy_text="Shared family policy")
        first_profile = Profile(family=family, name="Ava", age=7, policy_text="Shared child policy")
        second_profile = Profile(family=family, name="Ben", age=9, policy_text="Shared child policy")
        db.add_all([family, first_profile, second_profile])
        db.commit()
        db.refresh(first_profile)
        db.refresh(second_profile)

        model = FakeModel(status="allow", confidence=0.9)
        service = ClassificationService(
            db,
            Settings(),
            youtube=FakeYouTube(),
            model=model,
            transcript_provider=FakeTranscriptProvider(text="Transcript"),
        )
        video_url = "https://www.youtube.com/watch?v=profile123456"
        first, _ = asyncio.run(service.classify(first_profile, video_url))
        model.result = {"status": "block", "explanation": "Profile-specific result.", "confidence": 0.95}

        second, second_cached = asyncio.run(service.classify(second_profile, video_url))

        assert second_cached is False
        assert second.status == "block"
        assert first.status == "allow"
        assert model.calls == 2


def test_existing_feedback_is_included_in_model_context(session_factory):
    with session_factory() as db:
        family = Family(name="Family", policy_text="")
        profile = Profile(family=family, name="Ava", age=7, policy_text="Allow science")
        db.add_all([family, profile])
        db.commit()
        db.refresh(profile)
        prior_classification = Classification(
            profile_id=profile.id,
            video_id="priorvideo123",
            status="ask_parent",
            explanation="Needs review.",
            confidence=0.6,
            policy_hash=profile.policy_hash,
        )
        db.add(prior_classification)
        db.commit()
        db.refresh(prior_classification)
        db.add(Feedback(classification_id=prior_classification.id, decision="approve", comment="Reviewed"))
        db.commit()

        model = FeedbackRecordingModel(status="allow", confidence=0.9)
        service = ClassificationService(
            db,
            Settings(),
            youtube=FakeYouTube(),
            model=model,
            transcript_provider=FakeTranscriptProvider(text="Transcript"),
        )

        asyncio.run(service.classify(profile, "https://www.youtube.com/watch?v=newvideo12345"))

        assert model.feedback_contexts == [[{"decision": "approve", "comment": "Reviewed"}]]


def test_family_policy_is_shared_with_siblings_and_child_policies_are_isolated(session_factory):
    with session_factory() as db:
        family_one = Family(name="Family One", policy_text="FAMILY-ONE-RULE")
        first_child = Profile(family=family_one, name="Ava", age=7, policy_text="CHILD-AVA-RULE")
        second_child = Profile(family=family_one, name="Ben", age=9, policy_text="CHILD-BEN-RULE")
        family_two = Family(name="Family Two", policy_text="FAMILY-TWO-RULE")
        third_child = Profile(family=family_two, name="Mia", age=8, policy_text="CHILD-MIA-RULE")
        db.add_all([family_one, first_child, second_child, family_two, third_child])
        db.commit()
        db.refresh(first_child)
        db.refresh(second_child)
        db.refresh(third_child)

        model = FeedbackRecordingModel(status="allow", confidence=0.9)
        service = ClassificationService(
            db,
            Settings(),
            youtube=FakeYouTube(),
            model=model,
            transcript_provider=FakeTranscriptProvider(text="Transcript"),
        )
        video_url = "https://www.youtube.com/watch?v=sharedvideo12"
        asyncio.run(service.classify(first_child, video_url))
        asyncio.run(service.classify(second_child, video_url))
        asyncio.run(service.classify(third_child, video_url))

        first_policy, second_policy, third_policy = model.policy_contexts
        assert "FAMILY-ONE-RULE" in first_policy and "FAMILY-ONE-RULE" in second_policy
        assert "FAMILY-TWO-RULE" not in first_policy and "FAMILY-TWO-RULE" not in second_policy
        assert "CHILD-AVA-RULE" in first_policy and "CHILD-AVA-RULE" not in second_policy
        assert "CHILD-BEN-RULE" not in first_policy and "CHILD-BEN-RULE" in second_policy
        assert "FAMILY-TWO-RULE" in third_policy and "FAMILY-ONE-RULE" not in third_policy
        assert "CHILD-MIA-RULE" in third_policy
        assert "CHILD-AVA-RULE" not in third_policy and "CHILD-BEN-RULE" not in third_policy


def test_feedback_from_one_profile_is_not_sent_to_another(session_factory):
    with session_factory() as db:
        family = Family(name="Family", policy_text="Shared family policy")
        first_profile = Profile(family=family, name="Ava", age=7, policy_text="Ava policy")
        second_profile = Profile(family=family, name="Ben", age=9, policy_text="Ben policy")
        db.add_all([family, first_profile, second_profile])
        db.commit()
        db.refresh(first_profile)
        db.refresh(second_profile)
        prior_classification = Classification(
            profile_id=first_profile.id,
            video_id="feedbackprior1",
            status="ask_parent",
            explanation="Needs review.",
            confidence=0.6,
            policy_hash=first_profile.policy_hash,
        )
        db.add(prior_classification)
        db.commit()
        db.refresh(prior_classification)
        db.add(Feedback(classification_id=prior_classification.id, decision="block", comment="Ava's rule"))
        db.commit()

        model = FeedbackRecordingModel(status="allow", confidence=0.9)
        service = ClassificationService(
            db,
            Settings(),
            youtube=FakeYouTube(),
            model=model,
            transcript_provider=FakeTranscriptProvider(text="Transcript"),
        )
        asyncio.run(service.classify(first_profile, "https://www.youtube.com/watch?v=firstvideo12"))
        asyncio.run(service.classify(second_profile, "https://www.youtube.com/watch?v=secondvid12"))

        assert model.feedback_contexts == [
            [{"decision": "block", "comment": "Ava's rule"}],
            [],
        ]


def test_editing_and_removing_relevant_feedback_reclassifies(session_factory):
    with session_factory() as db:
        family = Family(name="Family", policy_text="")
        profile = Profile(family=family, name="Ava", age=7, policy_text="Allow science")
        db.add_all([family, profile])
        db.commit()
        db.refresh(profile)
        prior_classification = Classification(
            profile_id=profile.id,
            video_id="feedbackprior2",
            status="ask_parent",
            explanation="Needs review.",
            confidence=0.6,
            policy_hash=profile.policy_hash,
        )
        db.add(prior_classification)
        db.commit()
        db.refresh(prior_classification)
        feedback = Feedback(classification_id=prior_classification.id, decision="approve", comment="Initial note")
        db.add(feedback)
        db.commit()

        model = FeedbackRecordingModel(status="allow", confidence=0.9)
        service = ClassificationService(
            db,
            Settings(),
            youtube=FakeYouTube(),
            model=model,
            transcript_provider=FakeTranscriptProvider(text="Transcript"),
        )
        video_url = "https://www.youtube.com/watch?v=feedbackstate1"
        first, cached = asyncio.run(service.classify(profile, video_url))
        assert first.status == "allow" and cached is False

        feedback.comment = "Edited note"
        db.commit()
        model.result = {"status": "block", "explanation": "Updated feedback context.", "confidence": 0.95}
        second, cached = asyncio.run(service.classify(profile, video_url))
        assert second.status == "block" and cached is False

        db.delete(feedback)
        db.commit()
        model.result = {"status": "allow", "explanation": "No feedback context.", "confidence": 0.9}
        third, cached = asyncio.run(service.classify(profile, video_url))
        assert third.status == "allow" and cached is False
        assert model.calls == 3
        assert model.feedback_contexts == [
            [{"decision": "approve", "comment": "Initial note"}],
            [{"decision": "approve", "comment": "Edited note"}],
            [],
        ]


def test_other_profiles_feedback_does_not_invalidate_this_profiles_cache(session_factory):
    with session_factory() as db:
        family = Family(name="Family", policy_text="")
        first_profile = Profile(family=family, name="Ava", age=7, policy_text="Shared policy")
        second_profile = Profile(family=family, name="Ben", age=9, policy_text="Shared policy")
        db.add_all([family, first_profile, second_profile])
        db.commit()
        db.refresh(first_profile)
        db.refresh(second_profile)
        other_classification = Classification(
            profile_id=second_profile.id,
            video_id="otherprofile1",
            status="ask_parent",
            explanation="Needs review.",
            confidence=0.6,
            policy_hash=second_profile.policy_hash,
        )
        db.add(other_classification)
        db.commit()
        db.refresh(other_classification)

        model = FeedbackRecordingModel(status="allow", confidence=0.9)
        service = ClassificationService(
            db,
            Settings(),
            youtube=FakeYouTube(),
            model=model,
            transcript_provider=FakeTranscriptProvider(text="Transcript"),
        )
        video_url = "https://www.youtube.com/watch?v=profilefeedback1"
        first, _ = asyncio.run(service.classify(first_profile, video_url))
        db.add(Feedback(classification_id=other_classification.id, decision="block", comment="Ben's note"))
        db.commit()
        model.result = {"status": "block", "explanation": "Should not be requested.", "confidence": 0.95}

        second, cached = asyncio.run(service.classify(first_profile, video_url))

        assert second.id == first.id
        assert second.status == "allow"
        assert cached is True
        assert model.calls == 1
        assert model.feedback_contexts == [[]]


def test_feedback_older_than_model_context_limit_does_not_invalidate_cache(session_factory):
    with session_factory() as db:
        family = Family(name="Family", policy_text="")
        profile = Profile(family=family, name="Ava", age=7, policy_text="Allow science")
        db.add_all([family, profile])
        db.commit()
        db.refresh(profile)
        for index in range(10):
            classification = Classification(
                profile_id=profile.id,
                video_id=f"recentfeedback{index}",
                status="ask_parent",
                explanation="Prior result.",
                confidence=0.6,
                policy_hash=profile.policy_hash,
            )
            db.add(classification)
            db.flush()
            db.add(
                Feedback(
                    classification_id=classification.id,
                    decision="approve",
                    comment=f"Recent note {index}",
                    created_at=datetime(2026, 9, index + 1, tzinfo=timezone.utc),
                )
            )
        db.commit()

        model = FeedbackRecordingModel(status="allow", confidence=0.9)
        service = ClassificationService(
            db,
            Settings(),
            youtube=FakeYouTube(),
            model=model,
            transcript_provider=FakeTranscriptProvider(text="Transcript"),
        )
        video_url = "https://www.youtube.com/watch?v=feedbacklimit1"
        first, cached = asyncio.run(service.classify(profile, video_url))
        assert cached is False
        assert len(model.feedback_contexts[0]) == 10

        old_classification = Classification(
            profile_id=profile.id,
            video_id="olderfeedback1",
            status="ask_parent",
            explanation="Prior result.",
            confidence=0.6,
            policy_hash=profile.policy_hash,
        )
        db.add(old_classification)
        db.commit()
        db.refresh(old_classification)
        db.add(
            Feedback(
                classification_id=old_classification.id,
                decision="block",
                comment="Outside recent context",
                created_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
            )
        )
        db.commit()

        second, cached = asyncio.run(service.classify(profile, video_url))

        assert second.id == first.id
        assert cached is True
        assert model.calls == 1


