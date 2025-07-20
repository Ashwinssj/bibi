from django import template

register = template.Library()

@register.filter(name='get_item')
def get_item(dictionary, key):
    """
    Allows accessing dictionary items by key in Django templates.
    Usage: {{ my_dict|get_item:my_key }}
    """
    return dictionary.get(key)

