[SYSTEM]
{% include 'observation/system.md' %}

[TOOLS - ALREADY EXECUTED]
Tools executed in this iteration: {{ tool_calls|length }}

[MESSAGES - HISTORY]
{% for msg in messages[:-1] %}
[{{ msg.role|upper }}]
{{ msg.content }}

{% endfor %}

[USER - RESULTS TO ANALYZE]
{% include 'observation/user.md' %}


