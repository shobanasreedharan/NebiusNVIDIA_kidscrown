"""Print YouTube channel candidates for a trusted-channel review list."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings


async def resolve(path: Path, api_key: str) -> None:
    terms = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    async with httpx.AsyncClient(timeout=15) as client:
        for term in terms:
            response = await client.get(
                "https://www.googleapis.com/youtube/v3/search",
                params={
                    "part": "snippet",
                    "type": "channel",
                    "q": term,
                    "maxResults": 5,
                    "key": api_key,
                },
            )
            response.raise_for_status()
            print(f"{term}:")
            items = response.json().get("items", [])
            if not items:
                print("  no candidates")
            for item in items:
                channel_id = item.get("id", {}).get("channelId")
                title = item.get("snippet", {}).get("title", "")
                if channel_id:
                    print(f"  {channel_id}\t{title}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_file", type=Path, help="UTF-8 text file with one @handle or channel name per line")
    args = parser.parse_args()
    api_key = get_settings().youtube_api_key
    if not api_key:
        parser.error("YOUTUBE_API_KEY is not configured")
    asyncio.run(resolve(args.input_file, api_key))


if __name__ == "__main__":
    main()
