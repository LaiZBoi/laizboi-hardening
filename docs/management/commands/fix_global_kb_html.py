"""
Convert global KB articles from raw Markdown to Bootstrap-styled HTML.

Usage:
    python manage.py fix_global_kb_html
    python manage.py fix_global_kb_html --dry-run
"""

import re
import markdown
from django.core.management.base import BaseCommand
from docs.models import Document


def md_to_bootstrap_html(md_text):
    """Convert Markdown to Bootstrap 5-styled HTML (no inline styles — bleach strips them)."""

    # Convert markdown → raw HTML
    html = markdown.markdown(
        md_text,
        extensions=['extra', 'fenced_code', 'tables', 'toc', 'nl2br'],
    )

    # ── pre/code blocks — must be done BEFORE inline code ────────────────────
    # fenced_code produces: <pre><code class="language-bash">...</code></pre>
    # We want a dark, scrollable block with a language badge.
    def style_pre(m):
        inner = m.group(1)  # content between <pre>...</pre>
        lang_match = re.search(r'class="language-(\w+)"', inner)
        lang = lang_match.group(1).upper() if lang_match else 'CODE'
        # Strip class from inner <code> tag so bleach doesn't carry a stale class
        inner_clean = re.sub(r'\s*class="[^"]*"', '', inner)
        return (
            '<div class="position-relative mt-2 mb-3">'
            f'<span class="position-absolute top-0 end-0 badge bg-secondary small">{lang}</span>'
            f'<pre class="bg-dark text-light p-3 rounded overflow-auto">'
            f'{inner_clean}</pre>'
            '</div>'
        )

    html = re.sub(r'<pre>(.*?)</pre>', style_pre, html, flags=re.IGNORECASE | re.DOTALL)

    # ── inline code (single-line only — excludes newlines so pre content is safe) ──
    html = re.sub(
        r'<code>([^\n<]+)</code>',
        r'<code class="bg-secondary bg-opacity-25 px-1 rounded small">\1</code>',
        html
    )

    # ── headings ─────────────────────────────────────────────────────────────
    # h1 → suppress (page already shows the article title)
    html = re.sub(r'<h1[^>]*>.*?</h1>', '', html, flags=re.IGNORECASE | re.DOTALL)
    # h2 → section header with bottom border
    html = re.sub(
        r'<h2[^>]*>(.*?)</h2>',
        r'<h2 class="h4 mt-4 mb-2 pb-2 border-bottom">\1</h2>',
        html, flags=re.IGNORECASE | re.DOTALL
    )
    # h3 → subsection
    html = re.sub(
        r'<h3[^>]*>(.*?)</h3>',
        r'<h3 class="h5 mt-3 mb-2 fw-semibold">\1</h3>',
        html, flags=re.IGNORECASE | re.DOTALL
    )
    # h4 → minor header
    html = re.sub(
        r'<h4[^>]*>(.*?)</h4>',
        r'<h4 class="h6 mt-3 mb-1 text-muted fw-semibold text-uppercase small">\1</h4>',
        html, flags=re.IGNORECASE | re.DOTALL
    )

    # ── tables ────────────────────────────────────────────────────────────────
    html = html.replace(
        '<table>',
        '<div class="table-responsive my-3"><table class="table table-bordered table-sm table-hover">'
    )
    html = html.replace('</table>', '</table></div>')
    html = html.replace('<thead>', '<thead class="table-dark">')

    # ── blockquotes → info callouts ───────────────────────────────────────────
    html = re.sub(
        r'<blockquote>(.*?)</blockquote>',
        r'<div class="alert alert-info border-start border-4 border-info bg-opacity-10 rounded py-2 px-3 my-2">'
        r'<i class="fas fa-info-circle me-2"></i>\1</div>',
        html, flags=re.IGNORECASE | re.DOTALL
    )

    # ── lists & paragraphs ────────────────────────────────────────────────────
    html = html.replace('<ul>', '<ul class="mb-2">')
    html = html.replace('<ol>', '<ol class="mb-2">')
    html = html.replace('<p>', '<p class="mb-2">')
    html = html.replace('<hr>', '<hr class="my-4">')
    html = html.replace('<hr />', '<hr class="my-4">')

    return f'<div class="kb-article-body lh-base">\n{html}\n</div>'


class Command(BaseCommand):
    help = 'Convert global KB articles from raw Markdown to Bootstrap-styled HTML'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Show what would change without saving')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        articles = Document.objects.filter(is_global=True).order_by('title')
        count = 0

        for doc in articles:
            new_body = md_to_bootstrap_html(doc.body)
            if dry_run:
                self.stdout.write(f'  [DRY RUN] {doc.title} ({len(doc.body)} → {len(new_body)} chars)')
            else:
                doc.body = new_body
                doc.content_type = 'html'
                doc.save(update_fields=['body', 'content_type'])
                self.stdout.write(self.style.SUCCESS(f'  ✓ {doc.title}'))
            count += 1

        verb = 'Would update' if dry_run else 'Updated'
        self.stdout.write(self.style.SUCCESS(f'\nDone. {verb} {count} articles.'))
