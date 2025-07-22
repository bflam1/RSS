#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generate unified RSS (XML), Atom, and HTML feeds for all Mend release notes (Docs + GitHub Renovate).
Requires: requests, beautifulsoup4, feedgen, python-dateutil
"""

import html
import re
import requests
from bs4 import BeautifulSoup, Tag
from feedgen.feed import FeedGenerator
from datetime import datetime, timezone
from dateutil import parser as dateparser
from urllib.parse import urljoin

# ─── Configuration ────────────────────────────────────────────────────────────
release_pages = {
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
    "Mend for Bitbucket DC":      "https://docs.mend.io/integrations/latest/mend-for-bitbucket-data-center-release-notes"
}

# GitHub Renovate feed
github_feeds = {
    "mend Renovate": "https://github.com/renovatebot/renovate/releases.atom"
}

# ─── Utility: Normalize and fix quotes ─────────────────────────────────────────
def normalize_quotes(text: str) -> str:
    text = html.unescape(text)
    try:
        text = text.encode('latin-1', errors='ignore').decode('utf-8', errors='ignore')
    except:
        pass
    for ch in ['\u201c', '\u201d', '“', '”', '\u201e', '\u201f', '„', '‟']:
        text = text.replace(ch, '"')
    for ch in ['\u2018', '\u2019', '‘', '’']:
        text = text.replace(ch, "'")
    return text

# ─── Helper: extract latest release block with HTML ────────────────────────────
def fetch_latest_release_html(url: str):
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.content, 'html.parser')
    header = next((tag for tag in soup.find_all(['h2','h3','h4']) if tag.get_text(strip=True).lower().startswith('version')), None)
    if not header:
        header = soup.find(['h2','h3','h4'])
    version_text = header.get_text(strip=True) if header else 'Release'

    fragment = BeautifulSoup('', 'html.parser')
    for sib in header.next_siblings:
        if isinstance(sib, Tag) and sib.name in ['h2','h3','h4']:
            break
        fragment.append(sib)

    # Convert relative links to absolute
    for a in fragment.find_all('a', href=True):
        href = a['href']
        if not href.startswith(('http://','https://','#','mailto:')):
            a['href'] = urljoin(url, href)

    return version_text, normalize_quotes(str(fragment))

# ─── Helper: parse date from version header ────────────────────────────────────
def parse_version_date(version_text: str) -> datetime:
    m = re.search(r'\(([^)]+)\)', version_text)
    if m:
        try:
            dt = dateparser.parse(m.group(1))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except:
            pass
    return datetime.now(timezone.utc)

# ─── Build feed entries ────────────────────────────────────────────────────────
entries = []
for name, url in release_pages.items():
    version_line, details_html = fetch_latest_release_html(url)
    entries.append({
        'title': f"{name}: {version_line}",
        'link': url,
        'description': details_html,
        'pubDate': parse_version_date(version_line)
    })

for name, feed_url in github_feeds.items():
    resp = requests.get(feed_url, timeout=10)
    soup = BeautifulSoup(resp.content, 'xml')
    entry_xml = soup.find('entry')
    if entry_xml:
        updated = entry_xml.updated.text if entry_xml.updated else ''
        try:
            timestamp = datetime.fromisoformat(updated.replace('Z', '+00:00')) if updated else datetime.now(timezone.utc)
        except:
            timestamp = datetime.now(timezone.utc)
        summary_tag = entry_xml.find('summary')
        summary_text = summary_tag.text if summary_tag and summary_tag.text else ''
        summary = normalize_quotes(summary_text)
        entries.append({
            'title': f"{name}: {entry_xml.title.text}",
            'link': entry_xml.link['href'] if entry_xml.link and entry_xml.link.has_attr('href') else feed_url,
            'description': f"<p>{summary}</p>",
            'pubDate': timestamp
        })

# ─── Generate RSS & Atom Feeds ─────────────────────────────────────────────────
fg = FeedGenerator()
# Atom compliance: feed ID & self link
fg.id("https://bflam1.github.io/RSS/atom.xml")
fg.link(href="https://bflam1.github.io/RSS/atom.xml", rel="self")
# Alternate link
fg.link(href="https://docs.mend.io/", rel="alternate")

# Feed metadata
fg.title("Mend.io Unified Release Notes")  # fixed typo: gfg -> fg
fg.author({'name': 'Mend Release Bot', 'email': 'noreply@bflam1.github.io'})
fg.description("Aggregated RSS, Atom, and HTML of all Mend release notes.")
# Use latest entry date for updated
if entries:
    latest = max(e['pubDate'] for e in entries)
    fg.updated(latest)
else:
    fg.updated(datetime.now(timezone.utc))

# Add entries
for e in entries:
    fe = fg.add_entry()
    fe.id(e['link'])
    fe.title(e['title'])
    fe.link(href=e['link'])
    fe.author({'name': 'Mend Release Bot'})
    fe.content(e['description'], type='html')
    fe.pubDate(e['pubDate'])

# Write RSS
rss_str = fg.rss_str(pretty=True)
if isinstance(rss_str, (bytes, bytearray)):
    rss_str = rss_str.decode('utf-8')
with open('mend_combined_release_feed.xml', 'w', encoding='utf-8') as f:
    f.write(rss_str)
print("✅ RSS feed generated: mend_combined_release_feed.xml")

# Write Atom
atom_str = fg.atom_str(pretty=True)
if isinstance(atom_str, (bytes, bytearray)):
    atom_str = atom_str.decode('utf-8')
with open('mend_combined_release_feed.atom', 'w', encoding='utf-8') as f:
    f.write(atom_str)
print("✅ Atom feed generated: mend_combined_release_feed.atom")

# ─── Generate HTML Output ─────────────────────────────────────────────────────
html_file = 'mend_combined_release_feed.html'
with open(html_file, 'w', encoding='utf-8') as f:
    f.write('<!DOCTYPE html>\n<html lang="en">\n<head>\n')
    f.write('  <meta charset="utf-8">\n')
    f.write('  <title>Mend.io Unified Release Notes</title>\n</head>\n<body>\n')
    f.write('  <h1>Mend.io Unified Release Notes</h1>\n')
    for e in entries:
        iso = e['pubDate'].isoformat()
        f.write(f'  <section>\n    <h2><a href="{e["link"]}">{e["title"]}</a></h2>\n')
        f.write(f'    <time datetime="{iso}">{iso}</time>\n')
        f.write(f'    {e["description"]}\n')
        f.write('  </section>\n  <hr/>\n')
    f.write('</body>\n</html>')
print(f"✅ HTML feed generated: {html_file}")
