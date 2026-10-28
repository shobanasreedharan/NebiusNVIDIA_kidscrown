from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.policy import combine_policies
from app.policy import policy_hash as compute_policy_hash


def utcnow() -> datetime:
    # Produces timezone-aware timestamps for persisted records.
    return datetime.now(timezone.utc)


class Family(Base):
    """The tenant boundary: every parent, child profile, rule, and
    classification belongs to exactly one family, and every authorization
    check in the API resolves back to `family_id`."""

    __tablename__ = "families"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    # General rules that apply to every child in the family.
    policy_text: Mapped[str] = mapped_column(Text, default="")
    blocked_words: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    parents: Mapped[list["Parent"]] = relationship(back_populates="family")
    profiles: Mapped[list["Profile"]] = relationship(
        back_populates="family", cascade="all, delete-orphan"
    )


class Parent(Base):
    __tablename__ = "parents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    # Stored lower-cased and stripped; unique across the whole app.
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    family: Mapped[Family] = relationship(back_populates="parents")


class Profile(Base):
    """One child in a family."""

    __tablename__ = "profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    age: Mapped[int] = mapped_column(Integer)
    # The child's OWN policy text (age template or custom). The policy the
    # model actually sees is `effective_policy` below.
    policy_text: Mapped[str] = mapped_column(Text)
    policy_source: Mapped[str] = mapped_column(String(10), default="template")  # "template" | "custom"
    format_rules: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    family: Mapped[Family] = relationship(back_populates="profiles")
    classifications: Mapped[list["Classification"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )
    channel_rules: Mapped[list["ChannelRule"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )

    @property
    # Combines family-wide and child-specific rules for model evaluation.
    def effective_policy(self) -> str:
        # Family policy + this child's policy (child wins on conflict).
        family_policy = self.family.policy_text if self.family is not None else ""
        return combine_policies(family_policy, self.policy_text)

    @property
    # Computes the cache fingerprint from the current effective policy.
    def policy_hash(self) -> str:
        # Computed, not stored: derived from the *combined* text on demand, so
        # editing either the family policy or the child's policy changes it
        # immediately and no stored hash can go stale. Cache keys built from
        # it therefore never serve a result produced under an older policy.
        return compute_policy_hash(self.effective_policy)


class ChannelRule(Base):
    """A deterministic, always-allow or always-block rule for one YouTube
    channel on one profile.

    Checked BEFORE the classification cache and BEFORE any Nemotron call --
    a channel with a rule gets an instant, guaranteed decision with zero
    model cost for the DECISION itself. Only the explanation text is
    phrased by the model (see NebiusClassifier.explain_channel_rule), and
    even that has a plain-template fallback if the model call fails, so
    the decision never depends on the model being reachable.

    A channel can only have ONE rule per profile at a time (allow XOR
    block) -- the unique constraint enforces this, and the natural-language
    policy + model classification only ever runs for channels with no rule
    at all (the deterministic vs. non-deterministic split).
    """

    __tablename__ = "channel_rules"
    __table_args__ = (UniqueConstraint("profile_id", "channel_id", name="uq_profile_channel_rule"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"), index=True)
    channel_id: Mapped[str] = mapped_column(String(64), index=True)
    channel_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    action: Mapped[str] = mapped_column(String(10))  # "allow" | "block"
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    profile: Mapped[Profile] = relationship(back_populates="channel_rules")


class Classification(Base):
    __tablename__ = "classifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"), index=True)
    video_id: Mapped[str] = mapped_column(String(32), index=True)
    channel_id: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(20))
    explanation: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float)
    transcript_available: Mapped[bool] = mapped_column(default=False)
    # True when `status` came from a deterministic ChannelRule rather than
    # a Nemotron classification -- lets the UI show "Always allowed" /
    # "Always blocked" instead of a confidence-scored model result.
    decided_by_channel_rule: Mapped[bool] = mapped_column(default=False)
    decided_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Fingerprint of the effective policy that produced this row. Needed so a
    # cache hit under policy A does not reuse a newer DB row written under B
    # after an A → B → A edit cycle (the classification cache key already
    # includes the hash; this column keeps the DB row match equally strict).
    policy_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    profile: Mapped[Profile] = relationship(back_populates="classifications")
    feedback: Mapped[list["Feedback"]] = relationship(
        back_populates="classification", cascade="all, delete-orphan"
    )


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    classification_id: Mapped[int] = mapped_column(ForeignKey("classifications.id"), index=True)
    decision: Mapped[str] = mapped_column(String(10))
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    classification: Mapped[Classification] = relationship(back_populates="feedback")


class CacheEntry(Base):
    __tablename__ = "cache_entries"

    key: Mapped[str] = mapped_column(String(300), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
