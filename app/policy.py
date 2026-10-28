"""Policy text helpers shared by models, service, and routes.

Kept free of app imports so models.py can use it without a circular import.
"""

import hashlib

from app.config import MAX_EFFECTIVE_POLICY_CHARS

FAMILY_HEADER = "FAMILY POLICY (applies to every child in this family):"
CHILD_HEADER = (
    "CHILD POLICY (specific to this child; if it conflicts with the family "
    "policy, this child policy takes precedence):"
)


class EffectivePolicyTooLongError(ValueError):
    pass


def combine_policies(family_policy: str | None, child_policy: str | None) -> str:
    # Combines family-wide and child-specific policy text with child precedence.
    """Builds the single policy text the model evaluates a video against.

    Family rules apply to every kid; the child's own policy layers on top and
    wins on conflicts. With no family policy, the result is just the child's
    text -- so a family that hasn't written one hashes (and caches) exactly
    as a lone profile did before families existed.

    The child's NAME is deliberately not included: renaming a child must not
    invalidate cached classifications, and there's no reason to send it to
    the model.
    """
    family = (family_policy or "").strip()
    child = (child_policy or "").strip()
    if not family:
        combined = child
    elif not child:
        combined = family
    else:
        combined = f"{FAMILY_HEADER}\n{family}\n\n{CHILD_HEADER}\n{child}"
    if len(combined) > MAX_EFFECTIVE_POLICY_CHARS:
        raise EffectivePolicyTooLongError(
            f"The combined family and child policy must not exceed "
            f"{MAX_EFFECTIVE_POLICY_CHARS:,} characters."
        )
    return combined


def policy_hash(policy_text: str) -> str:
    # Creates a stable fingerprint for a policy text (child-only or combined).
    return hashlib.sha256(policy_text.strip().encode("utf-8")).hexdigest()
