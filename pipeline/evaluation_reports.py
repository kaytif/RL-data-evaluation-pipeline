"""Readiness, compliance, quality, reward and remediation reports using existing job JSON."""
import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import select
from .assessment_agent import AssessmentJob, AssessedAttempt
from .csv_uploads import TaskUploadBatch, UploadedTaskCSV
from .models import ClientTaskRevision
from .evaluation_types import CheckSet, requirements, package_requirements, validate_checks
from .intake import new_id

REPORT_VERSION = 3


def check(code, phase, status, expected, observed, issue, fix='', *, filename='', rows=(), task='', attempt='', scope='dataset'):
    locator = [phase, code, filename, [] if phase == 'Data quality' else list(rows), task, attempt]
    return dict(key=hashlib.sha256(json.dumps(locator, sort_keys=True).encode()).hexdigest()[:24],
        requirement_id=code, phase=phase, scope=scope, status=status, source_file=filename,
        source_rows=list(rows), task=task, attempt=attempt, expected=str(expected), observed=str(observed),
        issue=issue, suggested_fix=fix)


def overall(checks):
    relevant = [c for c in checks if c.get('scope', 'dataset') == 'dataset']
    if any(c['status'] == 'fail' for c in relevant):
        return 'fail'
    if not relevant or any(c['status'] == 'unverified' for c in relevant):
        return 'unverified'
    return 'pass'


def make_report(kind, checks, **details):
    return dict(version=REPORT_VERSION, kind=kind, created_at=datetime.now(timezone.utc).isoformat(),
        overall=overall(checks), checks=checks, issues=[c for c in checks if c['status'] != 'pass'],
        counts=dict(Counter(c['status'] for c in checks)), **details)


def load_batch(session, batch_id):
    batch = session.get(TaskUploadBatch, batch_id)
    if batch is None:
        raise ValueError('Choose an uploaded dataset.')
    revision = session.get(ClientTaskRevision, batch.requirement_revision_id)
    files = list(session.scalars(select(UploadedTaskCSV).where(UploadedTaskCSV.batch_id == batch_id).order_by(UploadedTaskCSV.id)))
    return batch, revision.snapshot, files


def quality_checks(files):
    checks = []
    summary = dict(files=len(files), data_rows=0, empty_cells=0, exact_duplicate_rows=0, files_with_errors=0)
    critical = {'prompt', 'instructions', 'definition', 'task', 'candidate_response', 'actual_answer',
                'actual_output', 'actual_result', 'answer', 'response', 'output', 'state_json'}
    for file in files:
        try:
            path = Path(file.path)
            if file.byte_count > 240000:
                raise ValueError('File exceeds the current 240 KB LLM assessment limit.')
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != file.sha256:
                raise ValueError('Original file checksum differs from the uploaded record.')
            import io
            reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig')), strict=True)
            headers = reader.fieldnames or []
            if not headers or any(not h.strip() for h in headers) or len(headers) != len(set(headers)):
                raise ValueError('A nonempty unique header is required for every column.')
            seen, duplicates, empty, bad_json, ids = {}, [], [], [], defaultdict(list)
            missing_fields = {}
            nrows = 0
            for n, row in enumerate(reader, 2):
                nrows += 1
                if nrows > 500:
                    raise ValueError('File exceeds the current 500 data-row LLM assessment limit.')
                if None in row or any(v is None for v in row.values()):
                    raise ValueError(f'Inconsistent column count at CSV row {n}.')
                summary['empty_cells'] += sum(not v.strip() for v in row.values())
                if any(k.strip().lower() in critical and not v.strip() for k, v in row.items()):
                    empty.append(n)
                    missing_fields[str(n)] = [k for k,v in row.items() if k.strip().lower() in critical and not v.strip()]
                for k, v in row.items():
                    if k.lower().endswith('_json') and v.strip():
                        try:
                            json.loads(v)
                        except ValueError:
                            bad_json.append(n)
                digest = hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
                if digest in seen:
                    duplicates.append(n)
                seen[digest] = n
                for k in ('attempt_id', 'run_id', 'execution_id', 'trial_id'):
                    if row.get(k, '').strip():
                        ids[row[k]].append(n)
                for k in ('reward', 'reported_reward'):
                    if row.get(k, '').strip() and row[k].strip() not in ('0', '1'):
                        checks.append(check('REWARD-FORMAT', 'Data quality', 'fail', 'A submitted reward of 0 or 1',
                            f'{k} contains a nonbinary value', 'Submitted reward is not binary.',
                            'Correct the submitted reward or document the scoring exception in the requirements.', filename=file.filename, rows=[n]))
            summary['data_rows'] += nrows
            summary['exact_duplicate_rows'] += len(duplicates)
            checks.append(check('FILE-READABLE', 'Data quality', 'pass' if nrows else 'fail', 'Readable nonempty CSV within assessment limits',
                f'{nrows} data rows', 'File parsed successfully.' if nrows else 'File contains no attempt data.',
                '' if nrows else 'Supply task instructions and candidate response records.', filename=file.filename))
            for code, affected, expected, issue, fix, status in [
                ('MISSING-DATA', empty, 'Task/candidate evidence fields are populated', 'Some task or response fields are empty.', 'Check the listed rows. Supply missing evidence, or retain a genuinely empty candidate response as a failed attempt rather than inventing content.', 'unverified'),
                ('JSON-VALID', sorted(set(bad_json)), 'Nested JSON fields parse correctly', 'Nested JSON is malformed.', 'Repair the JSON syntax in the affected rows, preserving the original task and response.', 'fail'),
                ('DUPLICATE-ROW', duplicates, 'No unexplained exact duplicate records', 'Identical records appear more than once.', 'Confirm whether these are separate executions. Preserve valid separate attempts and supply their execution IDs; remove accidental duplicate records in a corrected upload.', 'unverified'),
            ]:
                entry = check(code, 'Data quality', status if affected else 'pass', expected,
                    f'{len(affected)} affected rows', issue if affected else 'No issue detected.', fix if affected else '', filename=file.filename, rows=affected)
                if code == 'MISSING-DATA':
                    entry['missing_fields'] = missing_fields
                checks.append(entry)
        except (OSError, ValueError, UnicodeError, csv.Error) as exc:
            summary['files_with_errors'] += 1
            checks.append(check('FILE-READABLE', 'Data quality', 'fail', 'Readable original CSV within assessment limits',
                str(exc), 'The file cannot be fully assessed.', 'Restore the original file or re-upload a corrected CSV. Split files over 240 KB or 500 data rows.', filename=file.filename))
    return checks, summary


def quantity_checks(blueprint, inventory, complete=True):
    checks, tasks = [], defaultdict(list)
    executions = defaultdict(set)
    for record in inventory:
        tasks[record['task_key']].append(record)
        if record['execution_id']:
            executions[record['execution_id']].add((record['task_key'], record['candidate_hash']))
    for id, targets in executions.items():
        if len(targets) > 1:
            checks.append(check('EXECUTION-ID', 'Attempt coverage', 'fail', 'Each execution ID identifies one task and candidate',
                f'Execution ID {id} identifies {len(targets)} different task/response pairs', 'Execution ID is reused inconsistently.',
                'Assign the original unique execution ID to each separate execution; do not generate fictitious attempts.', attempt=id))
    counts = Counter()
    uncertain_categories = False
    for key, records in tasks.items():
        contexts = {r.get('task_hash') for r in records if r.get('task_hash')}
        if len(contexts) > 1:
            checks.append(check('TASK-ID-CONSISTENCY', 'Task coverage', 'unverified', 'One consistent task context or an explicit version mapping per task ID',
                f'{len(contexts)} distinct contexts share this task ID', 'Task ID is reused for different instructions.',
                'Confirm the task IDs and versions; do not merge unrelated task contexts.', task=key))
        categories = {r['category'] for r in records}
        category = next(iter(categories)) if len(categories) == 1 and None not in categories else None
        if category is None:
            uncertain_categories = True
        else:
            counts[category] += 1
        files = sorted({r['source_file'] for r in records})
        checks.append(check('CAT-001', 'Task coverage', 'pass' if category else 'unverified', 'Exactly one permitted category per distinct task',
            category or 'Category is unknown or inconsistent', 'Category identified.' if category else 'Cannot reliably assign this task to a category.',
            '' if category else 'Clarify the category and task instructions in a corrected upload.', filename='; '.join(files), task=key))
        unique_attempts = set()
        inferred = False
        for r in records:
            if r['execution_id']:
                unique_attempts.add(('explicit', r['execution_id']))
            else:
                inferred = True
                unique_attempts.add(('inferred', r['source_file'], tuple(r['source_rows'])))
        policy = next((q for q in blueprint.get('quantities', []) if q['category'] == category), None)
        minimum = policy['attempts_per_task'] if policy else None
        status = 'unverified' if minimum is None else ('fail' if complete else 'unverified') if len(unique_attempts) < minimum else 'unverified' if inferred else 'pass'
        checks.append(check('ATTEMPTS-PER-TASK', 'Attempt coverage', status, minimum if minimum else 'A minimum for the assigned category',
            f'{len(unique_attempts)} identified attempts; execution IDs {"incomplete" if inferred else "present"}',
            'Per-task attempt threshold checked.' if status == 'pass' else 'This task lacks enough identifiable attempts or verified execution IDs.',
            '' if status == 'pass' else 'Supply the missing separate executions and their real IDs. Confirm grouping; identical responses alone do not prove a duplicate.', filename='; '.join(files), task=key))
    for i, q in enumerate(blueprint.get('quantities', []), 1):
        actual = counts[q['category']]
        status = 'pass' if actual >= q['minimum_tasks'] else 'unverified' if uncertain_categories or not complete else 'fail'
        checks.append(check(f'QTY-{i:03}', 'Task coverage', status, f"{q['minimum_tasks']} distinct tasks in {q['category']}",
            f'{actual} identified distinct tasks', 'Distinct-task threshold checked.' if status == 'pass' else 'Required category coverage is not established.',
            '' if status == 'pass' else f"Supply missing distinct {q['category']} tasks or resolve category/grouping ambiguity."))
    return checks


def file_quantity_checks(blueprint, inventory, filename, file_id=None, complete=True, ambiguous_filename=False):
    relevant = [r for r in inventory if r.get('source_file_id') == file_id] if file_id else []
    if not relevant and not ambiguous_filename:
        relevant = [r for r in inventory if r.get('source_file') == filename and (not file_id or not r.get('source_file_id'))]
    if ambiguous_filename and not relevant:
        complete = False
    scoped = []
    for item in quantity_checks(blueprint, relevant, complete=complete):
        entry = check(item['requirement_id'], item['phase'], item['status'], item['expected'], item['observed'],
            item['issue'], item['suggested_fix'], filename=filename, rows=item['source_rows'],
            task=item['task'], attempt=item['attempt'], scope=item['scope'])
        entry.update(source_file_id=file_id, coverage_scope='file')
        scoped.append(entry)
    return scoped


def run_readiness(session, batch_id, judge=None, progress=None, file_id=None):
    batch, blueprint, files = load_batch(session, batch_id)
    if file_id:
        files = [f for f in files if f.id == file_id]
        if not files:
            raise ValueError('The selected coverage file does not belong to this upload.')
    adapter = 'readiness:' + (judge.name if judge else 'structural')
    fingerprint = hashlib.sha256(json.dumps([REPORT_VERSION, batch_id, file_id, adapter, blueprint], sort_keys=True).encode()).hexdigest()
    old = session.scalar(select(AssessmentJob).where(AssessmentJob.fingerprint == fingerprint))
    if old:
        return old.id
    checks, quality = quality_checks(files)
    inventory = []
    inventoried_files = set()
    instruction = None
    if judge:
        instruction = judge.review_instructions(blueprint).model_dump()
        if not instruction['concerns']:
            checks.append(check('INSTRUCTIONS', 'Instructions', 'pass', 'Clear, consistent, assessable requirements',
                instruction['summary'], 'No instruction issue identified by the model.'))
        for concern in instruction['concerns']:
            checks.append(check(concern['requirement_id'], 'Instructions', concern['status'], 'Clear, consistent, assessable instructions',
                concern['issue'], concern['issue'], concern['suggested_fix']))
        for index, file in enumerate(files):
            bad_file = any(c['requirement_id'] == 'FILE-READABLE' and c['status'] == 'fail' and c['source_file'] == file.filename for c in checks)
            if bad_file:
                continue
            try:
                rows, extracted = judge.inspect_file(file, blueprint)
                covered, grouped = set(), defaultdict(list)
                for item in extracted.attempts:
                    if not set(item.source_rows) <= {r['source_row'] for r in rows}:
                        raise ValueError('Extraction cited nonexistent source rows.')
                    covered.update(item.source_rows)
                    category = item.category if item.category in {q['category'] for q in blueprint.get('quantities', [])} else None
                    key = item.source_task_id or hashlib.sha256(' '.join(item.task.split()).encode()).hexdigest()
                    record = dict(task_key=key, category=category, execution_id=item.source_attempt_id,
                        source_file=file.filename, source_file_id=file.id, source_rows=item.source_rows,
                        task_hash=hashlib.sha256(' '.join(item.task.split()).encode()).hexdigest(),
                        candidate_hash=hashlib.sha256(item.candidate.encode()).hexdigest())
                    inventory.append(record)
                    grouped[key].append(item)
                if covered != {r['source_row'] for r in rows}:
                    raise ValueError('Extraction omitted source rows.')
                inventoried_files.add(file.id)
                packages = {}
                for package in extracted.packages:
                    package_key = ' '.join(package.task.split())
                    if package_key in packages:
                        raise ValueError('Duplicate task-package review.')
                    packages[package_key] = package
                for key, items in grouped.items():
                    category = items[0].category if all(i.category == items[0].category for i in items) else None
                    package = packages.get(' '.join(items[0].task.split()))
                    if package is None:
                        raise ValueError('Missing task-package review for an extracted task.')
                    required = package_requirements(blueprint, category)
                    output = CheckSet(checks=package.checks)
                    validate_checks(output, required)
                    if not set(package.source_rows) <= {r['source_row'] for r in rows}:
                        raise ValueError('Package review cited nonexistent rows.')
                    if set(package.source_rows) != {n for item in items for n in item.source_rows}:
                        raise ValueError('Package review does not cover its task source rows.')
                    for c in output.checks:
                        entry = check(c.requirement_id, 'Task completion', c.status if c.applicable else 'pass', c.expected,
                            c.actual if c.applicable else 'Not applicable: ' + c.reason,
                            c.reason, c.suggested_fix if c.applicable else '', filename=file.filename,
                            rows=sorted({n for item in items for n in item.source_rows}), task=key)
                        entry['applicable'] = c.applicable
                        entry['source_file_id'] = file.id
                        checks.append(entry)
            except (ValueError, OSError, UnicodeError, csv.Error) as exc:
                checks.append(check('EXTRACTION', 'Task coverage', 'unverified', 'Every source row maps to a supported task/attempt',
                    str(exc), 'Task grouping or package evidence needs review.', 'Clarify instructions, reference answers and real execution IDs in this file.', filename=file.filename))
            if progress:
                progress(index + 1, len(files))
        for file in files:
            checks += file_quantity_checks(blueprint, inventory, file.filename, file.id, complete=file.id in inventoried_files)
    else:
        checks.append(check('SEMANTIC-PENDING', 'Instructions', 'unverified', 'LLM instruction and task-package review',
            'Not run', 'Only structural checks have run.', 'Connect the evaluator and run readiness review.'))
    report = make_report('readiness', checks, quality=quality, inventory=inventory,
        instruction_review=instruction, llm_reviewed=bool(judge), requirements=blueprint,
        coverage_scope='file', source_file_id=file_id, inventoried_file_ids=sorted(inventoried_files))
    job = AssessmentJob(id=new_id('ASSESS'), batch_id=batch_id, fingerprint=fingerprint,
        created_at=report['created_at'], adapter=adapter, blueprint=blueprint,
        summary=dict(kind='readiness', source_file_id=file_id, report=report), approval=None)
    session.add(job); session.flush()
    return job.id


def build_final_report(session, job, readiness_id=None):
    batch, blueprint, files = load_batch(session, job.batch_id)
    file_id = job.summary.get('source_file_id')
    if file_id:
        files = [file for file in files if file.id == file_id]
    readiness = session.get(AssessmentJob, readiness_id) if readiness_id else None
    if readiness:
        checks = list(readiness.summary['report']['checks'])
        quality = readiness.summary['report']['quality']
    else:
        checks, quality = quality_checks(files)
        checks.append(check('READINESS', 'Instructions', 'unverified', 'Instruction and task-package review',
            'No readiness review linked', 'Earlier assessment lacks the new readiness evaluation.', 'Run the current evaluator to generate a complete report.'))
    attempts = list(session.scalars(select(AssessedAttempt).where(AssessedAttempt.job_id == job.id)))
    for saved in attempts:
        result = saved.result
        locator = dict(filename=result['source_file'], task=result.get('source_task_key') or '',
                       attempt=result.get('execution_id') or result.get('source_attempt_id') or result['attempt_id'])
        if not result['evidence']:
            checks.append(check('ASSESSMENT-COMPLETE', 'Data quality', 'unverified', 'Complete task extraction and assessment evidence',
                result['reasoning'], 'The file did not produce supported completion checks.',
                'Inspect the file error, restore missing originals or supply a corrected upload, then reassess.', **locator))
        for evidence in result['evidence']:
            checks.append(check(evidence['requirement_id'], 'Candidate rewards', evidence['status'], evidence.get('expected'),
                evidence.get('actual'), evidence['reason'], result.get('check_fixes', {}).get(evidence['requirement_id'],
                    'Review the cited task, candidate response and requirement; supply missing evidence or obtain a separate corrected execution.'),
                rows=evidence['source_rows'], scope='attempt', **locator))
        reported = result.get('reported_reward')
        if result.get('reported_reward_conflict'):
            checks.append(check('REWARD-CONSISTENCY', 'Reward evidence', 'unverified', 'One consistent submitted reward per execution',
                'Both 0 and 1 were supplied for this execution', 'Submitted reward labels conflict.',
                'Resolve the conflicting source labels with the vendor; preserve the original evidence.', **locator))
        if reported is not None:
            status = 'unverified' if result['score'] is None else 'pass' if reported == result['score'] else 'fail'
            checks.append(check('REWARD-AGREEMENT', 'Reward evidence', status, f'Independent reward {result["score"]}',
                f'Submitted reward {reported}', 'Submitted reward comparison.',
                '' if status == 'pass' else 'Review this reward disagreement against the completion checks. Do not overwrite the submitted value silently.', **locator))
    calibrations = list(session.scalars(select(AssessmentJob).where(
        AssessmentJob.batch_id == job.batch_id, AssessmentJob.adapter == 'calibration:' + job.adapter,
        AssessmentJob.created_at <= job.created_at).order_by(AssessmentJob.created_at.desc()).limit(1)))
    calibration = calibrations[0].summary['report'] if calibrations else None
    reward_summary = dict(attempts=len(attempts), reward_1=sum(a.result['score'] == 1 for a in attempts),
        reward_0=sum(a.result['score'] == 0 for a in attempts), unverified=sum(a.result['score'] is None for a in attempts))
    verified = reward_summary['reward_1'] + reward_summary['reward_0']
    reward_summary['success_rate_verified'] = reward_summary['reward_1'] / verified if verified else None
    reward_summary['verified_coverage'] = verified / len(attempts) if attempts else 0
    categories = {}
    for saved in attempts:
        result = saved.result
        category = result.get('category') or 'Unclassified'
        counts = categories.setdefault(category, dict(attempts=0, reward_1=0, reward_0=0, unverified=0))
        counts['attempts'] += 1
        counts['reward_1' if result['score'] == 1 else 'reward_0' if result['score'] == 0 else 'unverified'] += 1
    return make_report('final', checks, source_file_id=file_id, rewards=reward_summary, quality=quality, rewards_by_category=categories, calibration=calibration,
        instruction_summary=readiness.summary['report']['instruction_review']['summary'] if readiness and readiness.summary['report'].get('instruction_review') else None,
        ambiguous_sources=any(count > 1 for count in Counter(f.filename for f in files).values()),
        grader_reliability='See calibration results; limited to the reviewed reference set.' if calibration else 'Unverified: no reviewed calibration set run before this assessment.',
        requirements=blueprint, readiness_id=readiness_id,
        limitations='Dataset compliance is separate from candidate rewards. LLM checks are judgments, not executed vendor graders or external fact verification. Inferred execution grouping needs review.')


def compare_reports(current, previous):
    if current.get('ambiguous_sources') or previous.get('ambiguous_sources'):
        raise ValueError('Repeated filenames make source matching ambiguous. Use unique filenames in corrected uploads before comparing remediation.')
    if current.get('requirements') != previous.get('requirements'):
        raise ValueError('Choose a report using the same requirements revision for a meaningful remediation comparison.')
    now = {c['key']: c for c in current['checks']}
    before = {c['key']: c for c in previous['checks']}
    changes = []
    for key in sorted(set(now) | set(before)):
        old, new = before.get(key), now.get(key)
        if old and old['status'] != 'pass':
            state = 'Not reassessed' if new is None else 'Resolved' if new['status'] == 'pass' else 'Still open'
        elif new and new['status'] != 'pass':
            state = 'New'
        else:
            continue
        changes.append({**(new or old), 'reassessment_status': state,
                        'previous_status': old['status'] if old else None, 'current_status': new['status'] if new else None})
    return changes


def report_markdown(report):
    lines = ['# Dataset evaluation report', '', f"Report: {report['kind']}", f"Dataset compliance: {report['overall']}", '']
    if report.get('rewards'):
        lines += ['## Candidate rewards', '', json.dumps(report['rewards'], indent=2), '']
    if report.get('rewards_by_category'):
        lines += ['## Rewards by category', '', json.dumps(report['rewards_by_category'], indent=2), '']
    if report.get('instruction_summary'):
        lines += ['## Instruction review', '', report['instruction_summary'], '']
    if report.get('quality'):
        lines += ['## Data quality', '', json.dumps(report['quality'], indent=2), '']
    lines += ['## Grader reliability', '', report.get('grader_reliability', 'Calibration is separate from readiness.'), '', '## Findings', '']
    if report.get('calibration'):
        calibration = report['calibration']
        lines += [f"Calibration agreement: {calibration['agreement']:.1%}; false passes: {calibration['false_passes']}; false failures: {calibration['false_failures']}.", '', calibration['limitations'], '']
    for c in report['checks']:
        lines += [f"### {c['requirement_id']} - {c['status']} ({c['scope']})", '',
            f"Source: {c['source_file'] or 'Dataset'}; rows: {c['source_rows']}", f"Expected: {c['expected']}",
            f"Observed: {c['observed']}", c['issue'], f"Suggested fix: {c['suggested_fix'] or 'None'}", '']
    return '\n'.join(lines)


def issues_csv(issues):
    import io
    out = io.StringIO(newline='')
    fields = ['requirement_id', 'phase', 'scope', 'status', 'source_file', 'source_rows', 'task', 'attempt',
              'expected', 'observed', 'issue', 'suggested_fix', 'reassessment_status']
    writer = csv.DictWriter(out, fieldnames=fields, extrasaction='ignore')
    writer.writeheader(); writer.writerows(issues)
    return out.getvalue().encode('utf-8-sig')
