"""Plain-language presentation of saved checks in exactly two assessment sections."""
import re


def one_line(value, limit=150):
    text = ' '.join(str(value or '').split())
    if text.startswith(('{', '[')) or re.search(r'\b[a-f0-9]{24,}\b', text):
        return 'Evidence needs review.'
    return text if len(text) <= limit else text[:limit - 3].rstrip() + '...'


def section_for(check):
    if check['phase'] == 'Candidate rewards':
        return None  # A valid reward-0 example is not a dataset defect.
    return 'Data quality' if check['phase'] in ('Data quality', 'Reward evidence') else 'Task coverage'


def finding_text(check):
    code = check['requirement_id']
    passed = check['status'] == 'pass'
    if code == 'MISSING-DATA':
        if passed:
            return 'No required fields are missing.'
        names = {name for fields in check.get('missing_fields', {}).values() for name in fields}
        labels = {'actual_result': 'answer', 'actual_output': 'answer', 'actual_answer': 'answer',
                  'candidate_response': 'answer', 'response': 'answer', 'output': 'answer', 'state_json': 'task context'}
        names = sorted({labels.get(n, n.replace('_', ' ')) for n in names})
        return ('Missing ' + ' and '.join(names) + '.') if names else 'Required fields are missing.'
    if code.startswith('QTY-'):
        target = re.match(r'(\d+) distinct tasks in (.+)', check.get('expected', ''))
        found = re.match(r'(\d+)', check.get('observed', ''))
        if target and found:
            return one_line(f'{found[1]} of {target[1]} required {target[2]} tasks identified.')
        return 'The minimum number of distinct tasks is not established.'
    if code == 'ATTEMPTS-PER-TASK':
        actual = re.match(r'(\d+)', check.get('observed', ''))
        expected = str(check.get('expected', ''))
        if check['status'] == 'fail' and actual and expected.isdigit():
            return f'{actual[1]} of {expected} required attempts identified for this task.'
        if 'incomplete' in check.get('observed', ''):
            return 'Execution IDs are missing; separate attempts cannot be verified.'
        return 'The per-task attempt minimum is met.' if passed else 'The task category or attempt count needs review.'
    messages = {
        'FILE-READABLE': ('File is readable.', 'File is unreadable, empty, or over the assessment limit.'),
        'JSON-VALID': ('Nested JSON is valid.', 'Nested JSON is invalid.'),
        'DUPLICATE-ROW': ('No duplicate rows found.', 'Possible duplicate records need review.'),
        'REWARD-FORMAT': ('Reward format is valid.', 'A submitted reward is not 0 or 1.'),
        'COMP-001': ('The required task result is clear.', 'The required task result is missing or unclear.'),
        'COMP-002': ('Grader/reference evidence is supplied.', 'Required grader or reference evidence is incomplete.'),
        'COMP-003': ('Special-case behavior is specified or not applicable.', 'Behavior for empty or invalid inputs needs clarification.'),
        'USE-001': ('The task matches the required capability.', 'The required task capability is not established.'),
        'SCOPE-001': ('The task stays within scope.', 'The task may include excluded activities.'),
        'CAT-001': ('The task has a permitted category.', 'The task category is unclear.'),
        'EXECUTION-ID': ('Execution IDs are consistent.', 'An execution ID is reused for different task/answer pairs.'),
        'TASK-ID-CONSISTENCY': ('Task IDs are consistent.', 'A task ID has different task contexts or unclear versions.'),
        'EXTRACTION': ('Task records were identified.', 'The task and attempt grouping needs review.'),
        'ASSESSMENT-COMPLETE': ('Assessment completed.', 'Assessment did not complete for this file.'),
        'REWARD-AGREEMENT': ('The submitted reward matches the reviewed reward.', 'The submitted reward disagrees with the reviewed reward.'),
        'REWARD-CONSISTENCY': ('Submitted rewards are consistent.', 'The same execution has conflicting submitted rewards.'),
        'SEMANTIC-PENDING': ('Coverage reviewed.', 'Task coverage has not been reviewed yet.'),
        'READINESS': ('Coverage reviewed.', 'This older run has no coverage review.'),
        'INSTRUCTIONS': ('Rubric instructions are clear.', 'Rubric instructions need clarification.'),
        'OTHER-001': ('Other rubric conditions are met.', 'An additional rubric condition needs review.'),
    }
    if code in messages:
        return messages[code][0 if passed else 1]
    if code.startswith(('COMP-EXTRA', 'SCORE-EXTRA')):
        kind = 'completion' if code.startswith('COMP-EXTRA') else 'scoring'
        number = int(code.rsplit('-', 1)[-1]) if code.rsplit('-', 1)[-1].isdigit() else ''
        return f'Additional {kind} rule {number} is met.' if passed else f'Additional {kind} rule {number} needs review.'
    return 'Rubric wording needs clarification.' if check['phase'] == 'Instructions' else 'A dataset requirement needs review.'


def next_step(check):
    if check['status'] == 'pass':
        return ''
    code = check['requirement_id']
    simple = {
        'MISSING-DATA': 'Recover missing source data; preserve genuine empty responses.',
        'FILE-READABLE': 'Restore or re-upload the file; split oversized task files.',
        'JSON-VALID': 'Correct the JSON in the affected records.',
        'DUPLICATE-ROW': 'Confirm separate executions or remove accidental duplicates.',
        'COMP-001': 'Supply the original task definition or expected result.',
        'COMP-002': 'Supply the required grader and reference or reproducible method.',
        'COMP-003': 'Define the expected response to empty or invalid inputs.',
        'ATTEMPTS-PER-TASK': 'Supply missing executions and their original IDs.',
        'CAT-001': 'Clarify the task category.',
        'EXTRACTION': 'Clarify the task, answer and execution identifiers.',
        'USE-001': 'Supply a task matching the rubric capability.',
        'SCOPE-001': 'Review the excluded activity against the rubric.',
        'REWARD-AGREEMENT': 'Review the reward against its completion evidence.',
        'REWARD-CONSISTENCY': 'Resolve the conflicting submitted rewards.',
        'SEMANTIC-PENDING': 'Run the coverage review.',
        'READINESS': 'Run a new assessment to include coverage checks.',
    }
    if code.startswith('QTY-'):
        return 'Supply missing tasks or resolve unclear task categories.'
    return simple.get(code, 'Review this rubric rule against the affected source records.')


def summary_rows(checks, section, include_pass=False):
    grouped = {}
    severity = {'pass': 0, 'unverified': 1, 'fail': 2}
    for check in checks:
        if section_for(check) != section or (not include_pass and check['status'] == 'pass'):
            continue
        text = finding_text(check)
        key = (check['requirement_id'], text)
        group = grouped.setdefault(key, dict(status=check['status'], finding=text, next_step=next_step(check), records=[]))
        if severity[check['status']] > severity[group['status']]:
            group.update(status=check['status'], next_step=next_step(check))
        record = dict(file=check.get('source_file') or 'Dataset', rows=check.get('source_rows', []),
            status=check['status'], finding=text, change=check.get('reassessment_status', ''))
        if record not in group['records']:
            group['records'].append(record)
    results = []
    for group in grouped.values():
        files = {r['file'] for r in group['records'] if r['file'] != 'Dataset'}
        affected = next(iter(files)) if len(files) == 1 else f'{len(files)} files' if files else 'Dataset'
        results.append({**group, 'affected': affected})
    return sorted(results, key=lambda g: (-severity[g['status']], g['finding']))
