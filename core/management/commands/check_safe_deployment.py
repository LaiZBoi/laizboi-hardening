"""
Audit privacy-safe and security-safe deployment settings.

Usage: python manage.py check_safe_deployment
"""
import sys

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Print PASS/WARN/FAIL checks for privacy-safe production deployment'

    def handle(self, *args, **options):
        fail_count = 0
        warn_count = 0

        def emit(level, label, detail=''):
            nonlocal fail_count, warn_count
            suffix = f' — {detail}' if detail else ''
            if level == 'pass':
                self.stdout.write(self.style.SUCCESS(f'PASS  {label}{suffix}'))
            elif level == 'warn':
                warn_count += 1
                self.stdout.write(self.style.WARNING(f'WARN  {label}{suffix}'))
            else:
                fail_count += 1
                self.stdout.write(self.style.ERROR(f'FAIL  {label}{suffix}'))

        emit('pass' if not settings.DEBUG else 'fail',
             'DEBUG is False',
             'set DEBUG=False for production')

        emit('pass' if settings.SECRET_KEY else 'fail',
             'SECRET_KEY is set')

        emit('pass' if getattr(settings, 'APP_MASTER_KEY', '') else 'fail',
             'APP_MASTER_KEY is set',
             'required for vault encryption; back it up with your database')

        emit('pass' if getattr(settings, 'API_KEY_SECRET', '') else 'fail',
             'API_KEY_SECRET is set')

        auto_update = getattr(settings, 'AUTO_UPDATE_ENABLED', False)
        emit('pass' if not auto_update else 'warn',
             'AUTO_UPDATE_ENABLED is False',
             'currently True — web/scheduled update execution is enabled')

        upstream = (getattr(settings, 'BETA_UPSTREAM_URL', '') or '').strip()
        emit('pass' if not upstream else 'warn',
             'BETA_UPSTREAM_URL is blank',
             f'currently set to {upstream!r}')

        beta_email = (getattr(settings, 'BETA_ADMIN_EMAIL', '') or '').strip()
        emit('pass' if not beta_email else 'warn',
             'BETA_ADMIN_EMAIL is blank',
             f'currently set to {beta_email!r}')

        hibp = getattr(settings, 'HIBP_ENABLED', False)
        emit('pass' if not hibp else 'warn',
             'HIBP_ENABLED is False',
             'currently True — password breach checks may call api.pwnedpasswords.com')

        emit('pass' if getattr(settings, 'REQUIRE_2FA', False) else 'fail',
             'REQUIRE_2FA is True')

        emit('pass' if settings.SESSION_COOKIE_SECURE else 'fail',
             'SESSION_COOKIE_SECURE is True')

        emit('pass' if settings.CSRF_COOKIE_SECURE else 'fail',
             'CSRF_COOKIE_SECURE is True')

        hosts = list(getattr(settings, 'ALLOWED_HOSTS', []))
        wildcard = '*' in hosts or not hosts
        emit('fail' if wildcard else 'pass',
             'ALLOWED_HOSTS is not wildcard',
             'set explicit hostnames in ALLOWED_HOSTS')

        csrf_origins = list(getattr(settings, 'CSRF_TRUSTED_ORIGINS', []))
        emit('pass' if csrf_origins else 'fail',
             'CSRF_TRUSTED_ORIGINS is set',
             'set full https:// origins for HTTPS POST requests')

        db_engine = settings.DATABASES['default']['ENGINE']
        db_password = settings.DATABASES['default'].get('PASSWORD', '')
        if 'sqlite' in db_engine:
            emit('warn', 'DB_PASSWORD is set', 'using SQLite — no DB_PASSWORD required')
        else:
            emit('pass' if db_password else 'fail',
                 'DB_PASSWORD is set')

        file_server = getattr(settings, 'PRIVATE_FILE_SERVER', 'nginx').lower()
        emit('pass' if file_server in ('nginx', 'apache') else 'warn',
             'PRIVATE_FILE_SERVER is nginx or apache',
             f'currently {file_server!r} — use apache with mod_xsendfile or nginx with X-Accel-Redirect')

        self.stdout.write('')
        if fail_count:
            self.stdout.write(self.style.ERROR(
                f'{fail_count} check(s) failed, {warn_count} warning(s)'
            ))
            sys.exit(1)

        if warn_count:
            self.stdout.write(self.style.WARNING(
                f'All required checks passed with {warn_count} warning(s)'
            ))
        else:
            self.stdout.write(self.style.SUCCESS('All checks passed'))
