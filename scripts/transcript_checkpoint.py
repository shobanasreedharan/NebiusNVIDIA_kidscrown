"""
Week 1 go/no-go checkpoint (see plan: "Known Risks #1" and Sprint Week 1).

Purpose: decide, BEFORE building the rest of the pipeline, whether the
unofficial transcript-fetch path is reliable enough to promise live,
arbitrary-video coverage in the demo -- or whether the demo needs to be
scoped down to a curated video set.

What it does, per video in the input CSV:
  1. Fetch metadata via YouTubeClient (official Data API).
  2. Fetch transcript via ChainedTranscriptProvider: Tavily first, falling
     back to YoutubeTranscriptApiProvider (unofficial) only when Tavily's
     result is ambiguous. See transcript.py for why Tavily's "no
     transcript" isn't a confirmed absence, and the IP-blocking/latency
     tradeoff the fallback reintroduces.
  3. Classify via NebiusClassifier against a sample policy.
  4. Compare the classifier's status to your hand-labeled ground truth.

Then reports, split by bucket:
  - Transcript fetch success rate (this IS the go/no-go signal -- the
    plan's target is >90% agreement on the CLEAN bucket, and this number
    tells you whether failures there are a transcript problem or a
    classification problem).
  - Classification agreement rate vs. ground truth.
  - For the adversarial bucket specifically: catch rate (did the
    classifier block/ask_parent on content it should not have allowed),
    reported but with NO pass/fail threshold -- the plan's stance is to
    measure and name this as a known limitation, not to gate on it.

Note on caching: this script keeps two persistent caches next to your CSV,
both to avoid re-spending limited hackathon credits on repeat runs:
  - <videos>_transcript_cache.json -- Tavily transcript fetches. Keyed by
    video_id only, since a video's transcript doesn't depend on policy.
  - <videos>_classification_cache.json -- Nebius classifications. Keyed by
    video_id + policy hash + transcript content, so changing SAMPLE_POLICY
    (or a transcript that previously failed now succeeding) correctly
    produces a fresh classification instead of silently reusing a stale
    one -- only genuinely-unchanged (video, policy, transcript) combos are
    served from cache.

Usage:
    python scripts/transcript_checkpoint.py videos.csv
    python scripts/transcript_checkpoint.py videos.csv 5   # 5s delay between videos

Input CSV columns (see sample_videos.csv for a template):
    video_id, bucket, ground_truth, notes

    bucket:       clean | gaming | prank | adversarial
    ground_truth: allow | ask_parent | block

Requires NEBIUS_API_KEY, YOUTUBE_API_KEY, and TAVILY_API_KEY set (via
.env, same as the main app). Also requires `pip install tavily-python`.
"""

import asyncio
import csv
import hashlib
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.nebius import ModelError, NebiusClassifier  # noqa: E402
from app.service import policy_hash  # noqa: E402
from app.transcript import (  # noqa: E402
    ChainedTranscriptProvider,
    TavilyTranscriptProvider,
    TranscriptProvider,
    YoutubeTranscriptApiProvider,
)
from app.youtube import YouTubeClient, YouTubeError, extract_video_id  # noqa: E402

# Same style of policy used in the plan's worked examples (nebius.py
# SYSTEM_PROMPT few-shots) -- adjust to match whatever sample policy you
# want to validate against.
SAMPLE_POLICY = """
Allow: science, animals, dinosaurs, drawing, math, other clearly educational content.
Ask me first: gaming, unboxing, and prank videos. These categories must be ask_parent by default; do not block them solely because they are gaming or pranks.
Block gaming or prank videos only when the available evidence also shows violence, weapons, scary or horror content, profanity, or adult themes.
Never allow: violence, weapons, scary or horror content, profanity, adult themes.
""".strip()

VALID_BUCKETS = {"clean", "gaming", "prank", "adversarial"}
VALID_STATUSES = {"allow", "ask_parent", "block"}


class TranscriptCache:
    """Persists successful transcript fetches to disk across runs.

    YouTube's IP-blocking of the unofficial fetch library can be
    intermittent rather than absolute -- some videos go through, most
    don't, and a retry later (different time, different network) may
    succeed where a prior attempt failed. This cache means every success
    is kept permanently instead of being re-fought for on every run, and
    it doubles as a starting point for a curated-fallback video set if
    live fetching never becomes reliable enough for the demo.

    Stored value per video_id is either a transcript string, or the
    literal null (JSON) for "confirmed no transcript exists" -- distinct
    from simply not being in the cache yet (never successfully checked).
    """

    def __init__(self, path: Path):
        # Loads the persistent transcript cache when the file already exists.
        self.path = path
        self.data: dict[str, str | None] = {}
        if path.exists():
            self.data = json.loads(path.read_text(encoding="utf-8"))

    def __contains__(self, video_id: str) -> bool:
        # Checks whether a video has a cached transcript result.
        return video_id in self.data

    def get(self, video_id: str) -> str | None:
        # Returns the cached transcript result for a video.
        return self.data.get(video_id)

    def set(self, video_id: str, transcript: str | None) -> None:
        # Persists a transcript result immediately for reuse on later runs.
        self.data[video_id] = transcript
        self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")


def _transcript_signature(transcript: str | None) -> str:
    # Builds a compact cache-key fingerprint for the transcript content.
    """Short fingerprint of the transcript actually used for a classification.

    Folded into the classification cache key below so that if a video's
    transcript fetch failed on an earlier run (forcing ask_parent via the
    safety net) and later succeeds on a rerun, the changed transcript
    naturally produces a different cache key -- the stale "no transcript"
    verdict can never be served back for a video that now has real content.
    """
    if transcript is None:
        return "no-transcript"
    return hashlib.sha256(transcript.encode("utf-8")).hexdigest()[:16]


class ClassificationCache:
    """Persists successful Nemotron classifications to disk across runs.

    Hackathon Nebius credits are limited, same concern as Tavily credits --
    without this, every rerun of the checkpoint (e.g. after tweaking
    SAMPLE_POLICY, or just re-running to confirm a flaky result) re-spends
    a Nebius call for every video, even when nothing about that video has
    changed since the last run.

    Keyed by video_id + policy_hash(policy) + transcript_signature(transcript)
    -- the same combination service.py's production classification cache
    uses (video_id + policy_hash), extended with the transcript signature
    since this script's transcript availability can change between runs in
    a way production's cached transcript usually doesn't. Changing the
    policy text OR getting a different transcript result both correctly
    invalidate old entries instead of silently reusing an outdated verdict.
    """

    def __init__(self, path: Path):
        # Loads the persistent classification cache when available.
        self.path = path
        self.data: dict[str, dict] = {}
        if path.exists():
            self.data = json.loads(path.read_text(encoding="utf-8"))

    def _key(self, video_id: str, policy: str, transcript: str | None) -> str:
        # Combines video, policy, and transcript identity into one cache key.
        return f"{video_id}:{policy_hash(policy)}:{_transcript_signature(transcript)}"

    def get(self, video_id: str, policy: str, transcript: str | None) -> dict | None:
        # Returns a cached classification for the exact input combination.
        return self.data.get(self._key(video_id, policy, transcript))

    def set(self, video_id: str, policy: str, transcript: str | None, result: dict) -> None:
        # Persists a classification result for future checkpoint runs.
        self.data[self._key(video_id, policy, transcript)] = result
        self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")


@dataclass
class VideoResult:
    video_id: str
    bucket: str
    ground_truth: str
    predicted: str | None = None
    predicted_from_cache: bool = False
    transcript_ok: bool = False
    transcript_error: str | None = None
    transcript_from_cache: bool = False
    metadata_error: str | None = None
    model_error: str | None = None
    notes: str = ""

    @property
    def agrees(self) -> bool:
        # Reports whether the raw prediction matches the labeled status.
        return self.predicted == self.ground_truth

    @property
    def ran_cleanly(self) -> bool:
        # Reports whether metadata and model calls completed without errors.
        return self.metadata_error is None and self.model_error is None

    @property
    def production_status(self) -> str | None:
        # Applies the production transcript safety rule to the raw prediction.
        """What a parent would actually see in the real app, not just the
        raw model output.

        Mirrors service.py's safety net exactly: `if transcript is None:
        force ask_parent`, regardless of whether that's a confirmed absence
        of captions or a live-fetch error (both look identical to the app --
        it can't tell the difference, only this checkpoint script can via
        transcript_error). The raw `predicted` field measures the model's
        judgment in isolation; this measures what the parent-facing product
        actually decides.
        """
        if not self.ran_cleanly:
            return None
        if not self.transcript_ok:
            return "ask_parent"
        return self.predicted

    @property
    def agrees_production(self) -> bool:
        # Reports whether the parent-facing status matches the labeled status.
        return self.production_status == self.ground_truth


def load_rows(csv_path: Path) -> list[VideoResult]:
    # Loads, validates, and normalizes checkpoint cases from the input CSV.
    rows: list[VideoResult] = []
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        required = {"video_id", "bucket", "ground_truth"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise SystemExit(f"CSV is missing required columns: {sorted(missing)}")
        for i, row in enumerate(reader, start=2):
            raw = row["video_id"].strip()
            bucket = row["bucket"].strip().lower()
            ground_truth = row["ground_truth"].strip().lower()
            if not raw:
                continue  # allow blank template rows
            try:
                # Accepts a bare 11-char ID OR a full youtube.com/youtu.be URL
                # -- paste whatever you copied from the address bar.
                video_id = extract_video_id(raw)
            except ValueError:
                raise SystemExit(f"Row {i}: couldn't find a video ID in {raw!r}")
            if bucket not in VALID_BUCKETS:
                raise SystemExit(f"Row {i}: bucket {bucket!r} not in {sorted(VALID_BUCKETS)}")
            if ground_truth not in VALID_STATUSES:
                raise SystemExit(f"Row {i}: ground_truth {ground_truth!r} not in {sorted(VALID_STATUSES)}")
            rows.append(
                VideoResult(
                    video_id=video_id,
                    bucket=bucket,
                    ground_truth=ground_truth,
                    notes=row.get("notes", ""),
                )
            )
    seen = defaultdict(list)
    for r in rows:
        seen[r.video_id].append(r.bucket)
    duplicates = {vid: buckets for vid, buckets in seen.items() if len(buckets) > 1}
    if duplicates:
        print("WARNING: duplicate video_id(s) in the CSV -- these inflate bucket counts without adding real coverage:")
        for vid, buckets in duplicates.items():
            print(f"  {vid} appears {len(buckets)}x (buckets: {', '.join(buckets)})")
    return rows


async def evaluate_one(
    result: VideoResult,
    youtube: YouTubeClient,
    transcript_provider: TranscriptProvider,
    model: NebiusClassifier,
    transcript_cache: TranscriptCache,
    classification_cache: ClassificationCache,
) -> None:
    # Evaluates one video through metadata, transcript, cache, and classification flows.
    try:
        metadata = await youtube.get_metadata(result.video_id)
    except YouTubeError as exc:
        result.metadata_error = str(exc)
        return

    if result.video_id in transcript_cache:
        transcript = transcript_cache.get(result.video_id)
        result.transcript_ok = transcript is not None
        result.transcript_from_cache = True
    else:
        try:
            transcript = await transcript_provider.get_transcript(result.video_id)
            result.transcript_ok = transcript is not None
            # Success (or confirmed absence) -- save immediately so this
            # video never needs a live fetch again, regardless of what
            # happens to the rest of this run.
            transcript_cache.set(result.video_id, transcript)
        except Exception as exc:  # noqa: BLE001 - deliberately broad for the checkpoint
            result.transcript_error = f"{type(exc).__name__}: {exc}"
            transcript = None
            # Deliberately NOT cached: a live-fetch error (network issue,
            # rate limit, etc.) is transient, not a confirmed absence --
            # caching it would permanently poison this video_id even after
            # the underlying issue clears.

    cached_classification = classification_cache.get(result.video_id, SAMPLE_POLICY, transcript)
    if cached_classification is not None:
        result.predicted = cached_classification["status"]
        result.predicted_from_cache = True
        return

    try:
        classification = await model.classify(SAMPLE_POLICY, metadata, transcript, feedback=[])
        result.predicted = classification["status"]
        classification_cache.set(result.video_id, SAMPLE_POLICY, transcript, classification)
    except ModelError as exc:
        result.model_error = str(exc)
        # Deliberately NOT cached, same reasoning as transcript errors above:
        # a model-call failure is transient (rate limit, timeout), not a
        # verdict worth remembering.


def print_report(results: list[VideoResult]) -> None:
    # Prints per-video diagnostics and aggregate bucket performance metrics.
    by_bucket: dict[str, list[VideoResult]] = defaultdict(list)
    for r in results:
        by_bucket[r.bucket].append(r)

    print("\n=== Per-video results ===")
    for r in results:
        flags = []
        if not r.transcript_ok:
            flags.append("no-transcript" if not r.transcript_error else "transcript-ERROR")
        elif r.transcript_from_cache:
            flags.append("transcript-cached")
        if r.predicted_from_cache:
            flags.append("classification-cached")
        if r.metadata_error:
            flags.append("metadata-ERROR")
        if r.model_error:
            flags.append("model-ERROR")
        if r.ran_cleanly:
            flags.append("AGREE" if r.agrees else "DISAGREE")
            if r.production_status != r.predicted:
                prod_flag = "prod-AGREE" if r.agrees_production else "prod-DISAGREE"
                flags.append(f"real app would show: {r.production_status} ({prod_flag})")
        flag_str = ", ".join(flags) if flags else ""
        print(
            f"  [{r.bucket:10s}] {r.video_id}  truth={r.ground_truth:11s} "
            f"predicted={str(r.predicted):11s} ({flag_str})"
        )
        if r.transcript_error:
            print(f"      transcript error: {r.transcript_error}")
        if r.metadata_error:
            print(f"      metadata error:   {r.metadata_error}")
        if r.model_error:
            print(f"      model error:      {r.model_error}")

    print("\n=== Bucket summary ===")
    print("  (raw = model's judgment in isolation. real-app = what a parent actually sees,")
    print("   after service.py's safety net forces ask_parent whenever transcript is unavailable)")
    for bucket in ("clean", "gaming", "prank", "adversarial"):
        rows = by_bucket.get(bucket, [])
        if not rows:
            continue
        n = len(rows)
        transcript_success = sum(1 for r in rows if r.transcript_ok) / n
        clean_runs = [r for r in rows if r.ran_cleanly]
        agreement = (sum(1 for r in clean_runs if r.agrees) / len(clean_runs)) if clean_runs else 0.0
        prod_agreement = (
            (sum(1 for r in clean_runs if r.agrees_production) / len(clean_runs)) if clean_runs else 0.0
        )
        print(
            f"  {bucket:12s} n={n:2d}  transcript_success={transcript_success:5.0%}  "
            f"raw_agreement={agreement:5.0%}  real_app_agreement={prod_agreement:5.0%}  "
            f"({len(clean_runs)}/{n} ran cleanly)"
        )

    print("\n=== Go/no-go signal (plan target: >90% agreement on 'clean') ===")
    clean_rows = by_bucket.get("clean", [])
    clean_clean_runs = [r for r in clean_rows if r.ran_cleanly]
    if clean_clean_runs:
        clean_agreement = sum(1 for r in clean_clean_runs if r.agrees) / len(clean_clean_runs)
        clean_prod_agreement = sum(1 for r in clean_clean_runs if r.agrees_production) / len(clean_clean_runs)
        verdict = "GO" if clean_agreement > 0.90 else "NO-GO (or scope demo to curated videos)"
        print(f"  Clean-bucket raw agreement:      {clean_agreement:.0%} -> {verdict}")
        print(f"  Clean-bucket real-app agreement: {clean_prod_agreement:.0%} (what a parent actually sees)")
    else:
        print("  No clean-bucket videos ran cleanly -- cannot assess. Check API keys / errors above.")

    adversarial_rows = by_bucket.get("adversarial", [])
    if adversarial_rows:
        clean_adv = [r for r in adversarial_rows if r.ran_cleanly]
        caught_raw = sum(1 for r in clean_adv if r.predicted != "allow")
        caught_prod = sum(1 for r in clean_adv if r.production_status != "allow")
        print(
            f"  Adversarial catch rate (raw model, predicted != allow):      {caught_raw}/{len(clean_adv)}"
        )
        print(
            f"  Adversarial catch rate (real app, incl. ask_parent fallback): {caught_prod}/{len(clean_adv)} "
            "-- measure only, no threshold. Name this limitation in the README regardless of the number."
        )

    cached_count = sum(1 for r in results if r.predicted_from_cache)
    live_calls = sum(1 for r in results if r.predicted is not None and not r.predicted_from_cache)
    print(
        f"\n  Nebius calls this run: {live_calls} live, {cached_count} served from "
        f"classification cache (out of {len(results)} total videos)."
    )

    transcript_fail_count = sum(1 for r in results if r.transcript_error)
    if transcript_fail_count:
        print(
            f"\n  NOTE: {transcript_fail_count}/{len(results)} videos hit a transcript ERROR "
            "(not just 'no captions'). Check TAVILY_API_KEY is set correctly and that your "
            "Tavily credit balance isn't exhausted -- see the error text per-video above for "
            "specifics. If errors persist across a rerun, fall back to a small pre-fetched "
            "curated set for the demo rather than promising live, arbitrary-video coverage."
        )


async def main(csv_path: Path, delay_seconds: float = 1.5, cache_path: Path | None = None) -> None:
    # Runs the checkpoint sequentially while reusing persistent caches.
    settings = get_settings()
    results = load_rows(csv_path)
    youtube = YouTubeClient(settings)
    transcript_providers: list[TranscriptProvider] = [YoutubeTranscriptApiProvider()]
    if settings.tavily_api_key:
        transcript_providers.insert(0, TavilyTranscriptProvider(settings.tavily_api_key))
    transcript_provider = ChainedTranscriptProvider(transcript_providers)
    model = NebiusClassifier(settings)
    cache_path = cache_path or csv_path.with_name(f"{csv_path.stem}_transcript_cache.json")
    transcript_cache = TranscriptCache(cache_path)
    print(f"Transcript cache:     {cache_path} ({len(transcript_cache.data)} videos already cached)")

    classification_cache_path = csv_path.with_name(f"{csv_path.stem}_classification_cache.json")
    classification_cache = ClassificationCache(classification_cache_path)
    print(
        f"Classification cache: {classification_cache_path} "
        f"({len(classification_cache.data)} entries already cached)"
    )

    # Sequential on purpose, with a pause between LIVE fetches only. Tavily
    # itself doesn't need this, but transcript_provider is now a chain that
    # falls back to YoutubeTranscriptApiProvider whenever Tavily is
    # ambiguous (see transcript.py) -- and that fallback DOES hit the same
    # IP-blocking risk that made us move off it as the sole provider.
    # Pacing every live fetch (not just fallback ones) is the simple,
    # slightly conservative choice here -- distinguishing "this video will
    # hit the fallback" in advance isn't possible without fetching it
    # first. Cached videos skip the delay since no live request is made.
    made_live_request = False
    for result in results:
        will_be_live = result.video_id not in transcript_cache
        if made_live_request and will_be_live and delay_seconds > 0:
            await asyncio.sleep(delay_seconds)
        made_live_request = will_be_live
        await evaluate_one(result, youtube, transcript_provider, model, transcript_cache, classification_cache)

    print_report(results)


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        raise SystemExit(
            f"Usage: python {sys.argv[0]} <videos.csv> [delay_seconds]\n"
            f"  delay_seconds defaults to 1.5 -- pause between LIVE transcript fetches "
            f"(cached videos skip the delay). Pass 0 to disable.\n"
            f"  A transcript cache is kept at <videos>_transcript_cache.json next to your "
            f"CSV, and persists across runs -- re-running the same command later will retry "
            f"only videos that failed last time, not ones that already succeeded (this also "
            f"avoids re-spending Tavily credits on unchanged videos)."
        )
    delay = float(sys.argv[2]) if len(sys.argv) == 3 else 1.5
    asyncio.run(main(Path(sys.argv[1]), delay_seconds=delay))
