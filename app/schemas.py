import json
import re
import unicodedata
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from app.config import (
    MAX_BLOCKED_WORD_CHARS,
    MAX_BLOCKED_WORD_ITEMS,
    MAX_CHANNEL_RULE_NOTE_CHARS,
    MAX_CHILD_POLICY_CHARS,
    MAX_FAMILY_POLICY_CHARS,
)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
BlockedWord = Annotated[str, Field(min_length=2, max_length=MAX_BLOCKED_WORD_CHARS)]


# --- Auth & family ----------------------------------------------------------


class RegisterRequest(BaseModel):
    parent_name: str = Field(min_length=1, max_length=120)
    email: str = Field(max_length=254)
    password: str = Field(min_length=8, max_length=128)
    family_name: str = Field(min_length=1, max_length=120)
    # General rules for every kid; may be left blank and filled in later.
    family_policy_text: str = Field(default="", max_length=MAX_FAMILY_POLICY_CHARS)

    @field_validator("email")
    @classmethod
    def _valid_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not _EMAIL_RE.match(value):
            raise ValueError("Enter a valid email address")
        return value


class LoginRequest(BaseModel):
    email: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class FamilyResponse(BaseModel):
    id: int
    name: str
    policy_text: str
    blocked_words: list[str] = Field(default_factory=list)

    @field_validator("blocked_words", mode="before")
    @classmethod
    def _parse_blocked_words(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            return json.loads(value)
        return value

    model_config = {"from_attributes": True}


class FamilyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    # Empty string is allowed here: it clears the family policy.
    policy_text: str | None = Field(default=None, max_length=MAX_FAMILY_POLICY_CHARS)
    blocked_words: list[BlockedWord] | None = Field(
        default=None, max_length=MAX_BLOCKED_WORD_ITEMS
    )

    @field_validator("blocked_words")
    @classmethod
    def _normalize_blocked_words(cls, value):
        if value is None:
            return None
        normalized = []
        seen = set()
        for item in value:
            word = " ".join(unicodedata.normalize("NFKC", item).split()).casefold()
            if not 2 <= len(word) <= MAX_BLOCKED_WORD_CHARS:
                raise ValueError(f"Blocked words must be 2 to {MAX_BLOCKED_WORD_CHARS} characters")
            if word not in seen:
                normalized.append(word)
                seen.add(word)
        return normalized


class MeResponse(BaseModel):
    id: int
    name: str
    email: str
    family: FamilyResponse

    model_config = {"from_attributes": True}


class MeUpdate(BaseModel):
    # Email is read-only in v1 (changing it would need verification).
    name: str = Field(min_length=1, max_length=120)


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    user: MeResponse


# --- Policy templates -------------------------------------------------------


class PolicyTemplateResponse(BaseModel):
    band: str
    min_age: int
    max_age: int
    policy_text: str


# --- Child profiles ---------------------------------------------------------


class FormatRules(BaseModel):
    shorts: Literal["model", "ask_parent", "block"] = "model"
    live: Literal["model", "ask_parent", "block"] = "model"
    long_video_action: Literal["model", "ask_parent", "block"] = "model"
    long_video_minutes: int = Field(default=60, ge=5, le=600)


class ProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    age: int = Field(ge=3, le=17)
    # Omit to start from the age band's template; provide to use a custom
    # policy (or an edited template). `policy_source` is derived server-side.
    policy_text: str | None = Field(default=None, min_length=1, max_length=MAX_CHILD_POLICY_CHARS)
    format_rules: FormatRules | None = None


class ProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    age: int | None = Field(default=None, ge=3, le=17)
    policy_text: str | None = Field(default=None, min_length=1, max_length=MAX_CHILD_POLICY_CHARS)
    format_rules: FormatRules | None = None


class ProfileResponse(BaseModel):
    id: int
    family_id: int
    name: str
    age: int
    policy_text: str
    policy_source: Literal["template", "custom"]
    format_rules: FormatRules | None = None
    created_at: datetime

    @field_validator("format_rules", mode="before")
    @classmethod
    def _parse_format_rules(cls, value):
        if isinstance(value, str):
            return json.loads(value)
        return value

    model_config = {"from_attributes": True}


# --- Classification ---------------------------------------------------------


class ClassificationRequest(BaseModel):
    profile_id: int
    youtube_url: str = Field(min_length=1)


class ClassificationResponse(BaseModel):
    id: int
    profile_id: int
    video_id: str
    channel_id: str
    status: Literal["allow", "ask_parent", "block"]
    explanation: str
    confidence: float
    transcript_available: bool
    decided_by_channel_rule: bool
    decided_by: str | None = None
    cached: bool = False
    # Looked up when the response is built (see app/video_context.py), never
    # stored. None when the video can't be looked up (deleted, private, quota).
    title: str | None = None
    channel_name: str | None = None
    created_at: datetime

    @model_validator(mode="before")
    @classmethod
    def _normalize_decision_attribution(cls, value):
        if isinstance(value, dict):
            data = dict(value)
        else:
            data = {
                name: getattr(value, name)
                for name in cls.model_fields
                if hasattr(value, name)
            }
        decided_by = data.get("decided_by")
        if decided_by is None:
            decided_by = "channel_rule" if data.get("decided_by_channel_rule") else "model"
        data["decided_by"] = decided_by
        data["decided_by_channel_rule"] = decided_by == "channel_rule"
        return data

    model_config = {"from_attributes": True}


class FeedbackRequest(BaseModel):
    decision: Literal["approve", "block"]
    comment: str | None = None


class FeedbackResponse(BaseModel):
    id: int
    classification_id: int
    decision: str
    comment: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ChannelRuleCreate(BaseModel):
    channel_id: str = Field(min_length=1, max_length=64)
    # Display name is optional and only used for phrasing the explanation /
    # showing a friendly label in the rules-list UI -- channel_id is the
    # actual key checked against video metadata.
    channel_name: str | None = Field(default=None, max_length=200)
    action: Literal["allow", "block"]
    # Optional parent note, e.g. "too much toy-unboxing content" or
    # "our favorite science channel" -- woven into the LLM-generated
    # explanation shown for videos from this channel.
    reason: str | None = Field(default=None, max_length=MAX_CHANNEL_RULE_NOTE_CHARS)


class ChannelRuleResponse(BaseModel):
    id: int
    profile_id: int
    channel_id: str
    channel_name: str | None
    action: Literal["allow", "block"]
    reason: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ChannelRuleSuggestion(BaseModel):
    channel_id: str
    channel_name: str
    action: Literal["allow", "block"]
    answers: int
    
class VideoSearchResult(BaseModel):
    video_id: str
    title: str
    channel: str
    channel_id: str
    thumbnail: str    

class IdentifyRequest(BaseModel):
    profile_id: int
    title: str = Field(min_length=1, max_length=200)
    channel: str = Field(min_length=1, max_length=100)
    duration_seconds: int | None = Field(default=None, ge=1)


class IdentifyResponse(BaseModel):
    identified: bool
    video_id: str | None = None
    match_confidence: Literal["high", "medium"] | None = None
    classification: ClassificationResponse | None = None