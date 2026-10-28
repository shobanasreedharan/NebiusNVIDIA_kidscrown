"""Static, generic age-band policy templates.

No model call: choosing an age just pre-fills the parent's policy box with one
of these. The parent can keep it as-is or rewrite it; `policy_source` records
which. They're deliberately plain language, in the same allow / ask-parent /
block shape the classifier is built around.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class AgeBand:
    label: str
    min_age: int
    max_age: int
    policy_text: str


MIN_AGE = 3
MAX_AGE = 17

AGE_BANDS: tuple[AgeBand, ...] = (
    AgeBand(
        label="3–5",
        min_age=3,
        max_age=5,
        policy_text=(
            "Allow: gentle educational content for young children — letters, numbers, colors, "
            "shapes, songs and nursery rhymes, simple crafts and drawing, farm and zoo animals, "
            "nature, and read-aloud stories.\n"
            "Ask parent: cartoons or shows I might not recognize, toy unboxing, very loud or "
            "fast-paced videos, and anything where it's unclear whether it suits a preschooler.\n"
            "Block: anything scary, violent, or frightening (including scary characters and jump "
            "scares), weapons, profanity or crude humor, adult themes, dangerous stunts or pranks, "
            "and videos that use familiar kid characters in inappropriate or disturbing situations."
        ),
    ),
    AgeBand(
        label="6–8",
        min_age=6,
        max_age=8,
        policy_text=(
            "Allow: science experiments for kids, animals and nature, dinosaurs, space, drawing "
            "and crafts, math and reading help, age-appropriate cartoons, music, and how-things-work "
            "explainers.\n"
            "Ask parent: gaming videos and let's-plays, unboxing and toy reviews, prank or challenge "
            "videos, mild cartoon fighting, mildly rude humor, and anything unfamiliar or unclear.\n"
            "Block: violence, weapons, scary or horror content, profanity, adult themes, dangerous "
            "stunts, and videos that use familiar kid characters in inappropriate or disturbing "
            "situations."
        ),
    ),
    AgeBand(
        label="9–12",
        min_age=9,
        max_age=12,
        policy_text=(
            "Allow: educational content in science, history, math, geography and coding, DIY and "
            "making, art, animals and nature, sports instruction, and music.\n"
            "Ask parent: gaming (especially games rated above their age), unboxing and product "
            "reviews, prank or challenge videos, influencer vlogs, fantasy action, news, and "
            "occasional mild language.\n"
            "Block: graphic violence or gore, horror, weapons how-tos, heavy profanity, sexual or "
            "suggestive content, dangerous challenges, promotion of drugs, vaping or alcohol, and "
            "content promoting self-harm or eating disorders."
        ),
    ),
    AgeBand(
        label="13–17",
        min_age=13,
        max_age=17,
        policy_text=(
            "Allow: tutorials and educational content, science, history, technology, creative work, "
            "music, sports, documentaries, and age-appropriate comedy.\n"
            "Ask parent: mature-rated gaming, news and political commentary, dating or relationship "
            "advice, mild language, fictional violence in film or TV clips, pranks, and influencer "
            "content I might want to review.\n"
            "Block: graphic violence or gore, sexual content, promotion of drugs, vaping or alcohol, "
            "content promoting self-harm or eating disorders, hate speech, dangerous challenges, and "
            "extreme profanity."
        ),
    ),
)


def template_for_age(age: int) -> AgeBand:
    # Selects the static policy template whose age range contains the requested age.
    for band in AGE_BANDS:
        if band.min_age <= age <= band.max_age:
            return band
    raise ValueError(f"No policy template for age {age}")


def policy_source_for(age: int, policy_text: str) -> str:
    # Labels a stored policy as the age template or as parent-customized text.
    """'template' when the text is exactly the age band's template, else 'custom'.

    Derived from the text rather than trusted from the client, so the label
    can't drift from what's actually stored (e.g. a parent who edits the
    template and then types it back is honestly back on 'template').
    """
    same = policy_text.strip() == template_for_age(age).policy_text.strip()
    return "template" if same else "custom"
