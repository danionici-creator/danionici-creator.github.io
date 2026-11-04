"""Builds the deployable site into _site/: copies the static pages and adds a blog made of the Substack posts
listed in blog/posts.json that are already public.

Each post body is read from the publication's RSS feed, so a post appears here only once Substack has published it
(scheduled posts are not in the feed), and saved in blog/cache/. Substack refuses the feed to GitHub's servers, so
there the build uses the saved copies. Posts scheduled on Substack are kept encrypted in blog/sealed/ (see
seal_posts.py) and are opened only once their publication time has passed, with the key in the BLOG_KEY environment
variable, so they appear on schedule without anyone running anything. Run from the repository root:

    python tools/build_blog.py
"""
import html
import json
import os
import re
import shutil
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '_site'
BASE = 'https://danionici-creator.github.io'
SUBSTACK = 'https://ioandanielionici.substack.com'
ORCID = 'https://orcid.org/0009-0009-8183-3656'
SKIP = {'_site', 'tools', '.github', '.git', 'blog'}
SITE_NAME = 'Ioan Daniel Ionici'
GOATCOUNTER = 'untimpallor'  # account code at goatcounter.com; empty: no visit counting
NOTE = {'ro': 'Vizitele sunt numărate anonim, fără cookie-uri (GoatCounter).',
        'en': 'Visits are counted anonymously, without cookies (GoatCounter).'}
# outbound links are counted as events named after their address, so the dashboard shows which links readers follow
CLICKS = ('<script>document.addEventListener("click",function(e){var a=e.target.closest&&e.target.closest("a[href^=http]");'
          'if(a&&a.host!==location.host&&window.goatcounter&&goatcounter.count)'
          'goatcounter.count({path:a.href,title:a.textContent.trim().slice(0,80),event:true})})</script>')
MONTHS = ['ianuarie', 'februarie', 'martie', 'aprilie', 'mai', 'iunie', 'iulie', 'august', 'septembrie',
          'octombrie', 'noiembrie', 'decembrie']

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo('Europe/Bucharest')
except Exception:  # no time-zone database: Romania's winter offset is close enough for a date
    TZ = timezone(timedelta(hours=2))


class Clean(HTMLParser):
    """Keeps the text structure of a post and drops scripts, forms, buttons, icons and all attributes but a few."""
    DROP = {'script', 'style', 'form', 'button', 'svg', 'iframe', 'source', 'input', 'noscript'}
    KEEP = {'p', 'h2', 'h3', 'h4', 'ul', 'ol', 'li', 'a', 'em', 'strong', 'i', 'b', 'blockquote', 'hr', 'br',
            'figure', 'figcaption', 'img', 'sub', 'sup', 'code', 'pre', 'table', 'thead', 'tbody', 'tr', 'th', 'td'}
    VOID = {'hr', 'br', 'img'}
    ATTRS = {'a': {'href'}, 'img': {'src', 'alt'}}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.DROP:
            self.skip += tag not in self.VOID and tag != 'source' and tag != 'input'
            return
        if self.skip or tag not in self.KEEP:
            return
        keep = self.ATTRS.get(tag, set())
        a = ''.join(f' {k}="{html.escape(v or "", quote=True)}"' for k, v in attrs if k in keep)
        if tag == 'img':
            a += ' loading="lazy"'
        self.out.append(f'<{tag}{a}>')

    def handle_endtag(self, tag):
        if tag in self.DROP:
            self.skip = max(0, self.skip - (tag not in self.VOID and tag != 'source' and tag != 'input'))
            return
        if self.skip or tag not in self.KEEP or tag in self.VOID:
            return
        self.out.append(f'</{tag}>')

    def handle_data(self, data):
        if not self.skip:
            self.out.append(html.escape(data, quote=False))


def clean(body):
    c = Clean()
    c.feed(body)
    return re.sub(r'<p>\s*</p>', '', ''.join(c.out))


def feed():
    """Published posts by slug, from the RSS feed (title, subtitle, date and full body)."""
    req = urllib.request.Request(f'{SUBSTACK}/feed', headers={'User-Agent': 'Mozilla/5.0 (site-build; +' + BASE + ')'})
    with urllib.request.urlopen(req, timeout=30) as r:
        root = ET.fromstring(r.read())
    ns = {'content': 'http://purl.org/rss/1.0/modules/content/'}
    posts = {}
    for i in root.iter('item'):
        slug = i.findtext('link', '').rstrip('/').rsplit('/p/', 1)[-1]
        posts[slug] = {'title': html.unescape(i.findtext('title', '')),
                       'subtitle': html.unescape(i.findtext('description', '')),
                       'post_date': parsedate_to_datetime(i.findtext('pubDate')).isoformat(),
                       'body_html': i.findtext('content:encoded', '', ns)}
    return posts


def unseal(slug):
    """A scheduled post whose time has come, or None (not due yet, not sealed, or no key)."""
    sealed = ROOT / 'blog' / 'sealed' / f'{slug}.bin'
    key = os.environ.get('BLOG_KEY', '').strip()
    if not sealed.exists() or not key:
        return None
    from cryptography.fernet import Fernet
    d = json.loads(Fernet(key.encode()).decrypt(sealed.read_bytes()).decode('utf-8'))
    due = datetime.fromisoformat(d['post_date'].replace('Z', '+00:00'))
    return d if due <= datetime.now(timezone.utc) else None


def ro_date(iso):
    d = datetime.fromisoformat(iso.replace('Z', '+00:00')).astimezone(TZ)
    return f'{d.day} {MONTHS[d.month - 1]} {d.year}', d.date().isoformat()


def css():
    src = (ROOT / 'index.html').read_text(encoding='utf-8')
    return re.search(r'<style>(.*?)</style>', src, re.S).group(1)


def page(title, desc, canon, body, depth, extra_head=''):
    up = '../' * depth
    return f'''<!doctype html>
<html lang="ro">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(desc, quote=True)}">
<link rel="canonical" href="{canon}">
<meta property="og:title" content="{html.escape(title, quote=True)}"><meta property="og:description" content="{html.escape(desc, quote=True)}"><meta property="og:url" content="{canon}"><meta property="og:type" content="article">
<style>{CSS}
.post img{{max-width:100%;height:auto;border-radius:8px}}.post figure{{margin:20px 0}}.post blockquote{{border-left:3px solid var(--acc);margin:16px 0;padding:2px 16px;color:var(--mut)}}
.src{{font:15px 'Segoe UI',Arial,sans-serif;background:var(--soft);border-radius:10px;padding:10px 14px}}</style>
{extra_head}
</head>
<body>
<header class="site"><div class="bar"><nav aria-label="Principal"><a href="{up}ro/index.html">Acasă</a><a href="{up}blog/index.html">Blog</a><a href="{up}series.html">Seria LLM</a></nav><a class="lang" href="{up}index.html" lang="en">English</a></div></header>
<main>{body}</main>
<footer><p>Ioan Daniel Ionici · Cercetător independent, Oradea · <a href="{ORCID}">ORCID</a> · Texte: CC BY 4.0. Copie a blogului <a href="{SUBSTACK}">„Un timp al lor”</a>, unde articolele apar întâi și unde vă puteți abona.</p></footer>
</body></html>'''


def name_site():
    """Tells search engines the site's name (they otherwise show the host, "GitHub"): og:site_name on every page and
    a schema.org WebSite on the home pages."""
    ld = json.dumps({'@context': 'https://schema.org', '@type': 'WebSite', 'name': SITE_NAME,
                     'alternateName': ['danionici-creator.github.io'], 'url': f'{BASE}/'}, ensure_ascii=False)
    for f in OUT.rglob('*.html'):
        page = f.read_text(encoding='utf-8')
        if '</head>' not in page or 'og:site_name' in page:
            continue
        extra = f'<meta property="og:site_name" content="{SITE_NAME}">'
        if f in (OUT / 'index.html', OUT / 'ro' / 'index.html'):
            extra += f'<script type="application/ld+json">{ld}</script>'
        f.write_text(page.replace('</head>', extra + '</head>', 1), encoding='utf-8')


def count_visits():
    """Adds the GoatCounter script, outbound-click counting and a one-line notice to every page of _site."""
    tag = (f'<script data-goatcounter="https://{GOATCOUNTER}.goatcounter.com/count" async '
           f'src="https://gc.zgo.at/count.js"></script>' + CLICKS)
    for f in OUT.rglob('*.html'):
        page = f.read_text(encoding='utf-8')
        if '</body>' not in page or f.name.startswith('google'):
            continue
        lang = 'en' if '<html lang="en"' in page else 'ro'
        if '</footer>' in page:
            page = page.replace('</footer>', f'<p>{NOTE[lang]}</p></footer>', 1)
        f.write_text(page.replace('</body>', tag + '</body>', 1), encoding='utf-8')


def main():
    global CSS
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    for p in ROOT.iterdir():
        if p.name in SKIP:
            continue
        (shutil.copytree if p.is_dir() else shutil.copy2)(p, OUT / p.name)
    CSS = css()
    listing = json.loads((ROOT / 'blog' / 'posts.json').read_text(encoding='utf-8'))
    try:
        published = feed()
    except Exception as e:  # Substack blocks datacenter addresses: fall back to the saved copies
        print('feed unavailable, using blog/cache:', e)
        published = {}
    cache = ROOT / 'blog' / 'cache'
    cache.mkdir(exist_ok=True)
    posts = []
    for item in listing:
        saved = cache / f'{item["slug"]}.json'
        d = published.get(item['slug'])
        if d and d['body_html']:
            saved.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding='utf-8')
        elif saved.exists():
            d = json.loads(saved.read_text(encoding='utf-8'))
        else:
            d = unseal(item['slug'])
        if not d or not d['body_html']:
            print('not public yet:', item['slug'])
            continue
        human, iso = ro_date(d['post_date'])
        body = clean(d['body_html'])
        # a heading that repeats the title would show it twice under the page's own h1
        body = re.sub(r'<h[2-4]>\s*' + re.escape(html.escape(d['title'], quote=False)) + r'\s*</h[2-4]>', '', body, count=1)
        posts.append(dict(item, title=d['title'], subtitle=d['subtitle'], date=human, iso=iso, ts=d['post_date'], body=body))
    posts.sort(key=lambda p: datetime.fromisoformat(p['ts'].replace('Z', '+00:00')), reverse=True)
    for p in posts:
        url = f'{BASE}/blog/{p["slug"]}/'
        ld = json.dumps({'@context': 'https://schema.org', '@type': 'BlogPosting', 'headline': p['title'], 'datePublished': p['iso'],
                         'inLanguage': p.get('lang', 'ro'), 'url': url, 'author': {'@type': 'Person', 'name': 'Ioan Daniel Ionici', 'sameAs': [ORCID]}},
                        ensure_ascii=False)
        body = (f'<article class="post"><div class="hero"><p class="meta">{html.escape(p["series"])} · {p["date"]}</p>'
                f'<h1>{html.escape(p["title"])}</h1><p class="role">{html.escape(p["subtitle"])}</p></div>'
                + (f'<p class="src">Originally published on the blog “Un timp al lor”: <a href="{SUBSTACK}/p/{p["slug"]}">read on Substack or subscribe</a>.</p>'
                   if p.get('lang') == 'en' else
                   f'<p class="src">Publicat inițial pe blogul „Un timp al lor”: <a href="{SUBSTACK}/p/{p["slug"]}">citiți pe Substack sau abonați-vă</a>.</p>')
                + 
                f'{p["body"]}</article>')
        d = OUT / 'blog' / p['slug']
        d.mkdir(parents=True, exist_ok=True)
        (d / 'index.html').write_text(page(f'{p["title"]} · Un timp al lor', p['subtitle'] or p['title'], url, body, 2,
                                           f'<script type="application/ld+json">{ld}</script>'), encoding='utf-8')
    rows = ''.join(f'<article class="card"><p class="meta">{html.escape(p["series"])} · {p["date"]}</p>'
                   f'<h3><a href="{p["slug"]}/index.html">{html.escape(p["title"])}</a></h3><p>{html.escape(p["subtitle"])}</p></article>'
                   for p in posts)
    idx = (f'<div class="hero"><h1>Un timp al lor</h1><p class="role">Blog de popularizare științifică, în limba română</p></div>'
           f'<section><p>Articolele blogului „Un timp al lor”, copiate aici după ce apar pe <a href="{SUBSTACK}">Substack</a>, '
           f'unde vă puteți abona ca să le primiți prin email. Cele mai noi sunt primele.</p>{rows}</section>')
    (OUT / 'blog').mkdir(exist_ok=True)
    (OUT / 'blog' / 'index.html').write_text(page('Blog · Un timp al lor', 'Articolele blogului „Un timp al lor”: popularizare științifică despre inteligența artificială, timp și memorie.',
                                                  f'{BASE}/blog/', idx, 1), encoding='utf-8')
    urls = [f'{BASE}/', f'{BASE}/ro/', f'{BASE}/series.html', f'{BASE}/blog/'] + [f'{BASE}/blog/{p["slug"]}/' for p in posts]
    (OUT / 'sitemap.xml').write_text('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                                     + ''.join(f'  <url><loc>{u}</loc></url>\n' for u in urls) + '</urlset>\n', encoding='utf-8')
    name_site()
    if GOATCOUNTER:
        count_visits()
    print(f'built {len(posts)} posts:', ', '.join(p['slug'] for p in posts))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
