"""Customer portal URLs — kept off the main staff /psa/ path for clarity."""
from django.urls import path

from . import views

app_name = 'portal'

urlpatterns = [
    path('', views.ticket_list, name='ticket_list'),
    path('new/', views.ticket_create, name='ticket_create'),
    path('t/<str:ticket_number>/', views.ticket_detail, name='ticket_detail'),
    path('t/<str:ticket_number>/reply/', views.post_reply, name='post_reply'),
]
