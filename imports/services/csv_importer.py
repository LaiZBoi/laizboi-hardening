"""
CSV import service - imports rows from a CSV file using field_mappings.
"""
import csv
import io
import logging

logger = logging.getLogger('imports')

# Target fields available per model, with human labels
ASSET_TARGET_FIELDS = {
    'name': 'Name (required)',
    'asset_type': 'Asset Type',
    'asset_tag': 'Asset Tag',
    'serial_number': 'Serial Number',
    'manufacturer': 'Manufacturer',
    'model': 'Model',
    'hostname': 'Hostname',
    'ip_address': 'IP Address',
    'mac_address': 'MAC Address',
    'os_version': 'OS / Firmware Version',
    'notes': 'Notes',
    '__skip__': '— Skip this column —',
}

PASSWORD_TARGET_FIELDS = {
    'name': 'Name (required)',
    'username': 'Username',
    'password': 'Password',
    'url': 'URL',
    'notes': 'Notes',
    '__skip__': '— Skip this column —',
}

CONTACT_TARGET_FIELDS = {
    'name': 'Full Name (required)',
    'first_name': 'First Name',
    'last_name': 'Last Name',
    'email': 'Email',
    'phone': 'Phone',
    'title': 'Title / Role',
    'notes': 'Notes',
    '__skip__': '— Skip this column —',
}

DOCUMENT_TARGET_FIELDS = {
    'title': 'Title (required)',
    'body': 'Body / Content',
    '__skip__': '— Skip this column —',
}

TARGET_FIELDS = {
    'asset': ASSET_TARGET_FIELDS,
    'password': PASSWORD_TARGET_FIELDS,
    'contact': CONTACT_TARGET_FIELDS,
    'document': DOCUMENT_TARGET_FIELDS,
}


def read_csv_preview(file_obj, max_rows=5):
    """
    Read CSV headers and up to max_rows of sample data.
    Returns (headers, rows) where rows is a list of dicts.
    """
    file_obj.seek(0)
    content = file_obj.read()
    if isinstance(content, bytes):
        content = content.decode('utf-8-sig', errors='replace')

    reader = csv.DictReader(io.StringIO(content))
    headers = reader.fieldnames or []
    rows = []
    for i, row in enumerate(reader):
        if i >= max_rows:
            break
        rows.append(dict(row))
    return headers, rows


class CSVImportService:
    """Import CSV rows into Assets, Passwords, Contacts, or Documents."""

    def __init__(self, import_job):
        self.job = import_job
        self.org = import_job.target_organization

    def run_import(self):
        """Execute the CSV import. Returns stats dict."""
        job = self.job
        mappings = job.field_mappings or {}
        target_model = job.csv_target_model

        if not job.source_file:
            raise ValueError("No CSV file attached to this import job.")
        if not target_model:
            raise ValueError("No target model selected for CSV import.")
        if not self.org:
            raise ValueError("Target organization is required for CSV imports.")

        job.mark_running()

        job.source_file.seek(0)
        content = job.source_file.read()
        if isinstance(content, bytes):
            content = content.decode('utf-8-sig', errors='replace')

        reader = csv.DictReader(io.StringIO(content))
        rows = list(reader)
        job.total_items = len(rows)
        job.save(update_fields=['total_items'])

        job.add_log(f"Starting CSV import: {len(rows)} rows → {target_model}")

        imported = skipped = failed = 0

        for i, row in enumerate(rows, 1):
            try:
                # Apply field mappings: build target_fields dict from source columns
                target_fields = {}
                for src_col, tgt_field in mappings.items():
                    if tgt_field and tgt_field != '__skip__':
                        value = (row.get(src_col) or '').strip()
                        if value:
                            target_fields[tgt_field] = value

                if not target_fields:
                    skipped += 1
                    continue

                if job.dry_run:
                    imported += 1
                    continue

                ok = self._create_record(target_model, target_fields, i)
                if ok:
                    imported += 1
                else:
                    skipped += 1

            except Exception as e:
                logger.warning(f"CSV row {i} failed: {e}")
                job.add_log(f"Row {i}: ERROR — {e}")
                failed += 1

        job.items_imported = imported
        job.items_skipped = skipped
        job.items_failed = failed
        job.save(update_fields=['items_imported', 'items_skipped', 'items_failed'])
        job.mark_completed()
        job.add_log(f"Done: {imported} imported, {skipped} skipped, {failed} failed")
        return {'imported': imported, 'skipped': skipped, 'failed': failed}

    def _create_record(self, target_model, fields, row_num):
        from imports.models import ImportMapping

        if target_model == 'asset':
            return self._create_asset(fields, row_num)
        elif target_model == 'password':
            return self._create_password(fields, row_num)
        elif target_model == 'contact':
            return self._create_contact(fields, row_num)
        elif target_model == 'document':
            return self._create_document(fields, row_num)
        return False

    def _create_asset(self, fields, row_num):
        from assets.models import Asset
        from django.core.validators import validate_ipv46_address

        name = fields.get('name', '').strip()
        if not name:
            return False

        asset_fields = {
            'organization': self.org,
            'name': name,
            'asset_type': fields.get('asset_type', 'other'),
            'asset_tag': fields.get('asset_tag', ''),
            'serial_number': fields.get('serial_number', ''),
            'manufacturer': fields.get('manufacturer', ''),
            'model': fields.get('model', ''),
            'hostname': fields.get('hostname', ''),
            'mac_address': fields.get('mac_address', ''),
            'os_version': fields.get('os_version', ''),
            'notes': fields.get('notes', ''),
        }

        # Validate IP address
        raw_ip = fields.get('ip_address', '')
        if raw_ip:
            try:
                validate_ipv46_address(raw_ip)
                asset_fields['ip_address'] = raw_ip
            except Exception:
                pass

        # Validate asset_type against known choices
        valid_types = {k for k, _ in Asset.ASSET_TYPES}
        if asset_fields['asset_type'] not in valid_types:
            asset_fields['asset_type'] = 'other'

        asset = Asset.objects.create(**{k: v for k, v in asset_fields.items() if v or k in ('organization', 'name', 'asset_type')})
        from imports.models import ImportMapping
        ImportMapping.objects.create(
            import_job=self.job,
            source_type='csv_asset',
            source_id=str(row_num),
            target_model='Asset',
            target_id=asset.id,
            target_organization=self.org,
        )
        return True

    def _create_password(self, fields, row_num):
        from vault.models import Password

        name = fields.get('name', '').strip()
        if not name:
            return False

        pwd = Password.objects.create(
            organization=self.org,
            name=name,
            username=fields.get('username', ''),
            password=fields.get('password', ''),
            url=fields.get('url', ''),
            notes=fields.get('notes', ''),
        )
        from imports.models import ImportMapping
        ImportMapping.objects.create(
            import_job=self.job,
            source_type='csv_password',
            source_id=str(row_num),
            target_model='Password',
            target_id=pwd.id,
            target_organization=self.org,
        )
        return True

    def _create_contact(self, fields, row_num):
        from assets.models import Contact

        # Support both full name and first/last separately
        name = fields.get('name', '').strip()
        first = fields.get('first_name', '').strip()
        last = fields.get('last_name', '').strip()
        if not name:
            name = f"{first} {last}".strip()
        if not name:
            return False

        contact = Contact.objects.create(
            organization=self.org,
            name=name,
            email=fields.get('email', ''),
            phone=fields.get('phone', ''),
            title=fields.get('title', ''),
            notes=fields.get('notes', ''),
        )
        from imports.models import ImportMapping
        ImportMapping.objects.create(
            import_job=self.job,
            source_type='csv_contact',
            source_id=str(row_num),
            target_model='Contact',
            target_id=contact.id,
            target_organization=self.org,
        )
        return True

    def _create_document(self, fields, row_num):
        from docs.models import Document
        from django.utils.text import slugify

        title = fields.get('title', '').strip()
        if not title:
            return False

        doc = Document.objects.create(
            organization=self.org,
            title=title,
            body=fields.get('body', ''),
            content_type='html',
        )
        from imports.models import ImportMapping
        ImportMapping.objects.create(
            import_job=self.job,
            source_type='csv_document',
            source_id=str(row_num),
            target_model='Document',
            target_id=doc.id,
            target_organization=self.org,
        )
        return True
