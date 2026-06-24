#!/usr/bin/env python3
"""
RSS feed generator for Telegram channels.

Fetches the latest posts from one or more Telegram public channels via their
web preview (https://t.me/s/<channel>) and produces a valid RSS 2.0 feed
(Atom + media + content namespaces) committed to the repository as feed.xml.

Designed to run inside GitHub Actions on a schedule. No server required.
The feed is served directly from:
    https://raw.githubusercontent.com/<owner>/<repo>/main/feed.xml
"""

from __future__ import annotations

import html as html_module
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable
from xml.sax.saxutils import escape

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Channels to aggregate. Order does not matter — the final feed is sorted by
# publication date (newest first).
CHANNELS: list[str] = [
    "sochiautoparts",
    "bmw_mpower_club",
]

# How many recent posts to keep PER channel.
POSTS_PER_CHANNEL = 30

# Total cap for the combined feed (safety bound).
MAX_FEED_ITEMS = POSTS_PER_CHANNEL * len(CHANNELS)

# HTTP settings.
REQUEST_TIMEOUT = 20  # seconds
REQUEST_RETRIES = 3
RETRY_BACKOFF = 2  # seconds
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Pagination safety: never request more than this many pages per channel.
MAX_PAGES_PER_CHANNEL = 8

# Output path (relative to repo root when run in CI, or cwd locally).
OUTPUT_FILE = "feed.xml"

# Feed metadata.
FEED_TITLE = "Sochi Auto Parts + BMW M Power Club — Telegram"
FEED_DESCRIPTION = (
    "Последние посты из Telegram-каналов @sochiautoparts и @bmw_mpower_club."
)
FEED_LINK = "https://t.me/sochiautoparts"
FEED_LANGUAGE = "ru"
FEED_AUTHOR = "sochiautoparts"
FEED_GENERATOR = "sochiautoparts/rss (GitHub Actions)"

# Channel display names (resolved dynamically from the page, fallback here).
CHANNEL_FALLBACK_NAMES = {
    "sochiautoparts": "Сочи Автозапчасти | Sochi Auto Parts",
    "bmw_mpower_club": "BMW M Power Club",
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Post:
    channel: str
    post_id: str  # e.g. "sochiautoparts/93174"
    message_id: int  # numeric id, e.g. 93174
    url: str
    published: datetime
    text_html: str = ""  # inner HTML of the message text
    text_plain: str = ""  # plain text fallback
    media_urls: list[str] = field(default_factory=list)
    forwarded_from: str = ""

    @property
    def title(self) -> str:
        """Short single-line title derived from the post body."""
        base = self.text_plain.strip().splitlines()[0] if self.text_plain.strip() else ""
        if not base:
            if self.media_urls and not self.text_plain.strip():
                base = "📸 Фото/видео пост"
            else:
                base = "Пост без текста"
        # Trim to a readable length; ellipsis on multi-byte boundary.
        if len(base) > 120:
            base = base[:117].rstrip() + "…"
        prefix = f"[{self.channel}] "
        return prefix + base


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------


class Fetcher:
    """Thin HTTP wrapper with retries and a shared session."""

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "ru,en;q=0.8"})

    def get(self, url: str) -> str | None:
        last_err: Exception | None = None
        for attempt in range(1, REQUEST_RETRIES + 1):
            try:
                resp = self.session.get(url, timeout=REQUEST_TIMEOUT)
                if resp.status_code == 200 and resp.text:
                    return resp.text
                # 404 / empty -> no point retrying endlessly
                if resp.status_code in (404, 410):
                    return None
                last_err = RuntimeError(f"HTTP {resp.status_code} for {url}")
            except requests.RequestException as exc:  # network / DNS / timeout
                last_err = exc
            time.sleep(RETRY_BACKOFF * attempt)
        print(f"[warn] failed to fetch {url}: {last_err}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

_BG_URL_RE = re.compile(r"url\(['\"]?(.*?)['\"]?\)")


def _extract_text(message_el: BeautifulSoup) -> tuple[str, str]:
    """Return (html, plain) for the message text, de-duplicating nested copies."""
    text_el = message_el.select_one("div.tgme_widget_message_text.js-message_text")
    if not text_el:
        text_el = message_el.select_one("div.tgme_widget_message_text")
    if not text_el:
        return "", ""

    # Telegram sometimes nests an identical .tgme_widget_message_text inside
    # another. Collapse: if the outer's only meaningful child is an inner copy,
    # use the inner one to avoid duplicated content.
    inner = text_el.select_one("div.tgme_widget_message_text")
    if inner is not None and inner is not text_el:
        # If inner text equals outer minus wrapper, prefer inner.
        text_el = inner

    raw_html = text_el.decode_contents()
    plain = text_el.get_text(" ", strip=True)
    return raw_html, plain


def _extract_media(message_el: BeautifulSoup) -> list[str]:
    """Collect media URLs (full-size photo links, video poster fallbacks)."""
    urls: list[str] = []

    # Photos: <a class="tgme_widget_message_photo" href="FULL_URL">
    for a in message_el.select("a.tgme_widget_message_photo"):
        href = a.get("href")
        if href and href not in urls:
            urls.append(href)

    # Photo wraps carry a thumbnail as background-image — used as fallback.
    for wrap in message_el.select("a.tgme_widget_message_photo_wrap, i.tgme_widget_message_photo_wrap"):
        style = wrap.get("style", "")
        m = _BG_URL_RE.search(style)
        if m and m.group(1) not in urls:
            urls.append(m.group(1))

    # Video / round video posters.
    for v in message_el.select(
        "i.tgme_widget_message_video_thumb, i.tgme_widget_message_roundvideo_thumb"
    ):
        style = v.get("style", "")
        m = _BG_URL_RE.search(style)
        if m and m.group(1) not in urls:
            urls.append(m.group(1))

    return urls


def parse_channel_page(html: str, channel: str) -> tuple[list[Post], str | None]:
    """Parse one preview page. Returns (posts, next_before_id).

    next_before_id is the oldest post id on the page, used for backward
    pagination. None when no "more" link is present.
    """
    soup = BeautifulSoup(html, "lxml")
    posts: list[Post] = []

    for wrap in soup.select("div.tgme_widget_message_wrap"):
        msg = wrap.select_one("div.tgme_widget_message[data-post]")
        if not msg:
            continue
        post_id = msg.get("data-post", "")
        if "/" not in post_id:
            continue

        date_a = wrap.select_one("a.tgme_widget_message_date")
        time_el = wrap.select_one("time")
        dt_str = time_el.get("datetime") if time_el else None
        url = date_a.get("href") if date_a else None
        if not (dt_str and url):
            continue

        try:
            published = datetime.fromisoformat(dt_str)
        except ValueError:
            continue

        try:
            message_id = int(post_id.split("/", 1)[1])
        except (IndexError, ValueError):
            message_id = 0

        text_html, text_plain = _extract_text(msg)
        media = _extract_media(msg)

        fwd = wrap.select_one("div.tgme_widget_message_forwarded_from_name")
        forwarded_from = fwd.get_text(" ", strip=True) if fwd else ""

        posts.append(
            Post(
                channel=channel,
                post_id=post_id,
                message_id=message_id,
                url=url,
                published=published,
                text_html=text_html,
                text_plain=text_plain,
                media_urls=media,
                forwarded_from=forwarded_from,
            )
        )

    more = soup.select_one("a.tme_messages_more")
    next_before = more.get("data-before") if more else None
    return posts, next_before


def fetch_channel_posts(fetcher: Fetcher, channel: str, limit: int) -> list[Post]:
    """Fetch up to `limit` most-recent posts for a channel, paginating backward."""
    collected: dict[str, Post] = {}
    next_before: str | None = None

    for page in range(1, MAX_PAGES_PER_CHANNEL + 1):
        url = f"https://t.me/s/{channel}"
        if next_before:
            url = f"https://t.me/s/{channel}?before={next_before}"

        html = fetcher.get(url)
        if not html:
            break

        posts, next_before = parse_channel_page(html, channel)
        if not posts:
            break

        for p in posts:
            # Keep the most recent occurrence (dedupe by post id).
            if p.post_id not in collected:
                collected[p.post_id] = p

        print(f"[{channel}] page {page}: +{len(posts)} posts (total {len(collected)})")

        if len(collected) >= limit:
            break
        if not next_before:
            break

    # Sort newest first, take the requested limit.
    ordered = sorted(collected.values(), key=lambda p: (p.published, p.message_id), reverse=True)
    return ordered[:limit]


# ---------------------------------------------------------------------------
# RSS generation
# ---------------------------------------------------------------------------


def _rfc822(dt: datetime) -> str:
    """Format a datetime as RFC 822 (RSS 2.0 pubDate)."""
    return dt.astimezone(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")


def _build_item_html(post: Post) -> str:
    """Build the HTML content for an RSS item (used in content:encoded)."""
    parts: list[str] = []

    if post.forwarded_from:
        parts.append(
            f'<div style="color:#888;font-size:0.9em;margin-bottom:8px;">'
            f"↗ Переслано из <b>{escape(post.forwarded_from)}</b></div>"
        )

    # Media first for visual prominence.
    for m in post.media_urls:
        if m.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")) or "telesco.pe" in m:
            parts.append(f'<img src="{escape(m)}" style="max-width:100%;border-radius:8px;margin:4px 0;" />')
        else:
            parts.append(f'<a href="{escape(m)}">📎 Медиа</a><br/>')

    if post.text_html.strip():
        parts.append(f'<div class="tg-text">{post.text_html}</div>')
    elif not post.media_urls:
        parts.append("<i>(пост без текста)</i>")

    parts.append(
        f'<div style="margin-top:10px;font-size:0.85em;">'
        f'<a href="{escape(post.url)}">Открыть в Telegram →</a></div>'
    )
    return "\n".join(parts)


def generate_rss(posts: Iterable[Post], build_time: datetime) -> str:
    items_xml: list[str] = []
    for post in posts:
        content_html = _build_item_html(post)
        desc = post.text_plain if post.text_plain else ("Фото/видео пост" if post.media_urls else "Пост")
        if len(desc) > 300:
            desc = desc[:297].rstrip() + "…"

        media_tags = ""
        for m in post.media_urls:
            media_tags += (
                f'      <media:content url="{escape(m)}" medium="image" />\n'
            )

        items_xml.append(
            "    <item>\n"
            f"      <title>{escape(post.title)}</title>\n"
            f"      <link>{escape(post.url)}</link>\n"
            f"      <guid isPermaLink=\"true\">{escape(post.url)}</guid>\n"
            f"      <pubDate>{_rfc822(post.published)}</pubDate>\n"
            f"      <dc:creator>{escape('@' + post.channel)}</dc:creator>\n"
            f"      <category>{escape(post.channel)}</category>\n"
            f"      <description>{escape(desc)}</description>\n"
            f"      <content:encoded><![CDATA[{content_html}]]></content:encoded>\n"
            f"{media_tags}"
            "    </item>"
        )

    # Self link points to the raw feed on GitHub.
    self_url = "https://raw.githubusercontent.com/sochiautoparts/rss/main/feed.xml"

    rss = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" '
        'xmlns:atom="http://www.w3.org/2005/Atom" '
        'xmlns:content="http://purl.org/rss/1.0/modules/content/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:media="http://search.yahoo.com/mrss/">\n'
        "  <channel>\n"
        f"    <title>{escape(FEED_TITLE)}</title>\n"
        f"    <link>{escape(FEED_LINK)}</link>\n"
        f"    <description>{escape(FEED_DESCRIPTION)}</description>\n"
        f"    <language>{FEED_LANGUAGE}</language>\n"
        f"    <generator>{escape(FEED_GENERATOR)}</generator>\n"
        f"    <lastBuildDate>{_rfc822(build_time)}</lastBuildDate>\n"
        f"    <atom:link href=\"{escape(self_url)}\" rel=\"self\" type=\"application/rss+xml\" />\n"
        f"    <image>\n"
        f"      <url>https://t.me/i/userpic/320/sochiautoparts.jpg</url>\n"
        f"      <title>{escape(FEED_TITLE)}</title>\n"
        f"      <link>{escape(FEED_LINK)}</link>\n"
        f"    </image>\n"
        + "\n".join(items_xml)
        + "\n  </channel>\n</rss>\n"
    )
    return rss


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    fetcher = Fetcher()
    all_posts: list[Post] = []

    for channel in CHANNELS:
        try:
            posts = fetch_channel_posts(fetcher, channel, POSTS_PER_CHANNEL)
        except Exception as exc:  # never let one channel kill the whole feed
            print(f"[error] channel {channel} failed: {exc}", file=sys.stderr)
            posts = []
        print(f"[{channel}] collected {len(posts)} posts")
        all_posts.extend(posts)

    # Combine, dedupe (by post_id), sort newest first.
    seen: dict[str, Post] = {}
    for p in all_posts:
        seen.setdefault(p.post_id, p)
    combined = sorted(
        seen.values(),
        key=lambda p: (p.published, p.message_id),
        reverse=True,
    )[:MAX_FEED_ITEMS]

    build_time = datetime.now(timezone.utc)
    rss = generate_rss(combined, build_time)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as fh:
        fh.write(rss)

    print(f"\n✅ Wrote {OUTPUT_FILE} with {len(combined)} items ({len(rss)} bytes)")
    print(f"   Build time: {build_time.isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
