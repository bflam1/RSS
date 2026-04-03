#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generate unified RSS (XML), Atom, and HTML feeds for all Mend release notes (Docs + GitHub Renovate).
Requires: requests, beautifulsoup4, feedgen, python-dateutil, bleach
"""

# stdlib
import html
import logging
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

# third-party
import bleach
import requests
from bs4 import BeautifulSoup, Tag
from dateutil import parser as dateparser
from feedgen.feed import FeedGenerator

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# ─── URL Allowlist ─────────────────────────────────────────────────────────────
ALLOWED_DOMAINS = {'docs.mend.io', 'github.com'}

def validate_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != 'https':
        raise ValueError(f"URL must use HTTPS scheme: {url}")
    if parsed.netloc not in ALLOWED_DOMAINS:
        raise ValueError(f"URL domain not in allowlist: {parsed.netloc}")
    return url

# ─── HTML Sanitization Allowlist ───────────────────────────────────────────────
BLEACH_TAGS = [
    'p', 'br', 'strong', 'em', 'b', 'i', 'u', 'a', 'ul', 'ol', 'li',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'code', 'pre',
    'table', 'thead', 'tbody', 'tr', 'th', 'td', 'span', 'div', 'section',
    'hr', 'img',
]
BLEACH_ATTRS = {
    'a': ['href', 'title', 'target', 'rel'],
    'img': ['src', 'alt', 'title', 'width', 'height'],
    '*': ['class'],
}

# ─── Configuration ─────────────────────────────────────────────────────────────
release_pages: dict[str, str] = {
    "Mend AppSec Platform":       "https://docs.mend.io/platform/latest/mend-platform-release-notes",
    "Mend SCA":                   "https://docs.mend.io/platform/latest/mend-sca-release-notes",
    "Mend SAST":                  "https://docs.mend.io/platform/latest/mend-sast-release-notes",
    "Mend Container":             "https://docs.mend.io/platform/latest/mend-container-release-notes",
    "Mend AI":                    "https://docs.mend.io/platform/latest/mend-ai-release-notes",
    "Mend CLI":                   "https://docs.mend.io/platform/latest/mend-cli-release-notes",
    "Mend Unified Agent":         "https://docs.mend.io/legacy-sca/latest/mend-unified-agent-release-notes",
    "Mend Developer Platform":    "https://docs.mend.io/integrations/latest/mend-developer-platform-release-notes",
    "Mend for GitHub.com":        "https://docs.mend.io/integrations/latest/mend-for-github-com-release-notes",
    "Mend for GitHub Enterprise": "https://docs.mend.io/integrations/latest/mend-for-github-enterprise-release-notes",
    "Mend for GitLab":            "https://docs.mend.io/integrations/latest/mend-for-gitlab-release-notes",
    "Mend for Bitbucket DC":      "https://docs.mend.io/integrations/latest/mend-for-bitbucket-data-center-release-notes",
}

github_feeds: dict[str, str] = {
    "Mend Renovate CLI":   "https://github.com/renovatebot/renovate/releases.atom",
    "Mend Renovate CC-EE": "https://github.com/mend/renovate-ce-ee/releases.atom",
}

# ─── Utility: Normalize quotes ─────────────────────────────────────────────────
def normalize_quotes(text: str) -> str:
    text = html.unescape(text)
    for ch in ['\u201c', '\u201d', '\u201e', '\u201f']:
        text = text.replace(ch, '"')
    for ch in ['\u2018', '\u2019']:
        text = text.replace(ch, "'")
    return text

# ─── Helper: parse date from version header ────────────────────────────────────
def parse_version_date(version_text: str) -> datetime:
    m = re.search(r'\(([^)]+)\)', version_text)
    if m:
        try:
            dt = dateparser.parse(m.group(1))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (ValueError, OverflowError) as e:
            logger.warning("parse_version_date failed for %r: %s", version_text, e)
    return datetime.now(timezone.utc)

# ─── Helper: fetch a Mend docs release page ────────────────────────────────────
def fetch_latest_release_html(name: str, url: str) -> dict:
    validate_url(url)
    resp = requests.get(url, timeout=10, verify=True)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.content, 'html.parser')

    header = next(
        (tag for tag in soup.find_all(['h2', 'h3', 'h4'])
         if tag.get_text(strip=True).lower().startswith('version')),
        None,
    )
    if not header:
        header = soup.find(['h2', 'h3', 'h4'])
    if not header:
        logger.warning("No version header found for %s", name)
        return {'title': f"{name}: Release", 'link': url, 'description': '', 'pubDate': datetime.now(timezone.utc)}

    version_text = header.get_text(strip=True)

    fragment = BeautifulSoup('', 'html.parser')
    for sib in header.next_siblings:
        if isinstance(sib, Tag) and sib.name in ['h2', 'h3', 'h4']:
            break
        fragment.append(sib)

    for a in fragment.find_all('a', href=True):
        href = a['href']
        if not href.startswith(('http://', 'https://', '#', 'mailto:')):
            a['href'] = urljoin(url, href)

    return {
        'title': f"{name}: {version_text}",
        'link': url,
        'description': normalize_quotes(str(fragment)),
        'pubDate': parse_version_date(version_text),
    }

# ─── Helper: fetch a GitHub Atom feed entry ────────────────────────────────────
def fetch_github_feed(name: str, feed_url: str) -> dict | None:
    validate_url(feed_url)
    resp = requests.get(feed_url, timeout=10, verify=True)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.content, 'xml')
    entry_xml = soup.find('entry')
    if not entry_xml:
        logger.warning("No entries found in GitHub feed for %s", name)
        return None

    updated = entry_xml.updated.text if entry_xml.updated else ''
    try:
        timestamp = (
            datetime.fromisoformat(updated.replace('Z', '+00:00'))
            if updated
            else datetime.now(timezone.utc)
        )
    except (ValueError, AttributeError):
        logger.warning("Failed to parse timestamp %r for %s", updated, name)
        timestamp = datetime.now(timezone.utc)

    summary_tag = entry_xml.find('summary')
    summary_text = summary_tag.text if summary_tag and summary_tag.text else ''
    raw_link = entry_xml.link['href'] if entry_xml.link and entry_xml.link.has_attr('href') else feed_url
    try:
        link = validate_url(raw_link)
    except ValueError:
        logger.warning("Invalid link in GitHub feed for %s: %r — falling back to feed URL", name, raw_link)
        link = feed_url

    return {
        'title': f"{name}: {entry_xml.title.text}",
        'link': link,
        'description': f"<p>{normalize_quotes(summary_text)}</p>",
        'pubDate': timestamp,
    }

# ─── Build feed entries in parallel ────────────────────────────────────────────
def collect_entries() -> list[dict]:
    futures: dict = {}
    raw_results: dict[str, dict | None] = {}

    with ThreadPoolExecutor(max_workers=8) as executor:
        for name, url in release_pages.items():
            futures[executor.submit(fetch_latest_release_html, name, url)] = name
        for name, url in github_feeds.items():
            futures[executor.submit(fetch_github_feed, name, url)] = name

        for future in as_completed(futures):
            name = futures[future]
            try:
                raw_results[name] = future.result()
            except Exception as e:
                logger.error("Failed to fetch %s: %s", name, e)
                raw_results[name] = None

    # Rebuild in original insertion order; deduplicate by link
    entries: list[dict] = []
    seen_links: set[str] = set()
    for name in list(release_pages) + list(github_feeds):
        entry = raw_results.get(name)
        if entry and entry['link'] not in seen_links:
            seen_links.add(entry['link'])
            entries.append(entry)

    return entries

# ─── Validate output before writing ────────────────────────────────────────────
def validate_output(entries: list[dict]) -> bool:
    if not entries:
        logger.error("No entries generated — feed would be empty")
        return False
    invalid = [e.get('title', '?') for e in entries if not e.get('link') or not e.get('title')]
    if invalid:
        logger.error("Entries missing required fields: %s", invalid)
        return False
    logger.info("Validation passed: %d entries", len(entries))
    return True

# ─── Write all three feed files ────────────────────────────────────────────────
def generate_feeds(entries: list[dict]) -> None:
    fg = FeedGenerator()
    fg.id("https://bflam1.github.io/RSS/atom.xml")
    fg.link(href="https://bflam1.github.io/RSS/atom.xml", rel="self")
    fg.link(href="https://docs.mend.io/", rel="alternate")
    fg.title("Mend.io Unified Release Notes")
    fg.author({'name': 'Mend Release Bot', 'email': 'noreply@bflam1.github.io'})
    fg.description("Aggregated RSS, Atom, and HTML of all Mend release notes.")
    fg.updated(max(e['pubDate'] for e in entries) if entries else datetime.now(timezone.utc))

    for e in entries:
        fe = fg.add_entry()
        fe.id(e['link'])
        fe.title(e['title'])
        fe.link(href=e['link'])
        fe.author({'name': 'Mend Release Bot'})
        fe.content(e['description'], type='html')
        fe.pubDate(e['pubDate'])

    rss_str = fg.rss_str(pretty=True)
    if isinstance(rss_str, (bytes, bytearray)):
        rss_str = rss_str.decode('utf-8')
    with open('mend_combined_release_feed.xml', 'w', encoding='utf-8') as f:
        f.write(rss_str)
    logger.info("RSS feed written: mend_combined_release_feed.xml")

    atom_str = fg.atom_str(pretty=True)
    if isinstance(atom_str, (bytes, bytearray)):
        atom_str = atom_str.decode('utf-8')
    with open('mend_combined_release_feed.atom', 'w', encoding='utf-8') as f:
        f.write(atom_str)
    logger.info("Atom feed written: mend_combined_release_feed.atom")

    with open('mend_combined_release_feed.html', 'w', encoding='utf-8') as f:
        f.write('<!DOCTYPE html>\n<html lang="en">\n<head>\n')
        f.write('  <meta charset="utf-8">\n')
        f.write('  <title>Mend.io Unified Release Notes</title>\n</head>\n<body>\n')
        f.write('  <h1>Mend.io Unified Release Notes</h1>\n')
        for e in entries:
            iso = e['pubDate'].isoformat()
            safe_link = html.escape(e['link'], quote=True)
            safe_title = html.escape(e['title'])
            f.write(f'  <section>\n    <h2><a href="{safe_link}">{safe_title}</a></h2>\n')
            f.write(f'    <time datetime="{iso}">{iso}</time>\n')
            safe_desc = bleach.clean(e['description'], tags=BLEACH_TAGS, attributes=BLEACH_ATTRS, strip=True)
            f.write(f'    {safe_desc}\n')
            f.write('  </section>\n  <hr/>\n')
        f.write('</body>\n</html>')
    logger.info("HTML feed written: mend_combined_release_feed.html")

# ─── Entry point ───────────────────────────────────────────────────────────────
def main() -> int:
    logger.info("Starting Mend release feed generation...")
    entries = collect_entries()
    if not validate_output(entries):
        return 1
    generate_feeds(entries)
    logger.info("Done. %d feed entries written.", len(entries))
    return 0

if __name__ == '__main__':
    sys.exit(main())
