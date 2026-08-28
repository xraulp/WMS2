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


# El color del tipo de operacion. Cuando solo habia entrada y salida, cada
# plantilla lo resolvia con un `{% if %}` de dos ramas; con cuatro tipos eso son
# cuatro sitios donde acordarse del quinto.
CLASE_DEL_TIPO = {
    'ENTRY': 'b-entry',
    'EXIT':  'b-exit',
    'TD':    'b-td',
    'RD':    'b-rd',
}


@register.filter
def clase_de_tipo(operation_type):
    """Usage: <span class="badge {{ op.operation_type|clase_de_tipo }}">"""
    return CLASE_DEL_TIPO.get(operation_type, 'b-sent')


# El aviso de permanencia, en clase y en texto. La operacion decide si hay
# alerta (`alerta_permanencia`); esto solo la viste.
CLASE_DE_ALERTA = {'vencida': 'aging-vencida', 'urgente': 'aging-urgente'}


@register.filter
def clase_de_alerta(nivel):
    """Usage: <span class="{{ op.alerta_permanencia|clase_de_alerta }}">"""
    return CLASE_DE_ALERTA.get(nivel, '')
