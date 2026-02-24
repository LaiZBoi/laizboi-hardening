"""
Template filters for processes app
"""
from django import template
from django.utils.safestring import mark_safe
from django.utils.html import format_html, conditional_escape
from datetime import datetime
import json

register = template.Library()


@register.filter
def format_audit_value(value):
    """
    Format audit log old_value/new_value for display.

    Converts raw dictionaries into human-readable format.
    """
    if not value:
        return mark_safe('<span class="text-muted">None</span>')

    # If it's already a string (shouldn't happen but handle it)
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return conditional_escape(value)

    # If it's not a dict at this point, just return it
    if not isinstance(value, dict):
        return conditional_escape(str(value))

    # Format the dictionary nicely
    output = []
    for key, val in value.items():
        # Format the key (safe: derived from dict keys, but escape anyway)
        formatted_key = conditional_escape(key.replace('_', ' ').title())

        # Format the value
        if isinstance(val, bool):
            # Boolean indicators are static strings — safe
            formatted_val = mark_safe('✓ Yes' if val else '✗ No')
        elif val is None:
            formatted_val = mark_safe('<span class="text-muted">None</span>')
        elif isinstance(val, str):
            # Check if it's a datetime string
            if 'T' in val or '+' in val or val.count('-') == 2 and val.count(':') == 2:
                try:
                    # Try to parse and format datetime
                    dt = datetime.fromisoformat(val.replace('Z', '+00:00'))
                    formatted_val = conditional_escape(dt.strftime('%Y-%m-%d %H:%M:%S'))
                except (ValueError, AttributeError):
                    formatted_val = conditional_escape(val)
            else:
                formatted_val = conditional_escape(val)
        else:
            formatted_val = conditional_escape(str(val))

        output.append(format_html('<strong>{}:</strong> {}', formatted_key, formatted_val))

    return mark_safe('<br>'.join(str(item) for item in output))
