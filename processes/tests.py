"""
Baseline test coverage for the processes/ app.

Workflow engine — defines reusable Process templates with sequential
stages, executed against tickets. Bug here = silent workflow run
failure (a tech "completes" a runbook but a stage didn't actually
record). Touches PSA tickets via `ProcessExecution.native_psa_ticket`.

Coverage areas:
  * `Process` model — slug auto-generation, OrganizationManager,
    `is_global` vs org-specific.
  * `ProcessStage` ordering + linked-entity contract.
  * `ProcessExecution` lifecycle — `completion_percentage` math,
    `is_overdue` property.
  * `ProcessStageCompletion` unique-together (execution, stage)
    constraint — guards against double-completion of one stage.
"""
from __future__ import annotations

from datetime import timedelta

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from core.models import Organization
from processes.models import (
    Process,
    ProcessExecution,
    ProcessStage,
    ProcessStageCompletion,
)


# ---------------------------------------------------------------------------
# Process model
# ---------------------------------------------------------------------------

class ProcessModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='ProcCo', slug='proc-co')
        cls.user = User.objects.create_user('proc-user', email='p@x.com', password='pw')

    def test_slug_auto_generated_from_title(self):
        p = Process.objects.create(
            organization=self.org, title='Onboarding New Hire',
            created_by=self.user,
        )
        self.assertEqual(p.slug, 'onboarding-new-hire')

    def test_explicit_slug_preserved(self):
        p = Process.objects.create(
            organization=self.org, title='Foo Bar', slug='custom-slug',
            created_by=self.user,
        )
        self.assertEqual(p.slug, 'custom-slug')

    def test_str_marks_global_and_template_prefixes(self):
        normal = Process.objects.create(
            organization=self.org, title='Normal',
            created_by=self.user,
        )
        glob = Process.objects.create(
            organization=self.org, title='Global one', slug='g',
            is_global=True, created_by=self.user,
        )
        templ = Process.objects.create(
            organization=self.org, title='Template one', slug='t',
            is_template=True, created_by=self.user,
        )
        self.assertNotIn('[GLOBAL]', str(normal))
        self.assertIn('[GLOBAL]', str(glob))
        self.assertIn('[TEMPLATE]', str(templ))

    def test_unique_slug_per_organization(self):
        Process.objects.create(
            organization=self.org, title='X', slug='x',
            created_by=self.user,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            Process.objects.create(
                organization=self.org, title='X-dup', slug='x',
                created_by=self.user,
            )

    def test_same_slug_in_different_org_allowed(self):
        Process.objects.create(
            organization=self.org, title='X', slug='x',
            created_by=self.user,
        )
        org_b = Organization.objects.create(name='Other', slug='proc-other')
        # Same slug in different org — must NOT raise.
        Process.objects.create(
            organization=org_b, title='X', slug='x',
            created_by=self.user,
        )

    def test_for_organization_filtering(self):
        org_b = Organization.objects.create(name='ProcOther', slug='proc-other2')
        Process.objects.create(
            organization=self.org, title='A', slug='a',
            created_by=self.user,
        )
        Process.objects.create(
            organization=org_b, title='B', slug='b',
            created_by=self.user,
        )
        for_a = list(Process.objects.for_organization(self.org))
        self.assertEqual(len(for_a), 1)
        self.assertEqual(for_a[0].title, 'A')


# ---------------------------------------------------------------------------
# ProcessStage
# ---------------------------------------------------------------------------

class ProcessStageOrderingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='StageCo', slug='stage-co')
        cls.user = User.objects.create_user('stage-user', email='s@x.com', password='pw')
        cls.process = Process.objects.create(
            organization=cls.org, title='Multi-step', slug='multi-step',
            created_by=cls.user,
        )

    def test_stages_default_to_order_zero(self):
        s = ProcessStage.objects.create(process=self.process, title='step')
        self.assertEqual(s.order, 0)

    def test_explicit_order_preserved(self):
        ProcessStage.objects.create(process=self.process, title='first', order=10)
        ProcessStage.objects.create(process=self.process, title='second', order=20)
        ProcessStage.objects.create(process=self.process, title='middle', order=15)
        ordered = list(self.process.stages.order_by('order').values_list('title', flat=True))
        self.assertEqual(ordered, ['first', 'middle', 'second'])


# ---------------------------------------------------------------------------
# ProcessExecution lifecycle
# ---------------------------------------------------------------------------

class ProcessExecutionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='ExecCo', slug='exec-co')
        cls.user = User.objects.create_user('exec-user', email='e@x.com', password='pw')
        cls.process = Process.objects.create(
            organization=cls.org, title='Three-step', slug='three-step',
            created_by=cls.user,
        )
        cls.stage1 = ProcessStage.objects.create(process=cls.process, title='1', order=1)
        cls.stage2 = ProcessStage.objects.create(process=cls.process, title='2', order=2)
        cls.stage3 = ProcessStage.objects.create(process=cls.process, title='3', order=3)

    def _execution(self):
        return ProcessExecution.objects.create(
            process=self.process, organization=self.org,
            assigned_to=self.user, started_by=self.user,
        )

    def test_execution_starts_not_started(self):
        e = self._execution()
        self.assertEqual(e.status, 'not_started')

    def test_completion_percentage_is_zero_when_no_stages_completed(self):
        e = self._execution()
        self.assertEqual(e.completion_percentage, 0)

    def test_completion_percentage_one_third_when_one_of_three(self):
        e = self._execution()
        ProcessStageCompletion.objects.create(
            execution=e, stage=self.stage1, is_completed=True, completed_by=self.user,
        )
        self.assertEqual(e.completion_percentage, 33)

    def test_completion_percentage_full_when_all_done(self):
        e = self._execution()
        for stage in (self.stage1, self.stage2, self.stage3):
            ProcessStageCompletion.objects.create(
                execution=e, stage=stage, is_completed=True, completed_by=self.user,
            )
        self.assertEqual(e.completion_percentage, 100)

    def test_completion_percentage_handles_zero_stages(self):
        # Process with no stages at all — must not divide by zero.
        empty_proc = Process.objects.create(
            organization=self.org, title='Empty', slug='empty',
            created_by=self.user,
        )
        e = ProcessExecution.objects.create(
            process=empty_proc, organization=self.org,
            assigned_to=self.user, started_by=self.user,
        )
        self.assertEqual(e.completion_percentage, 0)

    def test_is_overdue_true_when_past_due_and_not_completed(self):
        e = self._execution()
        e.due_date = timezone.now() - timedelta(hours=1)
        e.save()
        self.assertTrue(e.is_overdue)

    def test_is_overdue_false_when_completed_even_if_past_due(self):
        e = self._execution()
        e.due_date = timezone.now() - timedelta(hours=1)
        e.status = 'completed'
        e.save()
        self.assertFalse(e.is_overdue)

    def test_is_overdue_false_when_no_due_date(self):
        e = self._execution()
        # No due_date set — the property must short-circuit, not raise on
        # `None > timezone.now()`.
        self.assertFalse(e.is_overdue)


# ---------------------------------------------------------------------------
# ProcessStageCompletion unique constraint — load-bearing for completion %
# ---------------------------------------------------------------------------

class ProcessStageCompletionConstraintTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='CompCo', slug='comp-co')
        cls.user = User.objects.create_user('comp-user', email='c@x.com', password='pw')
        cls.process = Process.objects.create(
            organization=cls.org, title='P', slug='p',
            created_by=cls.user,
        )
        cls.stage = ProcessStage.objects.create(process=cls.process, title='S', order=1)
        cls.execution = ProcessExecution.objects.create(
            process=cls.process, organization=cls.org,
            assigned_to=cls.user, started_by=cls.user,
        )

    def test_same_stage_in_same_execution_rejected(self):
        ProcessStageCompletion.objects.create(
            execution=self.execution, stage=self.stage, is_completed=True,
            completed_by=self.user,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            ProcessStageCompletion.objects.create(
                execution=self.execution, stage=self.stage, is_completed=True,
                completed_by=self.user,
            )

    def test_str_marks_completed_with_check(self):
        c_done = ProcessStageCompletion.objects.create(
            execution=self.execution, stage=self.stage, is_completed=True,
            completed_by=self.user,
        )
        self.assertIn('✓', str(c_done))

    def test_str_marks_uncompleted_with_circle(self):
        # Different stage so the unique-together doesn't fire.
        stage2 = ProcessStage.objects.create(process=self.process, title='S2', order=2)
        c_open = ProcessStageCompletion.objects.create(
            execution=self.execution, stage=stage2, is_completed=False,
        )
        self.assertIn('○', str(c_open))


# ---------------------------------------------------------------------------
# Phase 38 — Runbook clone-template + spawn-ticket
# ---------------------------------------------------------------------------

from django.conf import settings as django_settings
from django.test import Client, override_settings
from accounts.models import Membership, Role


_TEST_MIDDLEWARE = [
    m for m in django_settings.MIDDLEWARE
    if 'Enforce2FAMiddleware' not in m and 'AxesMiddleware' not in m
]


@override_settings(MIDDLEWARE=_TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class ProcessCloneTemplateTests(TestCase):
    """Phase 38: clone an is_template=True Process into a runnable copy."""

    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='RunbookCo', slug='rb-co')
        cls.user = User.objects.create_user('rb-user', 'rb@x.com', 'pw')
        Membership.objects.create(
            user=cls.user, organization=cls.org, role=Role.OWNER, is_active=True,
        )
        cls.tpl = Process.objects.create(
            organization=cls.org, title='Client Onboarding Template',
            description='Standard new-client onboarding',
            is_template=True, category='client_onboarding',
            created_by=cls.user,
        )
        ProcessStage.objects.create(process=cls.tpl, title='Provision M365', order=1,
                                     description='Create accounts')
        ProcessStage.objects.create(process=cls.tpl, title='Set up vault', order=2,
                                     description='Add credentials')
        ProcessStage.objects.create(process=cls.tpl, title='Schedule kickoff', order=3,
                                     description='Send invite')

    def _login(self, c):
        c.force_login(self.user)
        s = c.session
        s['2fa_prompted'] = True
        s['current_organization_id'] = self.org.id
        s.save()

    def test_clone_creates_new_process_with_all_stages(self):
        c = Client()
        self._login(c)
        before = Process.objects.count()
        r = c.post(f'/processes/{self.tpl.slug}/clone-template/')
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Process.objects.count(), before + 1)
        clone = Process.objects.exclude(pk=self.tpl.pk).get(organization=self.org)
        self.assertFalse(clone.is_template)
        self.assertIn('Client Onboarding Template', clone.title)
        self.assertEqual(clone.category, 'client_onboarding')
        self.assertEqual(clone.stages.count(), 3)
        titles = list(clone.stages.order_by('order').values_list('title', flat=True))
        self.assertEqual(titles, ['Provision M365', 'Set up vault', 'Schedule kickoff'])

    def test_clone_rejects_non_template_source(self):
        # Create a NON-template Process and try to clone it.
        non_tpl = Process.objects.create(
            organization=self.org, title='Ad-hoc workflow',
            is_template=False, created_by=self.user,
        )
        c = Client()
        self._login(c)
        before = Process.objects.count()
        r = c.post(f'/processes/{non_tpl.slug}/clone-template/')
        self.assertEqual(r.status_code, 302)
        # No new Process created.
        self.assertEqual(Process.objects.count(), before)

    def test_new_categories_accept_client_onboarding(self):
        # The Process model's CATEGORY_CHOICES gained client_*
        # values in v3.17.223 — make sure they save without ValidationError.
        for cat in ('client_onboarding', 'client_offboarding', 'client_termination'):
            p = Process.objects.create(
                organization=self.org, title=f'Cat {cat}',
                category=cat, created_by=self.user,
                last_modified_by=self.user,
            )
            p.full_clean()
            self.assertEqual(p.category, cat)


@override_settings(MIDDLEWARE=_TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class ProcessStageSpawnTicketTests(TestCase):
    """Phase 38: spawn a PSA Ticket from a runbook stage in a running execution."""

    @classmethod
    def setUpTestData(cls):
        from django.core.management import call_command
        call_command('psa_seed_defaults', verbosity=0)
        cls.org = Organization.objects.create(name='SpawnCo', slug='spawn-co')
        cls.user = User.objects.create_user('spawn-user', 'sp@x.com', 'pw')
        Membership.objects.create(
            user=cls.user, organization=cls.org, role=Role.OWNER, is_active=True,
        )
        cls.process = Process.objects.create(
            organization=cls.org, title='Onboard',
            category='client_onboarding', created_by=cls.user,
        )
        cls.stage = ProcessStage.objects.create(
            process=cls.process, title='Provision endpoint',
            description='Deploy laptop + image', order=1,
        )
        cls.execution = ProcessExecution.objects.create(
            process=cls.process, organization=cls.org,
            assigned_to=cls.user, status='in_progress',
        )

    def _login(self, c):
        c.force_login(self.user)
        s = c.session
        s['2fa_prompted'] = True
        s['current_organization_id'] = self.org.id
        s.save()

    def test_spawn_creates_ticket_and_links_completion(self):
        from psa.models import Ticket
        c = Client()
        self._login(c)
        before = Ticket.objects.filter(organization=self.org).count()
        r = c.post(
            f'/processes/execution/{self.execution.pk}/stage/{self.stage.pk}/spawn-ticket/'
        )
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Ticket.objects.filter(organization=self.org).count(), before + 1)
        completion = ProcessStageCompletion.objects.get(
            execution=self.execution, stage=self.stage,
        )
        self.assertIsNotNone(completion.spawned_ticket)
        self.assertIn('Provision endpoint', completion.spawned_ticket.subject)

    def test_spawn_is_idempotent(self):
        # First call creates; second call must NOT create another ticket.
        from psa.models import Ticket
        c = Client()
        self._login(c)
        c.post(f'/processes/execution/{self.execution.pk}/stage/{self.stage.pk}/spawn-ticket/')
        first_count = Ticket.objects.filter(organization=self.org).count()
        c.post(f'/processes/execution/{self.execution.pk}/stage/{self.stage.pk}/spawn-ticket/')
        second_count = Ticket.objects.filter(organization=self.org).count()
        self.assertEqual(first_count, second_count)

    def test_spawn_rejects_get(self):
        c = Client()
        self._login(c)
        r = c.get(f'/processes/execution/{self.execution.pk}/stage/{self.stage.pk}/spawn-ticket/')
        self.assertEqual(r.status_code, 405)
