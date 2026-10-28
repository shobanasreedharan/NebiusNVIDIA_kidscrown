"""Seed a demo family for local demos / hackathon walkthroughs.

Usage (from the repo root, with the same env as the app):

    .venv\\Scripts\\python scripts/seed_demo_family.py

Creates (or refreshes) demo@kidscrown.local / DemoPass123! with one child
and a sample Ask Parent item. Safe to re-run: it updates the existing demo
account instead of failing on a duplicate email.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select

from app.auth import hash_password
from app.db import SessionLocal, ensure_schema
from app.models import Classification, Family, Parent, Profile
from app.policy_templates import template_for_age

DEMO_EMAIL = "demo@kidscrown.local"
DEMO_PASSWORD = "DemoPass123!"
DEMO_PARENT = "Demo Parent"
DEMO_FAMILY = "Demo Family"
DEMO_POLICY = (
    "Allow: science, animals, dinosaurs, drawing, math, educational stories.\n"
    "Ask parent: gaming, unboxing, pranks.\n"
    "Block: violence, weapons, scary or horror content, profanity, adult themes."
)


def main() -> None:
    ensure_schema()
    with SessionLocal() as db:
        parent = db.scalar(select(Parent).where(Parent.email == DEMO_EMAIL))
        if parent is None:
            parent = Parent(
                name=DEMO_PARENT,
                email=DEMO_EMAIL,
                password_hash=hash_password(DEMO_PASSWORD),
                family=Family(name=DEMO_FAMILY, policy_text=DEMO_POLICY),
            )
            db.add(parent)
            db.flush()
            print(f"Created parent {DEMO_EMAIL}")
        else:
            parent.name = DEMO_PARENT
            parent.password_hash = hash_password(DEMO_PASSWORD)
            parent.family.name = DEMO_FAMILY
            parent.family.policy_text = DEMO_POLICY
            print(f"Updated existing parent {DEMO_EMAIL}")

        kids = list(parent.family.profiles)
        if not kids:
            kid = Profile(
                family_id=parent.family_id,
                name="Ava",
                age=7,
                policy_text=template_for_age(7).policy_text,
                policy_source="template",
            )
            db.add(kid)
            db.flush()
            print("Added child Ava (age 7, template policy)")
        else:
            kid = kids[0]
            print(f"Using existing child {kid.name} (id={kid.id})")

        existing = db.scalar(
            select(Classification).where(
                Classification.profile_id == kid.id,
                Classification.video_id == "Hxcs5g1AsZQ",
                Classification.status == "ask_parent",
            )
        )
        if existing is None:
            db.add(
                Classification(
                    profile_id=kid.id,
                    video_id="Hxcs5g1AsZQ",
                    channel_id="",
                    status="ask_parent",
                    explanation="Prank-style content — check with a parent first.",
                    confidence=0.64,
                    transcript_available=True,
                    policy_hash=kid.policy_hash,
                )
            )
            print("Seeded one Ask Parent queue item")
        else:
            print("Ask Parent sample already present")

        db.commit()

    print()
    print("Sign in with:")
    print(f"  email:    {DEMO_EMAIL}")
    print(f"  password: {DEMO_PASSWORD}")


if __name__ == "__main__":
    main()
