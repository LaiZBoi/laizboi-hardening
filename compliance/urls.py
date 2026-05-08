from django.urls import path

from . import views

app_name = 'compliance'

urlpatterns = [
    path('organizations/<int:org_id>/evidence-pack/',
         views.evidence_pack, name='evidence_pack'),
    # Phase 41 — frameworks + attestation
    path('organizations/<int:org_id>/',
         views.org_compliance_dashboard, name='org_dashboard'),
    path('organizations/<int:org_id>/enroll/<slug:framework_slug>/',
         views.enroll_framework, name='enroll_framework'),
    # checklist view + PDF land in v3.17.440 / v3.17.441; stub the
    # routes now so the dashboard template's {% url %} lookups resolve.
    path('organizations/<int:org_id>/<slug:framework_slug>/',
         views.checklist_view, name='checklist'),
    path('organizations/<int:org_id>/<slug:framework_slug>/report.pdf',
         views.compliance_report_pdf, name='report_pdf'),
    path('organizations/<int:org_id>/<slug:framework_slug>/save/',
         views.checklist_save, name='checklist_save'),
]
