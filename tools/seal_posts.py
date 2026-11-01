"""Seals scheduled Substack posts for the blog: each post is encrypted into blog/sealed/<slug>.bin, so the public
repository does not show it before its date. build_blog.py opens a sealed post only once its publication time has
passed, with the key from the BLOG_KEY environment variable (a GitHub Actions secret).

Input: a JSON list exported from the Substack editor (/api/v1/drafts/<id>): slug, trigger_at, title, subtitle and
body (the editor's ProseMirror document). Run locally, from the repository root:

    python tools/seal_posts.py drafts.json path/to/key.txt

The key file is created if it does not exist; it must never be committed.
"""
import html
import json
import sys
from pathlib import Path

from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[1]
BLOCK = {'paragraph': 'p', 'bullet_list': 'ul', 'ordered_list': 'ol', 'list_item': 'li', 'blockquote': 'blockquote'}
MARK = {'em': 'em', 'strong': 'strong', 'subscript': 'sub', 'superscript': 'sup', 'code': 'code'}


def text(n):
    s = html.escape(n.get('text', ''), quote=False)
    for m in reversed(n.get('marks', [])):
        if m['type'] == 'link':
            s = f'<a href="{html.escape(m["attrs"]["href"], quote=True)}">{s}</a>'
        elif m['type'] in MARK:
            s = f'<{MARK[m["type"]]}>{s}</{MARK[m["type"]]}>'
    return s


def to_html(n, title=''):
    """Converts the editor's document to HTML; unknown nodes keep only their content."""
    t = n['type']
    inner = ''.join(to_html(c, title) for c in n.get('content', []))
    if t == 'text':
        return text(n)
    if t == 'hard_break':
        return '<br>'
    if t == 'horizontal_rule':
        return '<hr>'
    if t == 'heading':
        lv = min(max(n['attrs'].get('level', 2), 2), 4)
        return f'<h{lv}>{inner}</h{lv}>'
    if t == 'image2':
        a = n['attrs']
        alt = a.get('alt') or a.get('title') or f'Schema articolului „{title}”'
        return f'<img src="{html.escape(a["src"], quote=True)}" alt="{html.escape(alt, quote=True)}">'
    if t == 'captionedImage':
        return f'<figure>{inner}</figure>'
    if t == 'caption':
        return f'<figcaption>{inner}</figcaption>'
    if t in BLOCK:
        return f'<{BLOCK[t]}>{inner}</{BLOCK[t]}>'
    return inner


def main(drafts, keyfile):
    keyfile = Path(keyfile)
    if not keyfile.exists():
        keyfile.write_bytes(Fernet.generate_key())
        print('new key written to', keyfile)
    f = Fernet(keyfile.read_bytes().strip())
    out = ROOT / 'blog' / 'sealed'
    out.mkdir(exist_ok=True)
    for d in json.loads(Path(drafts).read_text(encoding='utf-8')):
        post = {'title': d['title'], 'subtitle': d['subtitle'], 'post_date': d['trigger_at'],
                'body_html': to_html(json.loads(d['body']), d['title'])}
        (out / f'{d["slug"]}.bin').write_bytes(f.encrypt(json.dumps(post, ensure_ascii=False).encode('utf-8')))
        print('sealed', d['slug'], d['trigger_at'])


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main(*sys.argv[1:3])
