"""
Removes null-valued entries from a transcript cache JSON file, in place.

Why: TranscriptCache in transcript_checkpoint.py caches by video_id alone,
permanently, with no distinction between "confirmed no transcript" and
"Tavily's result was ambiguous" (see TavilyTranscriptProvider's docstring
in transcript.py). Entries written before ChainedTranscriptProvider
existed have that ambiguity baked in as a false None -- this script clears
just those, so a rerun of the checkpoint forces those specific videos
through the new Tavily -> youtube-transcript-api chain instead of
replaying a stale pre-chain result from cache. Entries with real
transcript text are left untouched (no need to re-spend credits on
videos that already succeeded).

Usage:
    python scripts/prune_null_transcript_cache.py scripts/sample_videos_transcript_cache.json
"""

import json
import sys
from pathlib import Path


def main(cache_path: Path) -> None:
    # Removes null transcript entries while preserving successfully fetched text.
    data = json.loads(cache_path.read_text(encoding="utf-8"))
    kept = {video_id: transcript for video_id, transcript in data.items() if transcript is not None}
    removed = len(data) - len(kept)
    cache_path.write_text(json.dumps(kept, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Removed {removed} null entries (will be re-fetched via the chain on next run).")
    print(f"Kept {len(kept)} entries with real transcript text (untouched).")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(f"Usage: python {sys.argv[0]} <transcript_cache.json>")
    main(Path(sys.argv[1]))
