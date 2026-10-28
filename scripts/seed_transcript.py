"""
Manually seed one video's transcript into the checkpoint's persistent
cache (scripts/<videos>_transcript_cache.json), for videos the live
unofficial fetch can't reach from this network.

Why this exists: after several runs of transcript_checkpoint.py at
different times, the IP block on youtube-transcript-api can turn out to
be persistent rather than intermittent -- retrying doesn't help. Rather
than waiting on it, grab the transcript by hand (YouTube shows captions
directly in the player -- click "..." under the video -> "Show
transcript") and feed it in here once. It's cached permanently after
that, exactly like a successful live fetch would be, and
transcript_checkpoint.py will pick it up automatically on the next run.

Usage:
    python scripts/seed_transcript.py <cache_file> <video_id_or_url> <transcript_text_file>
    python scripts/seed_transcript.py <cache_file> <video_id_or_url> --no-transcript

Examples:
    python scripts/seed_transcript.py scripts/sample_videos_transcript_cache.json ^
        https://www.youtube.com/watch?v=gTKS8SAwUzE transcript.txt

    python scripts/seed_transcript.py scripts/sample_videos_transcript_cache.json ^
        gTKS8SAwUzE --no-transcript
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.youtube import extract_video_id  # noqa: E402

# Reuse the same TranscriptCache class as the checkpoint script so the file
# format always stays identical between the two tools.
from transcript_checkpoint import TranscriptCache  # noqa: E402


def main() -> None:
    # Validates arguments and seeds one transcript cache entry from text or an absence marker.
    if len(sys.argv) != 4:
        raise SystemExit(
            f"Usage: python {sys.argv[0]} <cache_file> <video_id_or_url> <transcript_text_file>\n"
            f"       python {sys.argv[0]} <cache_file> <video_id_or_url> --no-transcript"
        )

    cache_file = Path(sys.argv[1])
    video_id = extract_video_id(sys.argv[2])
    text_arg = sys.argv[3]

    cache = TranscriptCache(cache_file)

    if text_arg == "--no-transcript":
        transcript = None
        print(f"Seeding {video_id} as 'confirmed no transcript' (not an error, a real absence).")
    else:
        text_path = Path(text_arg)
        if not text_path.exists():
            raise SystemExit(f"Transcript text file not found: {text_path}")
        transcript = text_path.read_text(encoding="utf-8").strip()
        if not transcript:
            raise SystemExit(f"{text_path} is empty -- nothing to seed. Use --no-transcript instead if intended.")
        word_count = len(transcript.split())
        print(f"Seeding {video_id} with a {word_count}-word transcript.")

    if video_id in cache:
        print(f"NOTE: {video_id} was already in the cache -- overwriting.")

    cache.set(video_id, transcript)
    print(f"Done. {cache_file} now has {len(cache.data)} video(s) cached.")


if __name__ == "__main__":
    main()
