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


@register.filter
def can_assign_role(profile, role):
    """Usage: {% if profile|can_assign_role:'superadmin' %}

    Sirve para no pintar en el desplegable un rol que el servidor va a rechazar.
    La decision de verdad se toma en la vista; esto solo evita ofrecerlo.
    """
    if profile and hasattr(profile, 'can_assign_role'):
        return profile.can_assign_role(role)
    return False


@register.filter
def can_edit_catalog(profile, category):
    """Usage: {% if profile|can_edit_catalog:'CUSTOMER' %}"""
    if profile and hasattr(profile, 'can_edit_catalog'):
        return profile.can_edit_catalog(category)
    return False
