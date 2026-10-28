"""Title and channel name for videos, resolved when needed instead of stored.

Ask Parent items and result cards need something a parent can recognise
("Minecraft survival, episode 12"), but classification rows only hold
video_id and channel_id. Rather than copy YouTube's text into our database,
this reads the metadata cache (already populated by every classification, and
expiring on its own TTL) and refetches on a miss. A video that has been
deleted or made private just comes back without a title.
"""

from typing import Any

from sqlalchemy.orm import Session

from app.cache import Cache
from app.youtube import YouTubeClient

# Protects latency and API quota when a queue is long and the cache is cold.
MAX_FETCHES_PER_CALL = 20

NO_CONTEXT: dict[str, str | None] = {"title": None, "channel_name": None}


async def get_video_context(
    db: Session,
    settings: Any,
    video_ids: list[str],
    youtube: Any | None = None,
) -> dict[str, dict[str, str | None]]:
    """Returns {video_id: {"title": ..., "channel_name": ...}} for each id.

    Never raises: any failure to look a video up yields empty context for
    that video, so a lookup problem can't break the queue or a check.
    """
    cache = Cache(db)
    client = youtube
    result: dict[str, dict[str, str | None]] = {}
    fetches = 0

    for video_id in dict.fromkeys(video_ids):
        metadata = cache.get(f"metadata:{video_id}")
        if metadata is None:
            if fetches >= MAX_FETCHES_PER_CALL:
                result[video_id] = dict(NO_CONTEXT)
                continue
            fetches += 1
            try:
                client = client or YouTubeClient(settings)
                metadata = await client.get_metadata(video_id)
            except Exception:  # deleted/private video, quota, network: show it without a title
                result[video_id] = dict(NO_CONTEXT)
                continue
            cache.set(f"metadata:{video_id}", metadata, settings.ttl_for("metadata"))
        result[video_id] = {
            "title": metadata.get("title") or None,
            "channel_name": metadata.get("channel") or None,
        }
    return result
