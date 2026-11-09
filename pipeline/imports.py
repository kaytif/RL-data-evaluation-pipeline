"""Bounded data parsing and checks. Uploaded code is never executed."""
import csv
import hashlib
import io
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import insert, select
from .assessment_spec import assessment_spec, TEMPLATE_FIELDS
from .models import ClientTaskRevision
from .upload_models import DataSubmission, ImportedRecord
from .intake import new_id

MAX_BYTES = 50 * 1024 * 1024
MAX_ROWS = 200000
csv.field_size_limit(MAX_BYTES)
ROOT = Path(__file__).resolve().parents[1] / 'artifacts' / 'submissions'

class ImportProblem(ValueError):
    pass

def parse(files):
    """files: [(role, display_name, bytes)]. Row numbers are 1-based data records."""
    if not files or sum(len(data) for _, _, data in files) > MAX_BYTES:
        raise ImportProblem('Select files totaling no more than 50 MiB.')
    records = {role: [] for role in TEMPLATE_FIELDS}
    for file_index, (role, name, data) in enumerate(files):
        if role not in records:
            raise ImportProblem('Unknown record type.')
        try:
            text = data.decode('utf-8-sig')
            extension = Path(name).suffix.lower()
            if extension == '.csv':
                reader = csv.DictReader(io.StringIO(text, newline=''), strict=True)
                fields = reader.fieldnames or []
                if len(fields) != len(set(fields)):
                    raise ImportProblem(f'{name}: duplicate CSV column names.')
                missing = set(TEMPLATE_FIELDS[role]) - set(fields)
                if missing:
                    raise ImportProblem(f'{name}: missing columns: {", ".join(sorted(missing))}. Download the template.')
                rows = reader
            elif extension == '.jsonl':
                rows = (json.loads(line) for line in io.StringIO(text) if line.strip())
            elif extension == '.json':
                rows = json.loads(text)
                if not isinstance(rows, list):
                    raise ImportProblem(f'{name}: JSON must contain an array of record objects. Use JSONL for large traces.')
            else:
                raise ImportProblem('Supported files: .csv, .json, .jsonl (UTF-8).')
            for number, row in enumerate(rows, 1):
                if not isinstance(row, dict) or None in row:
                    raise ImportProblem(f'{name}, record {number}: expected an object with matching column values.')
                missing = set(TEMPLATE_FIELDS[role]) - row.keys()
                if missing:
                    raise ImportProblem(f'{name}, record {number}: missing fields: {", ".join(sorted(missing))}.')
                # Reject nonstandard JSON NaN and Infinity; DB JSON must be portable.
                json.dumps(row, allow_nan=False)
                records[role].append({'source_file': name, 'source_row': number, 'file_index': file_index, 'payload': row})
                if sum(map(len, records.values())) > MAX_ROWS:
                    raise ImportProblem('Submission exceeds the 200,000-record limit. Split it into smaller submissions.')
        except (UnicodeError, json.JSONDecodeError, csv.Error, ValueError, RecursionError) as exc:
            if isinstance(exc, ImportProblem):
                raise
            raise ImportProblem(f'{name}: could not parse the file: {str(exc)[:250]}') from exc
    if any(not values for values in records.values()):
        raise ImportProblem('Supply at least one task, attempt, and check record. Header-only or empty submissions cannot be imported.')
    return records

def text(value):
    return value.strip() if isinstance(value, str) else ''

def json_list(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return None
    return value if isinstance(value, list) and value else None

def assess(snapshot, records):
    spec = assessment_spec(snapshot)
    results = {}
    def add(code, status, expected, actual, row=None):
        key = (code, status)
        item = results.setdefault(key, {'requirement_id': code, 'status': status, 'expected': expected,
                                        'actual': actual, 'count': 0, 'examples': []})
        item['count'] += 1
        if row and len(item['examples']) < 20:
            item['examples'].append({'file': row['source_file'], 'record': row['source_row'],
                                      'detail': actual})
    tasks, attempts = {}, {}
    counts, per_task = Counter(), Counter()
    categories = {r['category']: r for r in snapshot['quantities']}
    for row in records['tasks']:
        p = row['payload']; tid = text(p['task_id'])
        if not tid or tid in tasks:
            add('ID-001', 'failed', 'Unique nonempty task IDs', f'Empty or duplicate task ID: {tid}', row); continue
        tasks[tid] = row
        category = text(p['category'])
        if category not in categories:
            add('CAT-001', 'failed', 'One permitted category per task', f'Unknown category: {category}', row)
        else:
            counts[category] += 1
        if not text(p['task_version']):
            add('ID-001', 'failed', 'Nonempty task version', 'Task version missing or not a string', row)
        if not text(p['instructions']) or not text(p['expected_result']):
            add('COMP-001', 'failed', 'Instructions and required result', 'Instructions or expected result missing', row)
        checks = json_list(p['completion_checks'])
        if not text(p['grader_reference']) or not checks or any(not text(v) for v in checks or []) or (checks and len(set(map(str, checks))) != len(checks)):
            add('COMP-002', 'failed', 'Grader reference and unique completion check names', 'Missing grader reference or invalid completion_checks JSON array', row)
        if not text(p['special_case_behavior']):
            add('COMP-003', 'failed', 'Special-case behavior or explicit not-applicable explanation', 'Special-case behavior missing', row)
    for row in records['attempts']:
        p = row['payload']; aid = text(p['attempt_id'])
        if not aid or aid in attempts:
            add('ID-002', 'failed', 'Unique nonempty attempt IDs', f'Empty or duplicate attempt ID: {aid}', row); continue
        attempts[aid] = row
        tid = text(p['task_id'])
        if tid not in tasks or p['task_version'] != tasks[tid]['payload']['task_version']:
            add('LINK-001', 'failed', 'Attempt links to a supplied task and exact version', f'Unknown task or version for attempt {aid}', row)
        else:
            per_task[tid] += 1
        trace = json_list(p['trace'])
        if not text(p['model']) or not trace or any(not isinstance(step, dict) for step in trace or []):
            add('TRACE-001', 'failed', 'Model and nonempty trace array of objects', f'Missing model or invalid trace for {aid}', row)
    by_attempt = defaultdict(dict)
    for row in records['checks']:
        p = row['payload']; aid = text(p['attempt_id']); name = text(p['check_name'])
        if aid not in attempts:
            add('LINK-001', 'failed', 'Check links to a supplied attempt', f'Unknown attempt: {aid}', row)
        if not name or name in by_attempt[aid]:
            add('SCORE-002', 'failed', 'Unique named check per attempt', f'Empty or duplicate check name for {aid}', row)
        by_attempt[aid][name] = p
        if p['status'] not in ('pass', 'fail', 'unverified') or (p['status'] == 'unverified' and not text(p['unverified_reason'])) or p['expected_result'] is None or p['actual_result'] is None:
            add('SCORE-002', 'failed', 'Expected/actual results, pass/fail/unverified status, and reason if unverified', f'Incomplete check evidence for {aid}: {name}', row)
    for aid, row in attempts.items():
        p = row['payload']; tid = text(p['task_id'])
        declared = json_list(tasks[tid]['payload']['completion_checks']) if tid in tasks else None
        evidence = by_attempt[aid]
        if not evidence:
            add('SCORE-002', 'failed', 'Check evidence for every attempt', f'No checks supplied for {aid}', row)
        if declared:
            missing = set(map(str, declared)) - evidence.keys()
            extra = evidence.keys() - set(map(str, declared))
            if missing or extra:
                add('SCORE-002', 'failed', 'Evidence matches declared completion checks', f'{aid}: missing checks {sorted(missing)}, undeclared checks {sorted(extra)}', row)
        statuses = [c['status'] for c in evidence.values()]
        expected = '0' if 'fail' in statuses else 'unverified' if not declared or not evidence or any(s != 'pass' for s in statuses) or set(map(str, declared or [])) - evidence.keys() else '1'
        reward = str(int(p['reward'])) if type(p['reward']) in (int, float) and p['reward'] in (0, 1) else str(p['reward']).strip()
        if snapshot.get('scoring_requirements'):
            add('SCORE-001', 'needs_review', 'Human interpretation of requested scoring changes', 'Default reward consistency is not asserted when scoring changes are present')
        elif reward not in ('0', '1', 'unverified') or reward != expected:
            add('SCORE-001', 'failed', 'Reward consistent with all reported applicable checks', f'{aid}: expected {expected}, reported {reward}', row)
    for index, (category, rule) in enumerate(categories.items(), 1):
        code = f'QTY-{index:03}'
        deficient = [tid for tid, row in tasks.items() if text(row['payload']['category']) == category and per_task[tid] < rule['attempts_per_task']]
        status = 'failed' if counts[category] < rule['minimum_tasks'] or deficient else 'passed'
        add(code, status, rule, {'distinct_tasks': counts[category], 'tasks_below_attempt_minimum': len(deficient), 'example_task_ids': deficient[:20]})
    for rule in spec['requirements']:
        code = rule['requirement_id']
        if rule['assessment'] in ('human_review', 'presence_check_and_human_review'):
            add(code, 'needs_review', rule['rule'], 'Content correctness and prose requirements require human review; uploaded graders are not executed.')
        elif not any(key[0] == code for key in results):
            add(code, 'passed', rule['rule'], 'No inconsistency detected in reported records')
    failed = sum(r['count'] for r in results.values() if r['status'] == 'failed')
    return {'status': 'failed' if failed else 'needs_review', 'failure_count': failed,
            'counts': {role: len(rows) for role, rows in records.items()}, 'results': list(results.values()),
            'limitations': 'Structural checks and reported-evidence consistency only. No grader execution, capability judgment, or release approval.'}

def save_import(session, revision_id, files, artifact_root=None):
    revision = session.get(ClientTaskRevision, revision_id)
    if revision is None or revision.snapshot.get('rubric_version') != 2:
        raise ImportProblem('Select a saved revision using the post-training rubric.')
    fingerprint = hashlib.sha256((str(revision_id) + json.dumps(sorted(
        (role, name, hashlib.sha256(data).hexdigest()) for role, name, data in files))).encode()).hexdigest()
    existing = session.scalar(select(DataSubmission).where(DataSubmission.fingerprint == fingerprint))
    if existing:
        return existing.id
    records = parse(files)
    report = assess(revision.snapshot, records)
    sid = new_id('SUB')
    root = Path(artifact_root) if artifact_root else ROOT
    directory = root / sid
    directory.mkdir(parents=True, exist_ok=False)
    metadata = []
    try:
        for index, (role, name, data) in enumerate(files):
            # Uploaded names are display labels only, never filesystem paths.
            target = directory / f'{index:04}-{role}.bin'
            target.write_bytes(data)
            metadata.append({'name': name, 'role': role, 'path': str(target), 'bytes': len(data),
                             'sha256': hashlib.sha256(data).hexdigest()})
        session.add(DataSubmission(id=sid, requirement_revision_id=revision.id, fingerprint=fingerprint,
            created_at=datetime.now(timezone.utc).isoformat(), snapshot=revision.snapshot, report=report, files=metadata))
        session.flush()
        batch = []
        for role, rows in records.items():
            for row in rows:
                batch.append(dict(submission_id=sid, role=role, source_file=row['source_file'], source_row=row['source_row'], payload=row['payload']))
                if len(batch) == 1000:
                    session.execute(insert(ImportedRecord), batch); batch = []
        if batch:
            session.execute(insert(ImportedRecord), batch)
    except BaseException:
        for path in directory.iterdir():
            path.unlink()
        directory.rmdir()
        raise
    return sid
