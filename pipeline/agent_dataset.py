import csv
import io
import json
from datetime import datetime, timezone
from sqlalchemy import select
from .assessment_agent import AssessmentJob, AssessedAttempt, AssessmentToolCall

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
