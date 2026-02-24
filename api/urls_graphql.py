"""
GraphQL API URLs
"""

from django.urls import path
from graphene_django.views import GraphQLView
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings

from api.graphql.schema import schema


urlpatterns = [
    # GraphQL endpoint - graphiql disabled in production
    path('graphql/', csrf_exempt(GraphQLView.as_view(graphiql=settings.DEBUG, schema=schema)), name='graphql'),
]

if settings.DEBUG:
    # GraphQL Playground only available in development
    urlpatterns += [
        path('graphql/playground/', csrf_exempt(GraphQLView.as_view(graphiql=True, schema=schema)), name='graphql-playground'),
    ]
