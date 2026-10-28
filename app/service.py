import asyncio
import hashlib
import json
import logging
import time
from typing import Any, Protocol
from urllib.parse import urlsplit

from sqlalchemy import and_, desc, or_, select
from sqlalchemy.orm import Session

from app.cache import Cache
from app.config import Settings
from app.models import ChannelRule, Classification, Feedback, Profile
from app.nebius import PROMPT_VERSION, NebiusClassifier
from app.policy import policy_hash  # noqa: F401  (re-exported; moved to app/policy.py)
from app.transcript import (
    ChainedTranscriptProvider,
    TavilyTranscriptProvider,
    TranscriptProvider,
    YoutubeTranscriptApiProvider,
)
from app.youtube import YouTubeClient, extract_video_id
from app.redis_cache import RedisCachedTranscriptProvider, get_redis_client
from app.rules import evaluate_pre_model_rules, rule_layer_enabled

# uvicorn's own logger, so these lines show in the terminal running the server.
logger = logging.getLogger("uvicorn.error")

# The longest a check waits for a transcript before judging from the title and
# description alone. Overridable with a `transcript_timeout_seconds` setting.
DEFAULT_TRANSCRIPT_TIMEOUT_SECONDS = 20.0


class MetadataProvider(Protocol):
    async def get_metadata(self, video_id: str) -> dict[str, str]:
        ...


class ClassificationModel(Protocol):
    async def classify(
        self,
        policy: str,
        metadata: dict[str, str],
        transcript: str | None,
        feedback: list[dict[str, str]],
    ) -> dict[str, Any]:
        ...

    async def explain_channel_rule(
        self, action: str, channel_name: str, reason: str | None
    ) -> str:
        ...


class ClassificationService:
    # Coordinates metadata, channel rules, transcript retrieval, model inference, and persistence.
    def __init__(
        self,
        db: Session,
        settings: Settings,
        youtube: MetadataProvider | None = None,
        model: ClassificationModel | None = None,
        transcript_provider: TranscriptProvider | None = None,
    ):
        self.db = db
        self.settings = settings
        self.cache = Cache(db)
        self.youtube = youtube or YouTubeClient(settings)
        self.model = model or NebiusClassifier(settings)
        self.last_timings = {"metadata": 0.0, "redis": 0.0, "transcript": 0.0, "model": 0.0}
        self.suppress_timing_log = False
        # Was NullTranscriptProvider(), then TavilyTranscriptProvider alone --
        # now a chain, since the Week 1 checkpoint found Tavily's "no
        # transcript" result is ambiguous, not confirmed (see
        # ChainedTranscriptProvider's docstring for the full reasoning and
        # the IP-blocking/latency tradeoff this reintroduces). Whatever
        # still comes back None after BOTH providers is a genuine,
        # documented gap -- that's what falls through to the safety net
        # below (transcript unavailable -> an allow is downgraded to
        # ask_parent with reduced confidence; a block stays a block) and
        # gets named honestly in the README.
        # The transient-vs-confirmed-absence caching below (transcript_key /
        # ttl_for("transcript") / ttl_for("transcript_failure")) is
        # unchanged and applies to this provider exactly as it did before.
        if transcript_provider is not None:
            self.transcript_provider = transcript_provider
        else:
            providers: list[TranscriptProvider] = [YoutubeTranscriptApiProvider()]
            if settings.tavily_api_key:
                tavily: TranscriptProvider = TavilyTranscriptProvider(settings.tavily_api_key)
                redis_enabled = bool(settings.redis_url) and getattr(settings, "redis_enabled", None) is not False
                if redis_enabled:
                    tavily = RedisCachedTranscriptProvider(
                        tavily,
                        get_redis_client(
                            settings.redis_url,
                            getattr(settings, "redis_connect_timeout_seconds", 0.3),
                            getattr(settings, "redis_socket_timeout_seconds", 0.3),
                        ),
                        settings.redis_cache_tz,
                        breaker_seconds=getattr(settings, "redis_circuit_breaker_seconds", 60.0),
                    )
                providers.insert(0, tavily)
            self.transcript_provider = transcript_provider or ChainedTranscriptProvider(providers)

    # Runs the deterministic or model-based classification flow for one video.
    async def classify(self, profile: Profile, youtube_url: str) -> tuple[Classification, bool]:
        profile.effective_policy
        video_id = extract_video_id(youtube_url)
        started = time.perf_counter()
        self.last_timings = {"metadata": 0.0, "redis": 0.0, "transcript": 0.0, "model": 0.0}
        stages: dict[str, str] = {}  # what each stage took, for the one-line timing log

        # Metadata is fetched BEFORE the classification cache/channel-rule
        # check (not after, as it used to be) because the channel-rule
        # check needs channel_id up front, and doing it first means a rule
        # added *after* a video was already cached under the old policy
        # correctly overrides that stale cache entry on the very next
        # lookup, instead of silently serving the old result.
        metadata_key = f"metadata:{video_id}"
        metadata_started = time.perf_counter()
        metadata = self.cache.get(metadata_key)
        stage = time.perf_counter()
        if metadata is None:
            metadata = await self.youtube.get_metadata(video_id)
            self.cache.set(metadata_key, metadata, self.settings.ttl_for("metadata"))
            stages["metadata"] = f"{time.perf_counter() - stage:.2f}s fetched"
        else:
            stages["metadata"] = "cached"
        self.last_timings["metadata"] = time.perf_counter() - metadata_started

        # --- Deterministic path -------------------------------------------------
        # A channel with an explicit allow/block rule is a deterministic
        # decision: no classification cache lookup, no Nemotron call for
        # the decision itself. This is checked FIRST and short-circuits
        # everything below -- the non-deterministic (natural-language
        # policy + model) path only ever runs for channels with no rule.
        channel_rule = self._find_channel_rule(profile.id, metadata.get("channel_id", ""))
        if channel_rule is not None and rule_layer_enabled(self.settings, "channel_rule"):
            # "cached" is reported False since this isn't served from the
            # classification cache -- it's recomputed (cheaply, via a plain
            # DB lookup) every time and can never go stale.
            result = await self._record_channel_rule(profile, video_id, metadata, channel_rule)
            self._log_timing(video_id, profile, "channel rule", stages, started)
            return result, False

        parent_answer = self._parent_answer(profile.id, video_id)
        if parent_answer is not None and rule_layer_enabled(self.settings, "parent_answer"):
            status = "allow" if parent_answer.decision in {"approve", "allow"} else "block"
            row = self._record_pre_model_decision(
                profile,
                video_id,
                metadata,
                status,
                "You already decided this video.",
                "parent_answer",
            )
            self._log_timing(video_id, profile, "parent answer", stages, started)
            return row, False

        rule = evaluate_pre_model_rules(
            profile,
            profile.family,
            metadata,
            "/shorts/" in urlsplit(youtube_url).path,
            self.settings,
        )
        if rule is not None:
            row = self._record_pre_model_decision(
                profile,
                video_id,
                metadata,
                rule.status,
                rule.explanation,
                rule.decided_by,
            )
            self._log_timing(video_id, profile, rule.decided_by, stages, started)
            return row, False

        # --- Non-deterministic path ----------------------------------------------
        # No rule for this channel -> fall through to natural-language
        # policy evaluation via Nemotron, same as before.
        # profile.policy_hash is computed over the EFFECTIVE policy (family +
        # child text), so editing either one changes this key and a cached
        # result is only ever reused under the exact policy that produced it.
        channel_id = metadata.get("channel_id", "")
        channel_rule_state = self._channel_rule_cache_state(profile.id, video_id, channel_id)
        feedback = self._recent_feedback(profile.id)
        feedback_state = hashlib.sha256(
            json.dumps(feedback, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        cache_key = (
            f"classification:{PROMPT_VERSION}:{profile.id}:{video_id}:{profile.policy_hash}:"
            f"{channel_rule_state}:{feedback_state}"
        )
        cached = None if self._newer_deterministic_row_exists(profile.id, video_id) else self.cache.get(cache_key)
        if cached is not None:
            # Match both the cache key and the stored row on policy_hash so an
            # A → B → A edit cycle cannot reuse a newer row written under B
            # when the classification cache still holds A's result.
            existing = self.db.scalars(
                select(Classification)
                .where(
                    Classification.profile_id == profile.id,
                    Classification.video_id == video_id,
                    Classification.policy_hash == profile.policy_hash,
                    or_(
                        Classification.decided_by == "model",
                        and_(
                            Classification.decided_by.is_(None),
                            Classification.decided_by_channel_rule.is_(False),
                        ),
                    ),
                )
                .order_by(desc(Classification.created_at))
                .limit(1)
            ).first()
            self._log_timing(video_id, profile, "cached result", stages, started)
            if existing is not None:
                return existing, True
            return self._record_classification(profile, video_id, metadata, cached), True

        transcript_key = f"transcript:{video_id}"
        transcript_started = time.perf_counter()
        cached_transcript = self.cache.get(transcript_key)
        stage = time.perf_counter()
        if isinstance(cached_transcript, dict) and "text" in cached_transcript:
            transcript = cached_transcript["text"]
            stages["transcript"] = "cached"
        else:
            try:
                # A provider that hangs must not leave the parent watching a
                # spinner forever: past the budget the check goes ahead without
                # a transcript (and the safety net below applies).
                transcript = await asyncio.wait_for(
                    self.transcript_provider.get_transcript(video_id), timeout=self._transcript_timeout()
                )
                # Provider returned cleanly (even if None, meaning "this
                # video genuinely has no captions") — safe to cache for the
                # full TTL.
                transcript_ttl = self.settings.ttl_for("transcript")
                stages["transcript"] = f"{time.perf_counter() - stage:.2f}s fetched" + ("" if transcript else ", none found")
            except Exception as exc:
                # Provider raised or timed out: network blip, rate limit, etc.
                # This is indistinguishable from "no transcript" to the caller,
                # but unlike a genuine absence it may resolve itself soon, so
                # don't lock the video into reduced-confidence ask_parent
                # for the full transcript TTL (7+ days).
                transcript = None
                transcript_ttl = self.settings.ttl_for("transcript_failure")
                reason = "timed out" if isinstance(exc, asyncio.TimeoutError) else f"failed ({type(exc).__name__})"
                stages["transcript"] = f"{time.perf_counter() - stage:.2f}s {reason}"
            self.cache.set(transcript_key, {"text": transcript}, transcript_ttl)
        self.last_timings["transcript"] = time.perf_counter() - transcript_started
        self.last_timings["redis"] = self._redis_transcript_seconds()

        stage = time.perf_counter()
        result_data: dict[str, Any] = await self.model.classify(
            profile.effective_policy, metadata, transcript, feedback
        )
        stages["model"] = f"{time.perf_counter() - stage:.2f}s"
        self.last_timings["model"] = time.perf_counter() - stage
        if transcript is None:
            # A missing transcript is missing EVIDENCE, so it only weakens a
            # PERMISSIVE verdict:
            #  - allow -> ask_parent (confidence capped): the transcript might
            #    contain what the title/description hid.
            #  - ask_parent stays ask_parent (confidence capped).
            #  - block stays block: a block from title/description alone is
            #    already sufficient, and no missing transcript can make the
            #    video safer. Sending it to the queue would ask the parent to
            #    re-decide something their own policy already forbids.
            if result_data["status"] == "block":
                result_data["explanation"] += (
                    " Decided from the title and description; the transcript was unavailable."
                )
            else:
                result_data["confidence"] = min(result_data["confidence"], 0.69)
                result_data["status"] = "ask_parent"
                result_data["explanation"] += " Transcript was unavailable, so confidence is reduced."
        result_data["transcript_available"] = transcript is not None
        self.cache.set(cache_key, result_data, self.settings.ttl_for("classification"))
        self._log_timing(video_id, profile, "classified", stages, started)
        return self._record_classification(profile, video_id, metadata, result_data), False

    def _redis_transcript_seconds(self) -> float:
        pending = [self.transcript_provider]
        visited: set[int] = set()
        total = 0.0
        while pending:
            provider = pending.pop()
            if id(provider) in visited:
                continue
            visited.add(id(provider))
            total += float(getattr(provider, "last_redis_seconds", 0.0))
            pending.extend(getattr(provider, "providers", ()))
            inner = getattr(provider, "_inner", None)
            if inner is not None:
                pending.append(inner)
        return total

    def _transcript_timeout(self) -> float:
        return float(getattr(self.settings, "transcript_timeout_seconds", DEFAULT_TRANSCRIPT_TIMEOUT_SECONDS))

    def _log_timing(
        self, video_id: str, profile: Profile, path: str, stages: dict[str, str], started: float
    ) -> None:
        # One line per check, so a slow one shows which stage took the time.
        if self.suppress_timing_log:
            return
        detail = ", ".join(f"{name} {value}" for name, value in stages.items())
        logger.info("check %s for profile %s: %s | %s | total %.2fs", video_id, profile.id, path, detail, time.perf_counter() - started)

    def _find_channel_rule(self, profile_id: int, channel_id: str) -> ChannelRule | None:
        # Finds an explicit profile rule for a channel when one exists.
        if not channel_id:
            return None
        return self.db.scalar(
            select(ChannelRule).where(
                ChannelRule.profile_id == profile_id,
                ChannelRule.channel_id == channel_id,
            )
        )

    def _channel_rule_cache_state(self, profile_id: int, video_id: str, channel_id: str) -> str:
        # A prior rule-derived result marks the cache namespace after that profile's rule was removed.
        if not channel_id:
            return "no-channel-rule-history"
        latest_rule_result_id = self.db.scalar(
            select(Classification.id)
            .where(
                Classification.profile_id == profile_id,
                Classification.video_id == video_id,
                Classification.channel_id == channel_id,
                Classification.decided_by_channel_rule.is_(True),
            )
            .order_by(desc(Classification.id))
            .limit(1)
        )
        if latest_rule_result_id is None:
            return "no-channel-rule-history"
        return f"after-channel-rule-{latest_rule_result_id}"

    def _newer_deterministic_row_exists(self, profile_id: int, video_id: str) -> bool:
        deterministic = (
            "channel_rule",
            "parent_answer",
            "youtube_age_restricted",
            "word_rule",
            "format_rule",
            "trusted_channel",
        )
        latest_rule_id = self.db.scalar(
            select(Classification.id)
            .where(
                Classification.profile_id == profile_id,
                Classification.video_id == video_id,
                or_(
                    Classification.decided_by.in_(deterministic),
                    and_(
                        Classification.decided_by.is_(None),
                        Classification.decided_by_channel_rule.is_(True),
                    ),
                ),
            )
            .order_by(desc(Classification.id))
            .limit(1)
        )
        if latest_rule_id is None:
            return False
        latest_model_id = self.db.scalar(
            select(Classification.id)
            .where(
                Classification.profile_id == profile_id,
                Classification.video_id == video_id,
                or_(
                    Classification.decided_by == "model",
                    and_(
                        Classification.decided_by.is_(None),
                        Classification.decided_by_channel_rule.is_(False),
                    ),
                ),
            )
            .order_by(desc(Classification.id))
            .limit(1)
        )
        return latest_model_id is None or latest_rule_id > latest_model_id

    # Persists or reuses the classification produced by a deterministic channel rule.
    async def _record_channel_rule(
        self,
        profile: Profile,
        video_id: str,
        metadata: dict[str, str],
        channel_rule: ChannelRule,
    ) -> Classification:
        # If this exact video was already recorded under a channel rule,
        # reuse that row (and skip re-calling the model for phrasing)
        # rather than inserting a duplicate every time the same video is
        # looked up. Safe to reuse regardless of age: we've just confirmed
        # a rule for this channel STILL exists (via _find_channel_rule
        # above), so the decision can't have gone stale even if the row is
        # old. If the rule's action changed (allow -> block or vice versa)
        # since that row was written, decided_by_channel_rule=True rows are
        # still matched here regardless of status, so this reuse is only
        # correct as long as a channel can't have two simultaneous rules --
        # enforced by the DB's unique constraint on (profile_id, channel_id).
        existing = self.db.scalars(
            select(Classification)
            .where(
                Classification.profile_id == profile.id,
                Classification.video_id == video_id,
                Classification.decided_by_channel_rule.is_(True),
                Classification.status == channel_rule.action,
            )
            .order_by(desc(Classification.created_at))
            .limit(1)
        ).first()
        if existing is not None:
            return existing

        channel_name = metadata.get("channel") or channel_rule.channel_name or "this channel"
        verb = "allowed" if channel_rule.action == "allow" else "blocked"
        list_name = "always-allowed" if channel_rule.action == "allow" else "blocked"
        explanation = f"This video is {verb} because {channel_name} is on your {list_name} channels list."
        if channel_rule.reason:
            explanation += f" Your note: {channel_rule.reason}."
        classification = Classification(
            profile_id=profile.id,
            video_id=video_id,
            channel_id=channel_rule.channel_id,
            status=channel_rule.action,
            explanation=explanation,
            confidence=1.0,
            transcript_available=False,
            decided_by_channel_rule=True,
            decided_by="channel_rule",
            policy_hash=profile.policy_hash,
        )
        self.db.add(classification)
        self.db.commit()
        self.db.refresh(classification)
        return classification

    def _parent_answer(self, profile_id: int, video_id: str) -> Feedback | None:
        if not video_id:
            return None
        return self.db.scalars(
            select(Feedback)
            .join(Feedback.classification)
            .where(
                Classification.profile_id == profile_id,
                Classification.video_id == video_id,
                Feedback.decision.in_(("approve", "allow", "block")),
            )
            .order_by(desc(Feedback.created_at), desc(Feedback.id))
            .limit(1)
        ).first()

    def _record_pre_model_decision(
        self,
        profile: Profile,
        video_id: str,
        metadata: dict[str, str],
        status: str,
        explanation: str,
        decided_by: str,
    ) -> Classification:
        existing = self.db.scalars(
            select(Classification)
            .where(
                Classification.profile_id == profile.id,
                Classification.video_id == video_id,
                Classification.decided_by == decided_by,
                Classification.status == status,
            )
            .order_by(desc(Classification.id))
            .limit(1)
        ).first()
        if existing is not None:
            existing.explanation = explanation
            existing.channel_id = metadata.get("channel_id", "")
            self.db.commit()
            self.db.refresh(existing)
            return existing
        classification = Classification(
            profile_id=profile.id,
            video_id=video_id,
            channel_id=metadata.get("channel_id", ""),
            status=status,
            explanation=explanation,
            confidence=1.0,
            transcript_available=False,
            decided_by_channel_rule=False,
            decided_by=decided_by,
            policy_hash=profile.policy_hash,
        )
        self.db.add(classification)
        self.db.commit()
        self.db.refresh(classification)
        return classification

    def _recent_feedback(self, profile_id: int) -> list[dict[str, str]]:
        # Retrieves the latest parent feedback for model context.
        rows = self.db.scalars(
            select(Feedback)
            .join(Feedback.classification)
            .where(Classification.profile_id == profile_id)
            .order_by(desc(Feedback.created_at))
            .limit(10)
        ).all()
        return [{"decision": row.decision, "comment": row.comment or ""} for row in rows]

    # Persists a model classification and refreshes its database representation.
    def _record_classification(
        self, profile: Profile, video_id: str, metadata: dict[str, str], result: dict[str, Any]
    ) -> Classification:
        unresolved = self.db.scalars(
            select(Classification)
            .where(
                Classification.profile_id == profile.id,
                Classification.video_id == video_id,
                Classification.status == "ask_parent",
                ~Classification.feedback.any(),
            )
            .order_by(desc(Classification.id))
            .limit(1)
        ).first()
        if unresolved is not None:
            unresolved.channel_id = metadata.get("channel_id", "")
            unresolved.status = result["status"]
            unresolved.explanation = result["explanation"]
            unresolved.confidence = result["confidence"]
            unresolved.transcript_available = result.get("transcript_available", False)
            unresolved.decided_by_channel_rule = False
            unresolved.decided_by = "model"
            unresolved.policy_hash = profile.policy_hash
            self.db.commit()
            self.db.refresh(unresolved)
            return unresolved
        classification = Classification(
            profile_id=profile.id,
            video_id=video_id,
            channel_id=metadata.get("channel_id", ""),
            status=result["status"],
            explanation=result["explanation"],
            confidence=result["confidence"],
            transcript_available=result.get("transcript_available", False),
            decided_by="model",
            policy_hash=profile.policy_hash,
        )
        self.db.add(classification)
        self.db.commit()
        self.db.refresh(classification)
        return classification
