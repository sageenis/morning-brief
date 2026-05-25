#!/usr/bin/env python3
"""
Build a podcast episode from the newest brief JSON file.

Reads:  briefs/YYYY-MM-DD.json  (the newest one)
Writes: episodes/YYYY-MM-DD.mp3
Updates: feed.xml  (prepends new episode item)

Requires env: OPENAI_API_KEY
"""

import os
import sys
import json
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from html import escape

REPO_ROOT = Path(__file__).resolve().parent.parent
BRIEFS_DIR = REPO_ROOT / "briefs"
EPISODES_DIR = REPO_ROOT / "episodes"
FEED_PATH = REPO_ROOT / "feed.xml"

PODCAST_TITLE = "Sagee's Morning Brief"
PODCAST_DESC = "A personalized daily brief covering weather, markets, and the day ahead. Auto-generated every morning."
PODCAST_AUTHOR = "Sagee"
PODCAST_EMAIL = "sageenis@gmail.com"
PODCAST_LINK = "https://sageenis.github.io/morning-brief/"
PODCAST_IMAGE = "https://sageenis.github.io/morning-brief/cover.png"
PODCAST_CATEGORY = "News"
PAGES_BASE = "https://sageenis.github.io/morning-brief"

OPENAI_VOICE = "nova"  # nova, alloy, echo, fable, onyx, shimmer
OPENAI_MODEL = "tts-1"  # tts-1 (faster, cheaper) or tts-1-hd (higher quality)
OPENAI_FORMAT = "mp3"


def find_newest_brief() -> Path:
    briefs = sorted(BRIEFS_DIR.glob("*.json"))
    if not briefs:
        sys.exit("No brief JSON files found in briefs/")
    return briefs[-1]


def generate_mp3(text: str, out_path: Path) -> int:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        sys.exit("OPENAI_API_KEY env var not set")

    body = json.dumps({
        "model": OPENAI_MODEL,
        "voice": OPENAI_VOICE,
        "input": text,
        "response_format": OPENAI_FORMAT,
    }).encode()

    req = urllib.request.Request(
        "https://api.openai.com/v1/audio/speech",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            audio = resp.read()
    except urllib.error.HTTPError as e:
        sys.exit(f"OpenAI TTS failed: {e.code} {e.read().decode()[:500]}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(audio)
    return len(audio)


def estimate_duration(byte_size: int) -> str:
    # OpenAI TTS MP3 is ~32 kbps = ~4 KB/s
    seconds = max(1, int(byte_size / 4000))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def build_feed(items: list) -> str:
    now_rfc = format_datetime(datetime.now(timezone.utc))

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" '
        'xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" '
        'xmlns:content="http://purl.org/rss/1.0/modules/content/" '
        'xmlns:atom="http://www.w3.org/2005/Atom">',
        '  <channel>',
        f'    <title>{escape(PODCAST_TITLE)}</title>',
        f'    <link>{escape(PODCAST_LINK)}</link>',
        f'    <atom:link href="{escape(PAGES_BASE)}/feed.xml" rel="self" type="application/rss+xml"/>',
        '    <language>en-us</language>',
        f'    <description>{escape(PODCAST_DESC)}</description>',
        f'    <itunes:author>{escape(PODCAST_AUTHOR)}</itunes:author>',
        f'    <itunes:summary>{escape(PODCAST_DESC)}</itunes:summary>',
        f'    <itunes:image href="{escape(PODCAST_IMAGE)}"/>',
        f'    <itunes:category text="{escape(PODCAST_CATEGORY)}"/>',
        '    <itunes:explicit>false</itunes:explicit>',
        '    <itunes:owner>',
        f'      <itunes:name>{escape(PODCAST_AUTHOR)}</itunes:name>',
        f'      <itunes:email>{escape(PODCAST_EMAIL)}</itunes:email>',
        '    </itunes:owner>',
        f'    <lastBuildDate>{now_rfc}</lastBuildDate>',
    ]

    for item in items:
        parts += [
            '    <item>',
            f'      <title>{escape(item["title"])}</title>',
            f'      <description>{escape(item["description"])}</description>',
            f'      <itunes:summary>{escape(item["description"])}</itunes:summary>',
            f'      <pubDate>{item["pub_date"]}</pubDate>',
            f'      <enclosure url="{escape(item["mp3_url"])}" length="{item["bytes"]}" type="audio/mpeg"/>',
            f'      <guid isPermaLink="false">{escape(item["guid"])}</guid>',
            f'      <itunes:duration>{item["duration"]}</itunes:duration>',
            f'      <itunes:explicit>false</itunes:explicit>',
            '    </item>',
        ]

    parts += ['  </channel>', '</rss>', '']
    return '\n'.join(parts)


def parse_existing_items() -> list:
    if not FEED_PATH.exists():
        return []
    try:
        tree = ET.parse(FEED_PATH)
    except ET.ParseError:
        print("Warning: existing feed.xml unparseable; starting fresh.")
        return []

    ns = {'itunes': 'http://www.itunes.com/dtds/podcast-1.0.dtd'}
    items = []
    channel = tree.getroot().find('channel')
    if channel is None:
        return []
    for it in channel.findall('item'):
        guid_el = it.find('guid')
        enc_el = it.find('enclosure')
        if guid_el is None or enc_el is None:
            continue
        items.append({
            "title": (it.findtext('title') or '').strip(),
            "description": (it.findtext('description') or '').strip(),
            "pub_date": (it.findtext('pubDate') or '').strip(),
            "mp3_url": enc_el.get('url', ''),
            "bytes": int(enc_el.get('length', '0') or 0),
            "guid": guid_el.text or '',
            "duration": (it.findtext('itunes:duration', namespaces=ns) or '00:00:00').strip(),
        })
    return items


def main():
    brief_path = find_newest_brief()
    print(f"Building episode from: {brief_path.name}")
    brief = json.loads(brief_path.read_text(encoding='utf-8'))

    date_str = brief["date"]
    title = brief["title"]
    description = brief["description"]
    spoken_text = brief["spoken_text"]
    pub_date = brief["pub_date"]

    mp3_path = EPISODES_DIR / f"{date_str}.mp3"
    print(f"Generating MP3 ({len(spoken_text)} chars) via OpenAI {OPENAI_MODEL}/{OPENAI_VOICE}...")
    byte_size = generate_mp3(spoken_text, mp3_path)
    duration = estimate_duration(byte_size)
    print(f"  -> {mp3_path.relative_to(REPO_ROOT)} ({byte_size:,} bytes, ~{duration})")

    new_item = {
        "title": title,
        "description": description,
        "pub_date": pub_date,
        "mp3_url": f"{PAGES_BASE}/episodes/{date_str}.mp3",
        "bytes": byte_size,
        "guid": f"brief-{date_str}",
        "duration": duration,
    }

    existing = parse_existing_items()
    existing = [it for it in existing if it["guid"] != new_item["guid"]]
    items = [new_item] + existing

    FEED_PATH.write_text(build_feed(items), encoding='utf-8')
    print(f"Updated feed.xml with {len(items)} episode(s).")


if __name__ == "__main__":
    main()
