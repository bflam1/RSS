#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generate unified RSS (XML) and HTML feeds for all Mend release notes (Docs + GitHub Renovate).
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
    "Mend for GitHub.com":       "https://docs.mend.io/integrations/latest/mend-for-github-com-release-notes",
    "Mend for GitHub Enterprise": "https://docs.mend.io/integrations/latest/mend-for-github-enterprise-release-notes",
    "Mend for GitLab":            "https://docs.mend.io/integrations/latest/mend-for-gitlab-release-notes",
    "Mend for Bitbucket DC":      "https://docs.mend.io/integrations/latest/mend-for-bitbucket-data-center-release-notes"
}

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

# ─── Helper: extract latest version block ─────────────────────────────────────
def fetch_latest_release_html(url):
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, 'html.parser')
        header = None
        version_text = ''
        for tag in soup.find_all(['h2', 'h3', 'h4']):
            text = tag.get_text(strip=True)
            if text.lower().startswith('version'):
                header = tag
                version_text = text
                break
        if not header:
            header = soup.find(['h2', 'h3', 'h4'])
            version_text = header.get_text(strip=True) if header else 'Release'

        # collect following content until next version header
        fragment = BeautifulSoup('', 'html.parser')
        for sib in header.next_siblings:
            if isinstance(sib, Tag) and sib.name in ['h2', 'h3', 'h4']:
                break
            fragment.append(sib)

        # convert relative links to absolute docs.mend.io URLs
        for a in fragment.find_all('a', href=True):
            href = a['href']
            if not href.startswith(('http://', 'https://', '#', 'mailto:')):
                a['href'] = urljoin(url, href)

        details_html = str(fragment)
        return version_text, normalize_quotes(details_html)
    except Exception as e:
        return 'Error fetching content', normalize_quotes(f'<p>{e}</p>')

# ─── Helper: parse date from version text ──────────────────────────────────────
def parse_version_date(version_text: str) -> datetime:
    m = re.search(r'\(([^)]+)\)', version_text)
    if m:
        date_str = m.group(1)
        try:
            dt = dateparser.parse(date_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            pass
    return datetime.now(timezone.utc)

# ─── Build Entries ────────────────────────────────────────────────────────────
entries = []
for name, url in release_pages.items():
    version_line, details_html = fetch_latest_release_html(url)
    pub_date = parse_version_date(version_line)
    entries.append({
        'title': f"{name}: {version_line}",
        'link': url,
        'description': details_html,
        'pubDate': pub_date
    })

for name, feed_url in github_feeds.items():
    try:
        resp = requests.get(feed_url, timeout=10)
        soup = BeautifulSoup(resp.content, 'xml')
        entry_xml = soup.find('entry')
        if entry_xml:
            updated = entry_xml.updated.text
            try:
                timestamp = datetime.fromisoformat(updated.replace('Z', '+00:00'))
            except:
                timestamp = datetime.now(timezone.utc)
            summary = entry_xml.summary.text or 'See GitHub release for details.'
            desc_html = f"<p>{normalize_quotes(summary)}</p>"
            link_tag = entry_xml.find('link')
            href = link_tag['href'] if link_tag and link_tag.has_attr('href') else feed_url
            entries.append({
                'title': f"{name}: {entry_xml.title.text}",
                'link': href,
                'description': desc_html,
                'pubDate': timestamp
            })
    except Exception as e:
        print(f"Warning: failed to fetch GitHub feed {name}: {e}")

# ─── Generate RSS Feed ─────────────────────────────────────────────────────────
fg = FeedGenerator()
fg.title("Mend.io Unified Release Notes")
fg.link(href="https://docs.mend.io/", rel="self")
fg.description("Aggregated RSS and HTML of all Mend release notes.")
for e in entries:
    fe = fg.add_entry()
    fe.title(e['title'])
    fe.link(href=e['link'])
    fe.content(e['description'], type='CDATA')
    fe.pubDate(e['pubDate'])
rss_str = fg.rss_str(pretty=True).decode('utf-8')
with open('mend_combined_release_feed.xml', 'w', encoding='utf-8') as f:
    f.write(rss_str)
print("✅ RSS feed generated: mend_combined_release_feed.xml")

# ─── Generate HTML Output ─────────────────────────────────────────────────────
html_file = 'mend_combined_release_feed.html'
with open(html_file, 'w', encoding='utf-8') as f:
    f.write('<!DOCTYPE html>\n<html lang="en">\n<head>\n')
    f.write('  <meta charset="utf-8">\n')
    f.write('  <title>Mend.io Unified Release Notes</title>\n</head>\n<body>\n')
    f.write('  <h1>Mend.io Unified Release Notes</h1>\n')
    for e in entries:
        iso = e['pubDate'].isoformat()
        f.write(f'  <section>\n    <h2><a href="{e['link']}">{e['title']}</a></h2>\n')
        f.write(f'    <time datetime="{iso}">{iso}</time>\n')
        f.write(f'    {e['description']}\n')
        f.write('  </section>\n  <hr/>\n')
    f.write('</body>\n</html>')
print(f"✅ HTML feed generated: {html_file}")
