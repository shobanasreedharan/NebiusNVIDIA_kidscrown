import asyncio
from collections import deque
import json
import logging
import os
from threading import Lock
import time
from typing import Any
from fastapi import Depends, FastAPI, HTTPException, Path, Query, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.youtube import YouTubeClient, YouTubeError, YouTubeUnavailableError

from app.auth import (
    create_access_token,
    get_current_parent,
    hash_password,
    require_jwt_secret,
    verify_password,
)
from app.cache import Cache
from app.config import get_settings
from app.db import ensure_schema, get_db
from app.models import ChannelRule, Classification, Family, Feedback, Parent, Profile
from app.nebius import ModelError
from app.policy import EffectivePolicyTooLongError
from app.policy_templates import (
    AGE_BANDS,
    MAX_AGE,
    MIN_AGE,
    policy_source_for,
    template_for_age,
)
from app.schemas import (
    ChannelRuleCreate,
    ChannelRuleResponse,
    ChannelRuleSuggestion,
    ClassificationRequest,
    ClassificationResponse,
    FamilyResponse,
    FamilyUpdate,
    FeedbackRequest,
    FeedbackResponse,
    IdentifyRequest,      # new
    IdentifyResponse,     # new
    LoginRequest,
    MeResponse,
    MeUpdate,
    PolicyTemplateResponse,
    ProfileCreate,
    ProfileResponse,
    ProfileUpdate,
    RegisterRequest,
    TokenResponse,
    VideoSearchResult,
)
from app.service import ClassificationService
from app.video_context import get_video_context

MAX_KIDS_PER_FAMILY = 5
logger = logging.getLogger("uvicorn.error")
_family_request_times: dict[int, deque[float]] = {}
_family_request_times_lock = Lock()


class RequestBodyLimitMiddleware:
    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        content_length = next(
            (value for key, value in scope.get("headers", []) if key.lower() == b"content-length"),
            None,
        )
        if content_length is not None:
            try:
                if int(content_length) > self.max_bytes:
                    await self._reject(send)
                    return
            except ValueError:
                pass

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > self.max_bytes:
                await self._reject(send)
                return
            if not message.get("more_body", False):
                break

        replay = {"type": "http.request", "body": bytes(body), "more_body": False}

        async def replay_receive():
            nonlocal replay
            if replay is not None:
                message, replay = replay, None
                return message
            return await receive()

        await self.app(scope, replay_receive, send)

    async def _reject(self, send):
        body = b'{"detail":"request_body_too_large"}'
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
            }
        )
        await send({"type": "http.response.body", "body": body})

# Fail fast at startup rather than on the first login attempt.
require_jwt_secret()

ensure_schema()
app = FastAPI(title="KidsCrown Backend", version="0.2.0")

# The React dev server runs on a different origin. Auth is a bearer header
# (not a cookie), so credentials mode is off and any listed origin is safe to
# allow. Set CORS_ORIGINS to a comma-separated list for the deployed frontend.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
        if origin.strip()
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(
    RequestBodyLimitMiddleware,
    max_bytes=get_settings().request_body_max_bytes,
)


def get_owned_profile(profile_id: int, parent: Parent, db: Session) -> Profile:
    # Loads a child profile only if it belongs to the caller's family.
    # Not-found and not-yours are deliberately indistinguishable (same 404),
    # so IDs from other families can't be probed.
    profile = db.get(Profile, profile_id)
    if profile is None or profile.family_id != parent.family_id:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile


def _validate_effective_policy(profile: Profile) -> None:
    try:
        profile.effective_policy
    except EffectivePolicyTooLongError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _enforce_family_rate_limit(family_id: int) -> None:
    limit = get_settings().classify_identify_requests_per_family_per_hour
    now = time.monotonic()
    with _family_request_times_lock:
        for key, requests in list(_family_request_times.items()):
            while requests and now - requests[0] >= 3600:
                requests.popleft()
            if not requests:
                del _family_request_times[key]
        requests = _family_request_times.setdefault(family_id, deque())
        while requests and now - requests[0] >= 3600:
            requests.popleft()
        if len(requests) >= limit:
            raise HTTPException(
                status_code=429,
                detail="This family has reached the hourly video-check limit. Try again later.",
            )
        requests.append(now)

# Reports that the backend is reachable.
@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# --- Auth -------------------------------------------------------------------


# Creates a family and its parent account, and signs the parent in.
@app.post("/auth/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Session = Depends(get_db)) -> TokenResponse:
    conflict = HTTPException(status_code=409, detail="An account with this email already exists")
    if db.scalar(select(Parent).where(Parent.email == payload.email)) is not None:
        raise conflict
    parent = Parent(
        name=payload.parent_name,
        email=payload.email,
        password_hash=hash_password(payload.password),
        family=Family(name=payload.family_name, policy_text=payload.family_policy_text),
    )
    db.add(parent)
    try:
        db.commit()
    except IntegrityError:
        # Two simultaneous registrations for the same email: the unique
        # constraint is the real guard, the check above is just the fast path.
        db.rollback()
        raise conflict from None
    db.refresh(parent)
    return TokenResponse(
        access_token=create_access_token(parent.id), user=MeResponse.model_validate(parent)
    )


# Signs a parent in. Same 401 for unknown email and wrong password.
@app.post("/auth/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    parent = db.scalar(select(Parent).where(Parent.email == payload.email))
    if not verify_password(payload.password, parent.password_hash if parent else None):
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return TokenResponse(
        access_token=create_access_token(parent.id), user=MeResponse.model_validate(parent)
    )


# Returns the signed-in parent and their family.
@app.get("/me", response_model=MeResponse)
def read_me(parent: Parent = Depends(get_current_parent)) -> Parent:
    return parent


# Updates the parent's display name (email is read-only in v1).
@app.put("/me", response_model=MeResponse)
def update_me(
    payload: MeUpdate, parent: Parent = Depends(get_current_parent), db: Session = Depends(get_db)
) -> Parent:
    parent.name = payload.name
    db.commit()
    db.refresh(parent)
    return parent


# --- Family -----------------------------------------------------------------


# Returns the caller's family, including the general family policy.
@app.get("/family", response_model=FamilyResponse)
def read_family(parent: Parent = Depends(get_current_parent)) -> Family:
    return parent.family


# Updates the family name and/or general policy. Because every child's policy
# hash is computed over family + child text, this automatically invalidates
# cached classifications for all of the family's kids.
@app.put("/family", response_model=FamilyResponse)
def update_family(
    payload: FamilyUpdate,
    parent: Parent = Depends(get_current_parent),
    db: Session = Depends(get_db),
) -> Family:
    family = parent.family
    if payload.name is not None:
        family.name = payload.name
    if payload.policy_text is not None:
        family.policy_text = payload.policy_text
    if "blocked_words" in payload.model_fields_set:
        family.blocked_words = (
            json.dumps(payload.blocked_words, ensure_ascii=False)
            if payload.blocked_words is not None
            else None
        )
    db.commit()
    db.refresh(family)
    return family


# --- Age-band policy templates ---------------------------------------------


# Lists every age band and its generic starting policy.
@app.get("/policy-templates", response_model=list[PolicyTemplateResponse])
def list_policy_templates(
    _parent: Parent = Depends(get_current_parent),
) -> list[PolicyTemplateResponse]:
    return [
        PolicyTemplateResponse(
            band=b.label, min_age=b.min_age, max_age=b.max_age, policy_text=b.policy_text
        )
        for b in AGE_BANDS
    ]


# Returns the suggested policy for one age (used to pre-fill the policy box).
@app.get("/policy-templates/{age}", response_model=PolicyTemplateResponse)
def read_policy_template(
    age: int = Path(ge=MIN_AGE, le=MAX_AGE),
    _parent: Parent = Depends(get_current_parent),
) -> PolicyTemplateResponse:
    b = template_for_age(age)
    return PolicyTemplateResponse(
        band=b.label, min_age=b.min_age, max_age=b.max_age, policy_text=b.policy_text
    )


# --- Child profiles ---------------------------------------------------------


# Lists the family's children, oldest profile first (drives the view switcher).
@app.get("/profiles", response_model=list[ProfileResponse])
def list_profiles(
    parent: Parent = Depends(get_current_parent), db: Session = Depends(get_db)
) -> list[Profile]:
    return db.scalars(
        select(Profile).where(Profile.family_id == parent.family_id).order_by(Profile.created_at, Profile.id)
    ).all()


# Adds a child to the caller's family (max 5). Starts from the age band's
# template unless a policy is supplied.
@app.post("/profiles", response_model=ProfileResponse, status_code=status.HTTP_201_CREATED)
def create_profile(
    payload: ProfileCreate,
    parent: Parent = Depends(get_current_parent),
    db: Session = Depends(get_db),
) -> Profile:
    kids = db.scalar(select(func.count(Profile.id)).where(Profile.family_id == parent.family_id))
    if kids >= MAX_KIDS_PER_FAMILY:
        raise HTTPException(
            status_code=400, detail=f"A family can have at most {MAX_KIDS_PER_FAMILY} children"
        )
    policy_text = (
        payload.policy_text
        if payload.policy_text is not None
        else template_for_age(payload.age).policy_text
    )
    profile = Profile(
        family_id=parent.family_id,
        name=payload.name,
        age=payload.age,
        policy_text=policy_text,
        policy_source=policy_source_for(payload.age, policy_text),
        format_rules=(payload.format_rules.model_dump_json() if payload.format_rules else None),
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


# Returns one child profile (own family only).
@app.get("/profiles/{profile_id}", response_model=ProfileResponse)
def read_profile(
    profile_id: int, parent: Parent = Depends(get_current_parent), db: Session = Depends(get_db)
) -> Profile:
    return get_owned_profile(profile_id, parent, db)


# Applies supplied changes. If the age changes and the child is still on a
# template policy (and no new policy text was sent), the policy follows the
# new age band. Custom policies are never touched by an age change.
@app.put("/profiles/{profile_id}", response_model=ProfileResponse)
def update_profile(
    profile_id: int,
    payload: ProfileUpdate,
    parent: Parent = Depends(get_current_parent),
    db: Session = Depends(get_db),
) -> Profile:
    profile = get_owned_profile(profile_id, parent, db)
    age_changed = payload.age is not None and payload.age != profile.age
    if payload.name is not None:
        profile.name = payload.name
    if payload.age is not None:
        profile.age = payload.age
    if payload.policy_text is not None:
        profile.policy_text = payload.policy_text
    elif age_changed and profile.policy_source == "template":
        profile.policy_text = template_for_age(profile.age).policy_text
    profile.policy_source = policy_source_for(profile.age, profile.policy_text)
    if "format_rules" in payload.model_fields_set:
        profile.format_rules = (
            payload.format_rules.model_dump_json() if payload.format_rules is not None else None
        )
    db.commit()
    db.refresh(profile)
    return profile


# Removes a child and, by cascade, their classifications, feedback, and rules.
@app.delete("/profiles/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_profile(
    profile_id: int, parent: Parent = Depends(get_current_parent), db: Session = Depends(get_db)
) -> None:
    profile = get_owned_profile(profile_id, parent, db)
    db.delete(profile)
    db.commit()


# --- Channel rules ----------------------------------------------------------


# Adds a unique allow or block rule for a profile's channel.
@app.post(
    "/profiles/{profile_id}/channel-rules",
    response_model=ChannelRuleResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_channel_rule(
    profile_id: int,
    payload: ChannelRuleCreate,
    parent: Parent = Depends(get_current_parent),
    db: Session = Depends(get_db),
) -> ChannelRule:
    get_owned_profile(profile_id, parent, db)
    existing = db.scalar(
        select(ChannelRule).where(
            ChannelRule.profile_id == profile_id,
            ChannelRule.channel_id == payload.channel_id,
        )
    )
    if existing is not None:
        raise HTTPException(
            status_code=400,
            detail=f"This channel already has a rule ({existing.action}) for this profile. "
            f"Delete it first if you want to change the action.",
        )
    rule = ChannelRule(
        profile_id=profile_id,
        channel_id=payload.channel_id,
        channel_name=payload.channel_name,
        action=payload.action,
        reason=payload.reason,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


# Lists a profile's channel rules from newest to oldest.
@app.get("/profiles/{profile_id}/channel-rules", response_model=list[ChannelRuleResponse])
def list_channel_rules(
    profile_id: int, parent: Parent = Depends(get_current_parent), db: Session = Depends(get_db)
) -> list[ChannelRule]:
    get_owned_profile(profile_id, parent, db)
    return db.scalars(
        select(ChannelRule)
        .where(ChannelRule.profile_id == profile_id)
        .order_by(desc(ChannelRule.created_at))
    ).all()


@app.get(
    "/profiles/{profile_id}/channel-rule-suggestions",
    response_model=list[ChannelRuleSuggestion],
)
def channel_rule_suggestions(
    profile_id: int,
    min_answers: int = Query(default=3, ge=1),
    parent: Parent = Depends(get_current_parent),
    db: Session = Depends(get_db),
) -> list[ChannelRuleSuggestion]:
    get_owned_profile(profile_id, parent, db)
    answered = db.execute(
        select(
            Classification.channel_id,
            Classification.video_id,
            Feedback.decision,
            Feedback.id,
        )
        .join(Feedback, Feedback.classification_id == Classification.id)
        .where(Classification.profile_id == profile_id, Classification.channel_id != "")
        .order_by(desc(Feedback.created_at), desc(Feedback.id))
    ).all()
    latest_by_video: dict[tuple[str, str], str] = {}
    for channel_id, video_id, decision, _feedback_id in answered:
        latest_by_video.setdefault((channel_id, video_id), decision)

    grouped: dict[str, dict[str, Any]] = {}
    cache = Cache(db)
    for (channel_id, video_id), decision in latest_by_video.items():
        action = "allow" if decision in {"approve", "allow"} else "block"
        channel = grouped.setdefault(
            channel_id,
            {"actions": set(), "videos": set(), "channel_name": ""},
        )
        channel["actions"].add(action)
        channel["videos"].add(video_id)
        if not channel["channel_name"]:
            metadata = cache.get(f"metadata:{video_id}") or {}
            channel["channel_name"] = metadata.get("channel", "")

    existing_rules = set(
        db.scalars(select(ChannelRule.channel_id).where(ChannelRule.profile_id == profile_id)).all()
    )
    suggestions = []
    for channel_id, data in grouped.items():
        answers = len(data["videos"])
        actions = data["actions"]
        if channel_id in existing_rules or len(actions) != 1 or answers < min_answers:
            continue
        suggestions.append(
            ChannelRuleSuggestion(
                channel_id=channel_id,
                channel_name=data["channel_name"],
                action=next(iter(actions)),
                answers=answers,
            )
        )
    return sorted(suggestions, key=lambda suggestion: (-suggestion.answers, suggestion.channel_id))


# Looks up a channel rule for an instant allow/block decision in the UI.
@app.get("/profiles/{profile_id}/channel-rules/lookup", response_model=ChannelRuleResponse | None)
def lookup_channel_rule(
    profile_id: int,
    channel_id: str,
    parent: Parent = Depends(get_current_parent),
    db: Session = Depends(get_db),
) -> ChannelRule | None:
    """Instant, non-billable lookup for the frontend.

    Lets the UI show "Always allowed" / "Always blocked" and skip the
    classifying spinner for a known channel, without waiting on the full
    /classify round-trip (which itself already does this same check
    server-side before deciding whether to call the model -- this endpoint
    exists purely so the UI can reflect that same segregation instantly).
    Returns null (not 404) when no rule exists, since "no rule" is a valid,
    expected answer here, not an error.
    """
    get_owned_profile(profile_id, parent, db)
    return db.scalar(
        select(ChannelRule).where(
            ChannelRule.profile_id == profile_id,
            ChannelRule.channel_id == channel_id,
        )
    )


# Deletes a channel rule belonging to the requested profile.
@app.delete("/profiles/{profile_id}/channel-rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_channel_rule(
    profile_id: int,
    rule_id: int,
    parent: Parent = Depends(get_current_parent),
    db: Session = Depends(get_db),
) -> None:
    get_owned_profile(profile_id, parent, db)
    rule = db.get(ChannelRule, rule_id)
    if rule is None or rule.profile_id != profile_id:
        raise HTTPException(status_code=404, detail="Channel rule not found")
    db.delete(rule)
    db.commit()

# --- Video search -----------------------------------------------------------


# Searches YouTube for embeddable videos. Search results are NOT a decision:
# the UI must still call /classify for the selected child before playing.
@app.get("/search", response_model=list[VideoSearchResult])
async def search_videos(
    q: str = Query(min_length=2, max_length=100),
    _parent: Parent = Depends(get_current_parent),
    ) -> list[VideoSearchResult]:
    try:
        results = await YouTubeClient(get_settings()).search_videos(q)
    except YouTubeUnavailableError as exc:
        error_type = type(exc.__cause__ or exc).__name__
        logger.warning("YouTube lookup unavailable title=%r error=%s", q, error_type)
        raise HTTPException(status_code=503, detail="youtube_lookup_unavailable") from exc
    except YouTubeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return [VideoSearchResult(**r) for r in results]


# --- Classification & Ask Parent -------------------------------------------


# Classifies a video and translates service failures into API responses.
@app.post("/classify", response_model=ClassificationResponse)
async def classify(
    payload: ClassificationRequest,
    parent: Parent = Depends(get_current_parent),
    db: Session = Depends(get_db),
) -> ClassificationResponse:
    profile = get_owned_profile(payload.profile_id, parent, db)
    _validate_effective_policy(profile)
    _enforce_family_rate_limit(profile.family_id)
    try:
        classification, cached = await ClassificationService(db, get_settings()).classify(
            profile, payload.youtube_url
        )
    except EffectivePolicyTooLongError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except YouTubeUnavailableError as exc:
        error_type = type(exc.__cause__ or exc).__name__
        logger.warning(
            "YouTube lookup unavailable title=%r error=%s",
            payload.youtube_url,
            error_type,
        )
        raise HTTPException(status_code=503, detail="youtube_lookup_unavailable") from exc
    except (YouTubeError, ModelError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    response = ClassificationResponse.model_validate(classification)
    # The metadata was just fetched (or was already cached), so this is a cache read.
    context = await get_video_context(db, get_settings(), [classification.video_id])
    return response.model_copy(
        update={"cached": cached, **context.get(classification.video_id, {})}
    )
    
# Called by the Android probe with what it read off the screen. Resolves the
# video ID, then runs the normal classification. identified=false means the
# client should treat the video as unknown (ask_parent), never as allowed.
@app.post("/identify", response_model=IdentifyResponse)
async def identify(
    payload: IdentifyRequest,
    parent: Parent = Depends(get_current_parent),
    db: Session = Depends(get_db),
) -> IdentifyResponse:
    profile = get_owned_profile(payload.profile_id, parent, db)
    _validate_effective_policy(profile)
    _enforce_family_rate_limit(profile.family_id)
    settings = get_settings()
    started = time.perf_counter()
    timings = {
        "youtube_search": 0.0,
        "video_metadata": 0.0,
        "redis": 0.0,
        "transcript": 0.0,
        "model": 0.0,
    }
    youtube = YouTubeClient(settings)

    async def lookup_video_and_metadata():
        lookup_started = time.perf_counter()
        try:
            match = await youtube.identify_video(
                payload.title, payload.channel, payload.duration_seconds
            )
        finally:
            identify_timings = getattr(youtube, "identify_timings", {})
            elapsed = time.perf_counter() - lookup_started
            timings["youtube_search"] = identify_timings.get("youtube_search", elapsed)
            timings["video_metadata"] = identify_timings.get("video_metadata", 0.0)
        if match is not None:
            metadata_key = f"metadata:{match['video_id']}"
            metadata = Cache(db).get(metadata_key)
            if metadata is None:
                metadata_started = time.perf_counter()
                try:
                    metadata = await youtube.get_metadata(match["video_id"])
                    Cache(db).set(metadata_key, metadata, settings.ttl_for("metadata"))
                finally:
                    timings["video_metadata"] += time.perf_counter() - metadata_started
        return match

    try:
        match = await asyncio.wait_for(
            lookup_video_and_metadata(),
            timeout=min(
                12.0,
                float(getattr(settings, "youtube_identify_total_timeout_seconds", 12.0)),
            ),
        )
        if match is None:
            return IdentifyResponse(identified=False)
        service = ClassificationService(db, settings)
        classification, cached = await service.classify(
            profile, match["video_id"]
        )
        service_timings = getattr(service, "last_timings", {})
        timings["redis"] = service_timings.get("redis", 0.0)
        timings["transcript"] = service_timings.get("transcript", 0.0)
        timings["model"] = service_timings.get("model", 0.0)
        context = await get_video_context(db, settings, [classification.video_id], youtube=youtube)
        response = ClassificationResponse.model_validate(classification).model_copy(
            update={"cached": cached, **context.get(classification.video_id, {})}
        )
        return IdentifyResponse(
            identified=True,
            video_id=match["video_id"],
            match_confidence=match["confidence"],
            classification=response,
        )
    except EffectivePolicyTooLongError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (YouTubeUnavailableError, asyncio.TimeoutError) as exc:
        error_type = type(exc.__cause__ or exc).__name__
        logger.warning(
            "YouTube lookup unavailable title=%r error=%s",
            payload.title,
            error_type,
        )
        raise HTTPException(status_code=503, detail="youtube_lookup_unavailable") from exc
    except (YouTubeError, ModelError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    finally:
        logger.info(
            "identify title=%r | youtube_search %.2fs, video_metadata %.2fs, redis %.2fs, "
            "transcript %.2fs, model %.2fs | total %.2fs",
            payload.title,
            timings["youtube_search"],
            timings["video_metadata"],
            timings["redis"],
            timings["transcript"],
            timings["model"],
            time.perf_counter() - started,
        )


# Returns classifications that are waiting for a parent's decision.
@app.get("/profiles/{profile_id}/queue", response_model=list[ClassificationResponse])
async def queue(
    profile_id: int, parent: Parent = Depends(get_current_parent), db: Session = Depends(get_db)
) -> list[ClassificationResponse]:
    get_owned_profile(profile_id, parent, db)
    rows = db.scalars(
        select(Classification)
        .where(Classification.profile_id == profile_id, Classification.status == "ask_parent")
        .order_by(desc(Classification.created_at))
    ).all()
    context = await get_video_context(db, get_settings(), [row.video_id for row in rows])
    return [
        ClassificationResponse.model_validate(row).model_copy(
            update={"cached": False, **context.get(row.video_id, {})}
        )
        for row in rows
    ]


# Records a parent's decision and updates the queued classification status.
@app.post("/queue/{classification_id}/resolve", response_model=FeedbackResponse)
def resolve_queue_item(
    classification_id: int,
    payload: FeedbackRequest,
    parent: Parent = Depends(get_current_parent),
    db: Session = Depends(get_db),
) -> Feedback:
    classification = db.get(Classification, classification_id)
    # This route takes only a classification ID, so ownership is traced
    # classification -> profile -> family. Missing and not-yours are the same 404.
    if classification is None or classification.profile.family_id != parent.family_id:
        raise HTTPException(status_code=404, detail="Classification not found")
    if classification.status != "ask_parent":
        raise HTTPException(status_code=400, detail="Only ask_parent classifications can be resolved")
    feedback = Feedback(
        classification_id=classification_id,
        decision=payload.decision,
        comment=payload.comment,
    )
    classification.status = "allow" if payload.decision == "approve" else "block"
    db.add(feedback)
    db.commit()
    db.refresh(feedback)
    return feedback
