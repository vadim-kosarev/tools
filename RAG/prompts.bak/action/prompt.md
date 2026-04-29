[SYSTEM]
{% include 'action/system.md' %}

[TOOLS]
{{ available_tools }}

[MESSAGES - HISTORY]
{% for msg in messages %}
[{{ msg.role|upper }}]
{{ msg.content }}

{% endfor %}

[USER - CURRENT REQUEST]
{% include 'action/user.md' %}


