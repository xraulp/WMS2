from django import template
register = template.Library()

@register.filter
def get_item(dictionary, key):
    return dictionary.get(key)

@register.filter
def can_see_tab(profile, tab):
    """Usage: {% if profile|can_see_tab:'form' %}"""
    if profile and hasattr(profile, 'can_see_tab'):
        return profile.can_see_tab(tab)
    return True
