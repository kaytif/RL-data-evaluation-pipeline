import csv
import io
import json
from datetime import datetime, timezone
from sqlalchemy import select
from .assessment_agent import AssessmentJob, AssessedAttempt, AssessmentToolCall


def export_scored_source_csv(session, job_id, file_id):
    """Append assessment columns to the selected original CSV, joined by cited rows."""
    import hashlib
    from pathlib import Path
    from .csv_uploads import UploadedTaskCSV
    job = session.get(AssessmentJob, job_id)
    if job is None or job.approval is None:
        raise ValueError('Human approval is required before exporting the dataset.')
    file = session.get(UploadedTaskCSV, file_id)
    if file is None or file.batch_id != job.batch_id:
        raise ValueError('Choose a file from this assessment upload.')
    if job.summary.get('source_file_id') not in (None, file_id):
        raise ValueError('This assessment belongs to a different file.')
    try:
        raw = Path(file.path).read_bytes()
    except OSError:
        raise ValueError('The original CSV is unavailable. Re-upload it to download an enriched copy.') from None
    if hashlib.sha256(raw).hexdigest() != file.sha256:
        raise ValueError('Original CSV checksum changed since upload.')
    reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig')), strict=True)
    headers = list(reader.fieldnames or [])
    extra = []
    for label in ('assessment_reward', 'assessment_reasoning', 'assessment_attempt_id', 'assessment_source_row'):
        name = label
        while name in headers + extra:
            name = 'new_' + name
        extra.append(name)
    by_row = {}
    results = session.scalars(select(AssessedAttempt).where(AssessedAttempt.job_id == job_id,
        AssessedAttempt.task_id == file_id).order_by(AssessedAttempt.id))
    for saved in results:
        item = saved.result
        cited = {n for evidence in item.get('evidence', []) for n in evidence.get('source_rows', [])}
        for n in cited:
            by_row.setdefault(n, []).append(item)
    out = io.StringIO(newline='')
    writer = csv.DictWriter(out, headers + extra)
    writer.writeheader()
    for n, source in enumerate(reader, 2):
        # Multiple attempts embedded in a source row receive separate output rows.
        for item in by_row.get(n) or [None]:
            values = [item.get('score'), item.get('reasoning', ''), item.get('attempt_id', ''), n] if item else [
                None, 'No supported assessment is mapped to this source row.', '', n]
            writer.writerow({**source, **dict(zip(extra, values))})
    return out.getvalue().encode('utf-8-sig')

def approve_dataset(session, job_id, actor, rationale):
    job = session.get(AssessmentJob, job_id)
    if job is None:
        raise ValueError('Unknown assessment.')
    if not actor.strip() or not rationale.strip():
        raise ValueError('Reviewer name and rationale are required.')
    if job.approval is None:
        job.approval = {'actor': actor.strip(), 'rationale': rationale.strip(),
            'timestamp': datetime.now(timezone.utc).isoformat(), 'job_id': job.id,
            'batch_id': job.batch_id, 'blueprint_task_id': job.blueprint['task_id']}

def export_dataset(session, job_id, format='csv'):
    job = session.get(AssessmentJob, job_id)
    if job is None or job.approval is None:
        raise ValueError('Human approval is required before exporting the dataset.')
    rows = list(session.scalars(select(AssessedAttempt).where(AssessedAttempt.job_id == job.id)
                               .order_by(AssessedAttempt.task_id, AssessedAttempt.id)))
    metadata = {'assessment_id': job.id, 'batch_id': job.batch_id, 'adapter': job.adapter,
                'approval': job.approval, 'requirements_snapshot': job.blueprint}
    if format == 'jsonl':
        return '\n'.join(json.dumps({**row.result, **metadata}) for row in rows).encode()
    out = io.StringIO(newline='')
    fields = ['assessment_id', 'batch_id', 'task_id', 'attempt_id', 'source_attempt_id', 'source_file',
              'score', 'status', 'reasoning', 'evidence', 'task_review_notes', 'source_task', 'candidate_response',
              'source_task_key', 'category', 'execution_id', 'grouping_basis', 'reported_reward', 'reported_reward_conflict', 'check_fixes', 'adapter', 'approved_by']
    writer = csv.DictWriter(out, fields)
    writer.writeheader()
    for row in rows:
        result = {**row.result, 'assessment_id': job.id, 'batch_id': job.batch_id,
                  'adapter': job.adapter, 'approved_by': job.approval['actor']}
        result['evidence'] = json.dumps(result['evidence'])
        result['source_task'] = json.dumps(result.get('source_task'))
        result['task_review_notes'] = json.dumps(result.get('task_review_notes', []))
        result['check_fixes'] = json.dumps(result.get('check_fixes', {}))
        writer.writerow(result)
    return out.getvalue().encode('utf-8-sig')
