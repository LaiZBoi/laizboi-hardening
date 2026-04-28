from django.urls import path

from . import views

app_name = 'psa_ai'

urlpatterns = [
    path('inbox/', views.ai_inbox, name='inbox'),
    path('generate-reply/<str:ticket_number>/', views.generate_reply, name='generate_reply'),
    path('generate-actions/<str:ticket_number>/', views.generate_actions, name='generate_actions'),
    path('suggestion/<int:pk>/', views.suggestion_detail, name='suggestion_detail'),
    path('suggestion/<int:pk>/reject/', views.suggestion_reject, name='suggestion_reject'),
    path('suggestion/<int:pk>/request-approval/', views.suggestion_request_approval, name='suggestion_request_approval'),
    path('suggestion/<int:pk>/apply/', views.suggestion_approve_and_apply, name='suggestion_apply'),
]
