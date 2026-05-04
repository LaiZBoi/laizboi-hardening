"""
Docs models - Knowledge base documents with versions
"""
from django.db import models
from django.contrib.auth.models import User
from core.models import Organization, Tag, BaseModel
from core.utils import OrganizationManager
import re
import markdown
import bleach
try:
    from bleach.css_sanitizer import CSSSanitizer
except Exception:  # bleach < 6 — older API; CSS just gets stripped
    CSSSanitizer = None


class DocumentCategory(BaseModel):
    """
    Categories for organizing documents in Knowledge Base.
    """
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='document_categories', null=True, blank=True, help_text='Organization for org-specific categories. NULL for global KB categories.')
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255)
    description = models.TextField(blank=True)
    parent = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='subcategories')
    icon = models.CharField(max_length=50, default='folder', help_text='Font Awesome icon name')
    order = models.IntegerField(default=0)

    objects = OrganizationManager()

    class Meta:
        db_table = 'document_categories'
        unique_together = [['organization', 'slug']]
        ordering = ['order', 'name']
        verbose_name_plural = 'Document categories'

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        # Auto-generate slug if not provided
        if not self.slug:
            from django.utils.text import slugify
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class Document(BaseModel):
    """
    Knowledge base document with HTML or Markdown body, or uploaded file.
    """
    CONTENT_TYPES = [
        ('html', 'HTML (WYSIWYG)'),
        ('markdown', 'Markdown'),
        ('file', 'Uploaded File'),
    ]

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='documents', null=True, blank=True, help_text='Organization for org-specific docs. NULL for global KB articles.')
    title = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255)
    body = models.TextField(blank=True)  # Now optional for file uploads
    content_type = models.CharField(max_length=20, choices=CONTENT_TYPES, default='html')
    is_published = models.BooleanField(default=True)
    is_template = models.BooleanField(default=False, help_text='Is this a reusable template?')
    is_archived = models.BooleanField(default=False)
    is_global = models.BooleanField(default=False, help_text='Global KB - visible to all organizations')
    is_client_visible = models.BooleanField(
        default=False,
        help_text='Visible to clients in the customer portal /portal/kb/ — '
                  'global articles surface to every portal user, '
                  'org-scoped articles only to that client.'
    )
    is_featured_in_portal = models.BooleanField(
        default=False,
        help_text='v3.17.234: pin this article to the top of the customer '
                  'portal KB list. Useful for "How to file a support ticket", '
                  'common FAQs, etc.',
    )
    portal_view_count = models.PositiveIntegerField(
        default=0,
        help_text='v3.17.234: incremented every time a portal user opens this '
                  'article. Used to surface popular content + track engagement.',
    )

    # File upload fields
    file = models.FileField(
        upload_to='documents/files/%Y/%m/',
        null=True,
        blank=True,
        help_text='Uploaded file (PDF, DOCX, images, etc.)'
    )
    file_size = models.BigIntegerField(null=True, blank=True, help_text='File size in bytes')
    file_type = models.CharField(max_length=100, blank=True, help_text='MIME type of uploaded file')

    # Relations
    category = models.ForeignKey(DocumentCategory, on_delete=models.SET_NULL, null=True, blank=True, related_name='documents')
    tags = models.ManyToManyField(Tag, blank=True, related_name='documents')

    # Preview image for templates
    preview_image = models.ImageField(
        upload_to='documents/previews/',
        null=True,
        blank=True,
        help_text='Preview image for template (300x200)'
    )

    # Phase 22 v1 (v3.17.245) — Knowledge Base & SOP Management.
    # Owner: who is responsible for keeping this article current.
    # Defaults to created_by on first save (see save() override).
    owner = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='documents_owned',
        help_text='Person responsible for keeping this article current. '
                  'Receives review reminders.',
    )
    last_reviewed_at = models.DateTimeField(
        null=True, blank=True,
        help_text='When the owner last marked this article reviewed. '
                  'Drives the review reminder cron.',
    )
    review_interval_days = models.PositiveIntegerField(
        default=90,
        help_text='Send a review reminder this many days after the last '
                  'review. 0 = never remind.',
    )
    requires_approval = models.BooleanField(
        default=False,
        help_text='When true, edits move the article into a draft state '
                  'until an approver publishes it.',
    )
    is_draft = models.BooleanField(
        default=False,
        help_text='Pending approval — hidden from the regular KB list and '
                  'the customer portal until an approver publishes.',
    )
    published_at = models.DateTimeField(
        null=True, blank=True,
        help_text='When the article was first published.',
    )

    # Metadata
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='documents_created')
    last_modified_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='documents_modified')

    objects = OrganizationManager()

    class Meta:
        db_table = 'documents'
        unique_together = [['organization', 'slug']]
        ordering = ['-updated_at']
        indexes = [
            models.Index(fields=['organization', 'slug']),
            models.Index(fields=['is_published']),
        ]

    def __str__(self):
        return self.title

    def render_content(self):
        """
        Render content based on content_type.
        """
        if self.content_type == 'markdown':
            # Render markdown to HTML
            html = markdown.markdown(
                self.body,
                extensions=['extra', 'codehilite', 'toc']
            )
        else:
            # Already HTML from WYSIWYG editor or AI-generated
            html = self.body

        # Sanitize HTML for security
        # Allow Bootstrap 5 styling components and Font Awesome icons
        # Removed interactive/form elements: button, form, input, select, textarea
        allowed_tags = list(bleach.sanitizer.ALLOWED_TAGS) + [
            'p', 'br', 'pre', 'code', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
            'strong', 'em', 'ul', 'ol', 'li', 'blockquote', 'hr', 'table',
            'thead', 'tbody', 'tr', 'th', 'td', 'div', 'span', 'img', 'a',
            'i', 'b', 'u', 's', 'small', 'mark', 'del', 'ins', 'sub', 'sup',
            'kbd', 'samp', 'var', 'abbr', 'address', 'cite', 'q',
            'section', 'article', 'header', 'footer', 'nav', 'aside', 'main',
            'figure', 'figcaption', 'details', 'summary', 'time', 'dl', 'dt', 'dd'
        ]

        SAFE_HREF_PROTOCOLS = ('http://', 'https://', 'mailto:', '#')

        def allowed_attrs_fn(tag, name, value):
            if name == 'href':
                return any(value.startswith(p) for p in SAFE_HREF_PROTOCOLS)
            if name == 'style':
                # Issue #127: AI-generated docs need width: N% on Bootstrap
                # progress bars. Allow the attribute through; CSSSanitizer
                # below filters property names, so anything that isn't on
                # the property allowlist (width / height / max-width /
                # aspect-ratio) is dropped silently. No CSS injection
                # vector survives.
                return True
            # Allow role + aria-* on tags that drive accessibility for the
            # progress / table / status patterns AI docs emit.
            if name == 'role':
                return value in (
                    'progressbar', 'status', 'alert', 'note',
                    'tab', 'tabpanel', 'tablist',
                )
            if name.startswith('aria-'):
                return True
            # Allow class, id, and data-* attributes on all tags
            if name.startswith('data-'):
                return True
            allowed = {
                '*': ['class', 'id'],
                'img': ['src', 'alt', 'width', 'height', 'loading'],
                'a': ['href', 'title', 'target', 'rel'],
                'td': ['colspan', 'rowspan'],
                'th': ['colspan', 'rowspan', 'scope'],
                'ol': ['start', 'type'],
                'code': ['class'],
                'pre': ['class'],
            }
            global_allowed = allowed.get('*', [])
            tag_allowed = allowed.get(tag, [])
            return name in global_allowed or name in tag_allowed

        # Issue #127: bleach 6+ runs styles through CSSSanitizer separately
        # from the attribute filter. Without an explicit sanitizer the value
        # of `style` is emptied even when the attribute is allowed. Restrict
        # the property allowlist so only the bar-width pattern AI docs need
        # actually survives.
        clean_kwargs = dict(tags=allowed_tags, attributes=allowed_attrs_fn, strip=False)
        if CSSSanitizer is not None:
            clean_kwargs['css_sanitizer'] = CSSSanitizer(
                allowed_css_properties=['width', 'height', 'max-width', 'aspect-ratio'],
            )
        return bleach.clean(html, **clean_kwargs)

    # Backward compatibility
    def render_markdown(self):
        return self.render_content()

    def save(self, *args, **kwargs):
        # Auto-generate slug if not provided
        if not self.slug:
            from django.utils.text import slugify
            self.slug = slugify(self.title)
        # Create version on save if document already exists
        if self.pk:
            self._create_version()
        # v3.17.245 — default the owner to created_by on first save.
        if not self.owner_id and self.created_by_id:
            self.owner_id = self.created_by_id
        # v3.17.245 — stamp published_at on the first transition out of draft.
        if not self.is_draft and self.is_published and not self.published_at:
            from django.utils import timezone as _tz
            self.published_at = _tz.now()
        super().save(*args, **kwargs)

    @property
    def is_review_overdue(self) -> bool:
        """
        v3.17.245: True when the article is past its review window.
        Articles without a `last_reviewed_at` use `published_at` (or
        `created_at` as a final fallback) as the start of the clock.
        Articles with `review_interval_days=0` are never overdue.
        """
        from django.utils import timezone as _tz
        from datetime import timedelta as _td
        if not self.review_interval_days:
            return False
        anchor = self.last_reviewed_at or self.published_at or self.created_at
        if not anchor:
            return False
        due = anchor + _td(days=int(self.review_interval_days))
        return _tz.now() >= due

    @property
    def review_status(self) -> str:
        """`current` / `due_soon` (within 7 days of due) / `overdue` / `no_review`."""
        from django.utils import timezone as _tz
        from datetime import timedelta as _td
        if not self.review_interval_days:
            return 'no_review'
        anchor = self.last_reviewed_at or self.published_at or self.created_at
        if not anchor:
            return 'no_review'
        due = anchor + _td(days=int(self.review_interval_days))
        now = _tz.now()
        if now >= due:
            return 'overdue'
        if (due - now) <= _td(days=7):
            return 'due_soon'
        return 'current'

    def mark_reviewed(self, *, user=None):
        """v3.17.245: stamp last_reviewed_at + last_modified_by."""
        from django.utils import timezone as _tz
        self.last_reviewed_at = _tz.now()
        if user is not None:
            self.last_modified_by = user
        self.save(update_fields=['last_reviewed_at', 'last_modified_by',
                                  'updated_at'])

    def _create_version(self):
        """
        Create a version snapshot before saving changes.
        """
        try:
            old_doc = Document.objects.get(pk=self.pk)
            DocumentVersion.objects.create(
                document=self,
                title=old_doc.title,
                body=old_doc.body,
                version_number=self.versions.count() + 1,
                created_by=self.last_modified_by
            )
        except Document.DoesNotExist:
            pass


class DocumentVersion(BaseModel):
    """
    Document version history.
    """
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name='versions')
    version_number = models.PositiveIntegerField()
    title = models.CharField(max_length=255)
    body = models.TextField()
    content_type = models.CharField(max_length=20, default='markdown')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)

    class Meta:
        db_table = 'document_versions'
        unique_together = [['document', 'version_number']]
        ordering = ['-version_number']

    def __str__(self):
        return f"{self.document.title} v{self.version_number}"


class DocumentFlag(BaseModel):
    """
    User bookmarks/flags on documents.
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='document_flags')
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name='flags')
    color = models.CharField(max_length=20, default='yellow', choices=[
        ('blue', 'Blue'),
        ('green', 'Green'),
        ('purple', 'Purple'),
        ('red', 'Red'),
        ('yellow', 'Yellow'),
    ])
    notes = models.TextField(blank=True)

    class Meta:
        db_table = 'document_flags'
        unique_together = [['user', 'document']]

    def __str__(self):
        return f"{self.user.username} flagged {self.document.title}"


class Diagram(BaseModel):
    """
    Draw.io diagram with versioning support.
    """
    DIAGRAM_TYPES = [
        ('erd', 'Entity Relationship Diagram'),
        ('floorplan', 'Floor Plan'),
        ('flowchart', 'Flowchart'),
        ('network', 'Network Diagram'),
        ('org', 'Organizational Chart'),
        ('other', 'Other'),
        ('process', 'Process Flow'),
        ('rack', 'Rack Layout'),
        ('architecture', 'System Architecture'),
    ]

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name='diagrams',
        null=True,
        blank=True,
        help_text='Organization for org-specific diagrams. NULL for global diagram templates.'
    )
    title = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255)
    description = models.TextField(blank=True)

    # Diagram data (current version)
    diagram_xml = models.TextField(help_text='.drawio XML format')

    # Export caching
    png_export = models.FileField(
        upload_to='diagrams/png/',
        null=True,
        blank=True,
        help_text='PNG export of diagram'
    )
    svg_export = models.FileField(
        upload_to='diagrams/svg/',
        null=True,
        blank=True,
        help_text='SVG export of diagram'
    )
    thumbnail = models.ImageField(
        upload_to='diagrams/thumbnails/',
        null=True,
        blank=True,
        help_text='Thumbnail preview (300x200)'
    )

    # Categorization
    diagram_type = models.CharField(
        max_length=50,
        choices=DIAGRAM_TYPES,
        default='other'
    )
    tags = models.ManyToManyField(Tag, blank=True, related_name='diagrams')

    # Global/template support
    is_global = models.BooleanField(
        default=False,
        help_text='Global diagram - visible to all organizations'
    )
    is_published = models.BooleanField(default=True)
    is_template = models.BooleanField(
        default=False,
        help_text='Diagram template - can be cloned'
    )

    # Metadata
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='diagrams_created'
    )
    last_modified_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='diagrams_modified'
    )
    last_edited_at = models.DateTimeField(auto_now=True)

    # Version tracking
    version_number = models.PositiveIntegerField(default=1)

    objects = OrganizationManager()

    class Meta:
        db_table = 'diagrams'
        unique_together = [['organization', 'slug']]
        ordering = ['-last_edited_at']
        indexes = [
            models.Index(fields=['organization', 'slug']),
            models.Index(fields=['diagram_type']),
            models.Index(fields=['is_global', 'is_published']),
        ]

    def __str__(self):
        prefix = "[GLOBAL] " if self.is_global else ""
        template = "[TEMPLATE] " if self.is_template else ""
        return f"{prefix}{template}{self.title} (v{self.version_number})"

    def save(self, *args, **kwargs):
        from django.utils.text import slugify
        if not self.slug:
            self.slug = slugify(self.title)
        super().save(*args, **kwargs)

    def create_version_snapshot(self, user):
        """Create a version snapshot of current diagram."""
        DiagramVersion.objects.create(
            diagram=self,
            version_number=self.version_number,
            diagram_xml=self.diagram_xml,
            created_by=user
        )


class DiagramVersion(BaseModel):
    """
    Version history for diagrams.
    """
    diagram = models.ForeignKey(
        Diagram,
        on_delete=models.CASCADE,
        related_name='versions'
    )
    version_number = models.PositiveIntegerField()
    diagram_xml = models.TextField(help_text='Snapshot of diagram XML')
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True
    )
    change_notes = models.TextField(blank=True)

    class Meta:
        db_table = 'diagram_versions'
        unique_together = [['diagram', 'version_number']]
        ordering = ['-version_number']
        indexes = [
            models.Index(fields=['diagram', '-version_number']),
        ]

    def __str__(self):
        return f"{self.diagram.title} - v{self.version_number}"


class DiagramAnnotation(BaseModel):
    """
    Annotations on diagrams (comments, notes, highlights).
    """
    diagram = models.ForeignKey(
        Diagram,
        on_delete=models.CASCADE,
        related_name='annotations'
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='diagram_annotations'
    )

    # Annotation content
    text = models.TextField()
    annotation_type = models.CharField(
        max_length=20,
        choices=[
            ('comment', 'Comment'),
            ('issue', 'Issue'),
            ('note', 'Note'),
            ('suggestion', 'Suggestion'),
        ],
        default='note'
    )

    # Position (optional - for pinned annotations)
    position_x = models.IntegerField(null=True, blank=True)
    position_y = models.IntegerField(null=True, blank=True)

    is_resolved = models.BooleanField(default=False)

    class Meta:
        db_table = 'diagram_annotations'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['diagram', '-created_at']),
        ]

    def __str__(self):
        return f"{self.annotation_type}: {self.text[:50]}"
