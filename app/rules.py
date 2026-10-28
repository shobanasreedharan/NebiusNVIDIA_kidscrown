"""Pure pre-model rules for deterministic video decisions."""

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any
import unicodedata

from app.policy_templates import template_for_age

TRUSTED_CHANNELS_PATH = Path(__file__).parent / "data" / "trusted_channels.json"


@dataclass(frozen=True)
class RuleDecision:
    status: str
    explanation: str
    decided_by: str


def rule_layer_enabled(settings: Any, name: str) -> bool:
    disabled = {
        item.strip()
        for item in str(getattr(settings, "disabled_rule_layers", "")).split(",")
        if item.strip()
    }
    return name not in disabled


def evaluate_pre_model_rules(profile, family, metadata, is_short, settings) -> RuleDecision | None:
    """Evaluate metadata-only rules in their required priority order."""
    if (
        getattr(settings, "block_age_restricted", True)
        and rule_layer_enabled(settings, "youtube_age_restricted")
        and metadata.get("age_restricted", "unknown") == "true"
    ):
        return RuleDecision(
            status="block",
            explanation="YouTube marks this video as age-restricted.",
            decided_by="youtube_age_restricted",
        )
    if rule_layer_enabled(settings, "word_rule"):
        words = _family_words(family)
        if getattr(settings, "default_never_words_enabled", True):
            words = list(BUILTIN_NEVER_WORDS) + words
        for field, label in (("title", "title"), ("channel", "channel name")):
            text_tokens = _tokens(metadata.get(field, ""))
            for word in words:
                term_tokens = _tokens(word)
                if term_tokens and _contains_sequence(text_tokens, term_tokens):
                    return RuleDecision(
                        status="block",
                        explanation=f"The {label} contains a word on your blocked-words list: {word}.",
                        decided_by="word_rule",
                    )
    if rule_layer_enabled(settings, "format_rule"):
        format_rules = _format_settings(profile)
        checks = []
        if is_short:
            checks.append(("shorts", "This is a Short", "Shorts"))
        if metadata.get("live", "unknown") in {"live", "upcoming"}:
            checks.append(("live", "This is a live video", "live videos"))
        try:
            duration = int(metadata.get("duration_seconds", ""))
        except (TypeError, ValueError):
            duration = None
        long_minutes = format_rules["long_video_minutes"]
        if duration is not None and duration > long_minutes * 60:
            checks.append(("long_video_action", "This is a long video", "long videos"))
        for key, label, category in checks:
            action = format_rules[key]
            if action == "model":
                continue
            if action == "ask_parent":
                explanation = f"{label}, and you asked to be asked about {category}."
            else:
                explanation = f"{label}, and your rule blocks {category}."
            if key == "long_video_action":
                explanation = (
                    f"This video is longer than your {long_minutes}-minute limit, "
                    + ("and you asked to review it." if action == "ask_parent" else "so your rule blocks it.")
                )
            return RuleDecision(
                status=action,
                explanation=explanation,
                decided_by="format_rule",
            )
    if (
        getattr(settings, "trusted_channels_enabled", False)
        and rule_layer_enabled(settings, "trusted_channel")
    ):
        try:
            band = template_for_age(profile.age).label
            channel_ids = TRUSTED_CHANNELS.get("bands", {}).get(band, [])
        except (AttributeError, TypeError, ValueError):
            channel_ids = []
        if metadata.get("channel_id", "") in channel_ids:
            return RuleDecision(
                status="allow",
                explanation="From a channel on KidsCrown's trusted children's list.",
                decided_by="trusted_channel",
            )
    return None


def _tokens(value: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", value or "").casefold()
    tokens: list[str] = []
    current = []
    for character in normalized:
        if character.isalnum():
            current.append(character)
        elif current:
            tokens.append("".join(current))
            current = []
    if current:
        tokens.append("".join(current))
    return tokens


def _contains_sequence(tokens: list[str], term: list[str]) -> bool:
    return any(tokens[index : index + len(term)] == term for index in range(len(tokens) - len(term) + 1))


def _family_words(family) -> list[str]:
    value = getattr(family, "blocked_words", None)
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    if not isinstance(value, list):
        return []
    return [unicodedata.normalize("NFKC", word).strip().casefold() for word in value if isinstance(word, str)]


def _builtin_words() -> list[str]:
    path = Path(__file__).parent / "data" / "never_words.txt"
    return [
        line.strip().casefold()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _load_trusted_channels() -> dict[str, Any]:
    try:
        data = json.loads(TRUSTED_CHANNELS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"bands": {}}
    return data if isinstance(data, dict) else {"bands": {}}


def _format_settings(profile) -> dict[str, Any]:
    defaults = {
        "shorts": "model",
        "live": "model",
        "long_video_action": "model",
        "long_video_minutes": 60,
    }
    value = getattr(profile, "format_rules", None)
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return defaults
    if isinstance(value, dict):
        defaults.update(value)
    return defaults


BUILTIN_NEVER_WORDS = tuple(_builtin_words())
TRUSTED_CHANNELS = _load_trusted_channels()
