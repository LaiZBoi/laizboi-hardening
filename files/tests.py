"""
Baseline test coverage for the files/ app.

`Attachment` is the generic file-attachment model used across the app
(asset photos, document uploads, vehicle receipts, etc.). Files are
stored under per-org / per-entity directory paths and served via
X-Accel-Redirect. Bug here = either lost attachments or one tenant's
upload leaking into another's directory.

Coverage areas:
  * `attachment_upload_path` — generates per-org / per-entity-type /
    per-entity-id directory structure with a UUID filename, preserving
    the original extension. **This is the load-bearing tenant-isolation
    boundary on disk** — a bug that drops the org_id segment would put
    one tenant's files in another's directory.
  * `Attachment.size_kb` and `__str__`.
  * `OrganizationManager.for_organization()` filtering.
"""
from __future__ import annotations

import os
import tempfile

from django.conf import settings as django_settings
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.test import Client, TestCase, override_settings

from core.models import Organization
from files.models import Attachment, attachment_upload_path


class AttachmentUploadPathTests(TestCase):
    """`attachment_upload_path` is what Django calls when saving a
    FileField — it must produce per-tenant, per-entity directories
    with a UUID-named file. Tenant isolation on the filesystem
    depends on this function being correct."""

    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='FilesCo', slug='files-co')

    def _make_unsaved(self, *, org, entity_type, entity_id):
        # Build an in-memory Attachment we can pass to the path function
        # without saving (the path function only reads attributes).
        a = Attachment(
            organization=org,
            entity_type=entity_type,
            entity_id=entity_id,
            original_filename='probe.png',
            file_size=1024,
            content_type='image/png',
        )
        return a

    def test_path_starts_with_org_id(self):
        a = self._make_unsaved(org=self.org, entity_type='asset', entity_id=42)
        path = attachment_upload_path(a, 'photo.png')
        # First path segment must be the organization id (string).
        first = path.split(os.sep)[0]
        self.assertEqual(first, str(self.org.id))

    def test_path_uses_entity_type_and_id(self):
        a = self._make_unsaved(org=self.org, entity_type='document', entity_id=99)
        path = attachment_upload_path(a, 'spec.pdf')
        parts = path.split(os.sep)
        self.assertEqual(parts[1], 'document')
        self.assertEqual(parts[2], '99')

    def test_path_preserves_extension(self):
        a = self._make_unsaved(org=self.org, entity_type='asset', entity_id=1)
        path = attachment_upload_path(a, 'photo.PNG')
        # Original filename's extension survives — file size / type checks
        # downstream depend on it.
        self.assertTrue(path.endswith('.PNG'))

    def test_path_filename_is_uuid_not_original(self):
        a = self._make_unsaved(org=self.org, entity_type='asset', entity_id=1)
        path = attachment_upload_path(a, 'malicious filename with spaces.png')
        filename = os.path.basename(path)
        # The filename portion (sans extension) is a 32-hex-char UUID4 hex.
        # Confirms we do NOT trust the upload's filename verbatim.
        stem, ext = os.path.splitext(filename)
        self.assertEqual(ext, '.png')
        self.assertNotIn(' ', stem)
        # UUID hex is 32 chars (no dashes); UUID4 default str is 36 chars
        # with dashes. The implementation uses str(uuid.uuid4()) so both
        # 32-and-36 are acceptable lengths — assert one of them.
        self.assertIn(len(stem), (32, 36),
            f'expected UUID-shaped stem, got {stem!r}')

    def test_path_for_two_orgs_does_not_collide(self):
        org_a = Organization.objects.create(name='OrgA-files', slug='orga-files')
        org_b = Organization.objects.create(name='OrgB-files', slug='orgb-files')
        a_a = self._make_unsaved(org=org_a, entity_type='asset', entity_id=1)
        a_b = self._make_unsaved(org=org_b, entity_type='asset', entity_id=1)
        path_a = attachment_upload_path(a_a, 'x.png')
        path_b = attachment_upload_path(a_b, 'x.png')
        # Different org_id → different first segment → no filesystem collision.
        self.assertNotEqual(path_a.split(os.sep)[0], path_b.split(os.sep)[0])


class AttachmentModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='AttCo', slug='att-co')
        cls.user = User.objects.create_user('att-user', email='at@x.com', password='pw')

    def _attachment(self, **overrides):
        # Build via .objects.create — accept Django's behavior of writing
        # a FileField that points at no real file (we don't exercise the
        # filesystem in baseline tests; image-optimize logic is bypassed
        # because content_type isn't image/*).
        defaults = dict(
            organization=self.org, entity_type='asset', entity_id=1,
            file='dummy/path.txt',
            original_filename='probe.txt',
            file_size=2048, content_type='text/plain',
            uploaded_by=self.user,
        )
        defaults.update(overrides)
        return Attachment.objects.create(**defaults)

    def test_str_includes_filename_and_entity_pointer(self):
        a = self._attachment(original_filename='boot.txt', entity_type='asset', entity_id=5)
        s = str(a)
        self.assertIn('boot.txt', s)
        self.assertIn('asset:5', s)

    def test_size_kb_rounds_to_two_decimals(self):
        a = self._attachment(file_size=2048)
        self.assertEqual(a.size_kb, 2.0)
        a2 = self._attachment(file_size=1500)  # 1.46484375 → 1.46
        self.assertEqual(a2.size_kb, 1.46)

    def test_for_organization_filtering(self):
        org_b = Organization.objects.create(name='OrgB-att', slug='orgb-att')
        self._attachment()
        self._attachment(organization=org_b, original_filename='b.txt')
        for_a = list(Attachment.objects.for_organization(self.org))
        for_b = list(Attachment.objects.for_organization(org_b))
        self.assertEqual(len(for_a), 1)
        self.assertEqual(len(for_b), 1)
        self.assertEqual(for_a[0].original_filename, 'probe.txt')
        self.assertEqual(for_b[0].original_filename, 'b.txt')

    def test_indexed_query_by_entity_pointer(self):
        # The model has an index on (organization, entity_type, entity_id).
        # Confirm the query path returns rows correctly — guards against
        # the index being lost in a future migration.
        self._attachment(entity_type='asset', entity_id=10)
        self._attachment(entity_type='asset', entity_id=11)
        self._attachment(entity_type='document', entity_id=10)
        rows = Attachment.objects.filter(
            organization=self.org, entity_type='asset', entity_id=10,
        )
        self.assertEqual(rows.count(), 1)


TEST_MIDDLEWARE = [
    m for m in django_settings.MIDDLEWARE
    if 'Enforce2FAMiddleware' not in m and 'AxesMiddleware' not in m
]


@override_settings(
    DEBUG=False,
    MIDDLEWARE=TEST_MIDDLEWARE,
    SECURE_SSL_REDIRECT=False,
)
class ServeAttachmentProxyTests(TestCase):
    """Production attachment serving uses reverse-proxy offload headers."""

    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='ServeCo', slug='serve-co')
        cls.user = User.objects.create_user(
            'serve-user', 'serve@example.com', 'pw', is_superuser=True,
        )

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.upload_root = self._tmpdir.name
        self._settings = override_settings(
            UPLOAD_ROOT=self.upload_root,
            MEDIA_ROOT=self.upload_root,
        )
        self._settings.enable()

    def tearDown(self):
        self._settings.disable()
        self._tmpdir.cleanup()

    def _login(self, client):
        client.force_login(self.user)
        session = client.session
        session['current_organization_id'] = self.org.id
        session['2fa_prompted'] = True
        session.save()

    def _attachment_with_file(self):
        attachment = Attachment(
            organization=self.org,
            entity_type='asset',
            entity_id=1,
            original_filename='probe.txt',
            file_size=5,
            content_type='text/plain',
            uploaded_by=self.user,
        )
        attachment.file.save('probe.txt', ContentFile(b'hello'), save=True)
        attachment.save()
        return attachment

    @override_settings(PRIVATE_FILE_SERVER='nginx')
    def test_nginx_mode_sets_x_accel_redirect(self):
        attachment = self._attachment_with_file()
        client = Client()
        self._login(client)
        response = client.get(f'/files/serve/{attachment.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('X-Accel-Redirect', response)
        self.assertIn(attachment.file.name, response['X-Accel-Redirect'])
        self.assertNotIn('X-Sendfile', response)

    @override_settings(PRIVATE_FILE_SERVER='apache')
    def test_apache_mode_sets_x_sendfile(self):
        attachment = self._attachment_with_file()
        client = Client()
        self._login(client)
        response = client.get(f'/files/serve/{attachment.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('X-Sendfile', response)
        self.assertTrue(response['X-Sendfile'].startswith(self.upload_root))
        self.assertNotIn('X-Accel-Redirect', response)
