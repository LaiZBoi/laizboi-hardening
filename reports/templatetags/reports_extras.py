"""Small template helpers for the reports app."""
from django import template

register = template.Library()


@register.filter
def get_item(d, key):
    """Dictionary lookup by key — used by ticket-aging matrix template
    where the key is a runtime string (`{{ by_bucket|get_item:label }}`)
    and Django's stock dot-syntax can't be used."""
    if d is None:
        return ''
    try:
        return d.get(key, '')
    except AttributeError:
        return ''
