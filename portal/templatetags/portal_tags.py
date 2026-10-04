from django import template

from portal import roles

register = template.Library()
# Lets templates write {% if user|is_hod %} ... {% endif %}
register.filter("is_hod", roles.is_hod)
register.filter("is_lecturer", roles.is_lecturer)
register.filter("is_student", roles.is_student)
