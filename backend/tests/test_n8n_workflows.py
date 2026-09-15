import json
import os
import pytest

def test_notifications_workflow_if_node():
    workflow_path = os.path.join(os.path.dirname(__file__), '..', 'JP___Notifications.json')
    with open(workflow_path, 'r') as f:
        workflows = json.load(f)

    workflow = workflows[0]

    # Find the IF node
    if_node = next(node for node in workflow['nodes'] if node['name'] == 'Check If Empty')

    assert if_node['type'] == 'n8n-nodes-base.if'
    assert if_node['typeVersion'] == 2.2

    conditions = if_node['parameters']['conditions']['conditions']
    assert len(conditions) == 1

    condition = conditions[0]
    assert condition['leftValue'] == "={{ $json.recommendations.length }}"
    assert condition['rightValue'] == 0
    assert condition['operator']['type'] == 'number'

    # Verify the operation is 'gt' and absolutely not 'larger'
    assert condition['operator']['operation'] == 'gt', "IF node operator must be 'gt' in n8n v2.2 schema, 'larger' will silently fail"
