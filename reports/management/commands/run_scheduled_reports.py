"""
Process scheduled reports whose next_run <= now. Runs every 15 min via
cron. For each due schedule:
  1. Generate the report (PDF or CSV) using the existing
     reports.generators module
  2. Email it to recipients
  3. Update last_run + compute next_run from frequency
  4. Save a GeneratedReport row for audit trail

v3.17.147 — Phase 3.6 wave B.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = 'Process scheduled reports whose next_run is due.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help="Don't generate / email / advance — just list due rows.")
        parser.add_argument('--force-id', type=int,
                            help='Force-run a specific schedule by id (ignores next_run).')

    def handle(self, *args, **opts):
        from reports.models import ScheduledReport
        now = timezone.now()
        if opts.get('force_id'):
            qs = ScheduledReport.objects.filter(pk=opts['force_id'])
        else:
            qs = ScheduledReport.objects.filter(
                is_active=True, next_run__lte=now,
            )
        ran = 0
        for sched in qs:
            self.stdout.write(
                f'Running #{sched.id} {sched.name} (next_run={sched.next_run})'
            )
            if opts.get('dry_run'):
                continue
            try:
                self._run_one(sched)
                ran += 1
            except Exception as exc:
                self.stderr.write(f'  x {sched.name} failed: {exc}')
                # Don't update next_run on failure — let next tick retry
                continue
        verb = 'Would run' if opts.get('dry_run') else 'Ran'
        suffix = '' if ran == 1 else 's'
        self.stdout.write(self.style.SUCCESS(
            f'{verb} {ran} schedule{suffix}'
        ))

    # ------------------------------------------------------------------ helpers

    def _run_one(self, sched):
        from django.core.files.base import ContentFile
        from reports.generators import generate_report
        from reports.models import GeneratedReport

        output_format = (sched.output_format or 'pdf').lower()

        # Generate the artifact (filename + bytes)
        filename, artifact_bytes = generate_report(
            template=sched.template,
            output_format=output_format,
            organization=sched.organization,
            parameters=sched.parameters or {},
        )

        # Save GeneratedReport row for audit trail. Persist the artifact
        # via the FileField so it shows up in the Generated list.
        gen = GeneratedReport.objects.create(
            template=sched.template,
            scheduled_report=sched,
            organization=sched.organization,
            generated_by=None,  # cron-generated
            format=self._normalize_format(output_format),
            status='completed',
            parameters=sched.parameters or {},
        )
        try:
            gen.file.save(filename, ContentFile(artifact_bytes), save=False)
            gen.file_size = len(artifact_bytes)
            gen.completed_at = timezone.now()
            gen.save(update_fields=['file', 'file_size', 'completed_at'])
        except Exception:
            # Storing the file is best-effort — don't fail the schedule run.
            pass

        # Email to recipients
        if sched.recipients:
            self._email_artifact(sched, gen, filename, artifact_bytes,
                                 output_format)

        # Roll forward
        sched.last_run = timezone.now()
        sched.next_run = self._next_run(sched, sched.last_run)
        sched.save(update_fields=['last_run', 'next_run'])

    @staticmethod
    def _normalize_format(fmt):
        # GeneratedReport.format choices: pdf | excel | csv | json
        f = (fmt or 'pdf').lower()
        if f in ('pdf', 'csv', 'json', 'excel'):
            return f
        if f in ('xlsx', 'xls'):
            return 'excel'
        return 'pdf'

    def _next_run(self, sched, from_dt):
        freq = (sched.frequency or 'daily').lower()
        if freq == 'hourly':
            return from_dt + timedelta(hours=1)
        if freq == 'daily':
            return from_dt + timedelta(days=1)
        if freq == 'weekly':
            return from_dt + timedelta(days=7)
        if freq == 'monthly':
            return from_dt + timedelta(days=30)
        if freq == 'quarterly':
            return from_dt + timedelta(days=90)
        return from_dt + timedelta(days=1)

    def _email_artifact(self, sched, gen, filename, artifact_bytes,
                        output_format):
        from django.conf import settings
        from django.core.mail import EmailMessage

        recipients = sched.recipients
        if isinstance(recipients, str):
            recipients = [r.strip()
                          for r in recipients.replace(';', ',').split(',')
                          if r.strip()]
        if not recipients:
            return

        subject = f'[Client St0r] Scheduled report: {sched.name}'
        body = (
            f'Your scheduled report "{sched.name}" was generated at '
            f'{gen.created_at}.\n\nSee attached.'
        )
        msg = EmailMessage(
            subject=subject, body=body,
            from_email=(getattr(settings, 'DEFAULT_FROM_EMAIL', None)
                        or 'noreply@localhost'),
            to=recipients,
        )
        ext = (output_format or 'pdf').lower()
        mime = {
            'pdf': 'application/pdf',
            'csv': 'text/csv',
            'json': 'application/json',
            'excel': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        }.get(ext, 'application/octet-stream')
        msg.attach(filename, artifact_bytes, mime)
        msg.send(fail_silently=False)
