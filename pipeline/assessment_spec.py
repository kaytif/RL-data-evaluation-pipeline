import csv
import io
import json

def assessment_spec(snapshot):
    """Preserve prose; never silently translate arbitrary text into code."""
    rows = []
    def add(code, scope, rule, mode, **details):
        rows.append(dict(requirement_id=code, scope=scope, rule=rule, assessment=mode, **details))
    for i, row in enumerate(snapshot['quantities'], 1):
        add(f'QTY-{i:03}', row['category'], 'Minimum distinct tasks and attempts per task', 'automatic', **row)
    add('CAT-001', 'All tasks', snapshot['category_rule'], 'automatic')
    for code, rule in [('ID-001', 'Unique nonempty task IDs and task versions'),
                       ('ID-002', 'Unique nonempty attempt IDs'),
                       ('LINK-001', 'Attempts link to exact task versions; checks link to attempts'),
                       ('TRACE-001', 'Each attempt includes model and a nonempty trace array of objects')]:
        add(code, 'All tasks', rule, 'automatic')
    for i, text in enumerate(snapshot['completion_defaults'], 1):
        add(f'COMP-{i:03}', 'All tasks', text, 'presence_check_and_human_review')
    for i, text in enumerate(snapshot['scoring_defaults'], 1):
        add(f'SCORE-{i:03}', 'All tasks', text, 'human_review' if snapshot.get('scoring_requirements') else 'reported_evidence_consistency')
    for field, prefix in [('completion_requirements', 'COMP-EXTRA'), ('scoring_requirements', 'SCORE-EXTRA')]:
        for i, row in enumerate(snapshot.get(field, []), 1):
            add(f'{prefix}-{i:03}', row['scope'], row['requirement'], 'human_review')
    add('USE-001', 'All tasks', snapshot['required_capabilities'], 'human_review')
    add('SCOPE-001', 'All tasks', snapshot['outside_scope'], 'human_review')
    if snapshot.get('comments'):
        add('OTHER-001', 'All tasks', snapshot['comments'], 'human_review')
    return {'schema_version': 1, 'client_id': snapshot['client_id'], 'task_id': snapshot['task_id'],
            'rubric_version': snapshot['rubric_version'], 'requirements': rows}

def spec_csv(spec):
    out = io.StringIO(newline='')
    fields = ['client_id', 'task_id', 'requirement_revision_id', 'requirement_revision', 'requirement_id', 'scope', 'rule', 'assessment', 'minimum_tasks', 'attempts_per_task']
    writer = csv.DictWriter(out, fields, extrasaction='ignore')
    writer.writeheader()
    writer.writerows({**{key: spec.get(key, '') for key in fields[:4]}, **row} for row in spec['requirements'])
    return out.getvalue().encode('utf-8-sig')

TEMPLATE_FIELDS = {
    'tasks': ['task_id', 'task_version', 'category', 'instructions', 'expected_result', 'grader_reference', 'special_case_behavior', 'completion_checks'],
    'attempts': ['attempt_id', 'task_id', 'task_version', 'model', 'reward', 'trace'],
    'checks': ['attempt_id', 'check_name', 'expected_result', 'actual_result', 'status', 'unverified_reason'],
}

def template(role):
    out = io.StringIO(newline='')
    csv.writer(out).writerow(TEMPLATE_FIELDS[role])
    return out.getvalue().encode('utf-8-sig')
