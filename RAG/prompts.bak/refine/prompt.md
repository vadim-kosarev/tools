[SYSTEM]
{% include 'refine/system.md' %}

[MESSAGES - HISTORY]
{% for msg in messages %}
[{{ msg.role|upper }}]
{{ msg.content }}

{% endfor %}

[USER - CURRENT STATE]
{% include 'refine/user.md' %}


