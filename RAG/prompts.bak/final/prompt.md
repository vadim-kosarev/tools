[SYSTEM]
{% include 'final/system.md' %}

[MESSAGES - FULL HISTORY]
{% for msg in messages %}
[{{ msg.role|upper }}]
{{ msg.content }}

{% endfor %}

[USER - FINAL REQUEST]
{% include 'final/user.md' %}


