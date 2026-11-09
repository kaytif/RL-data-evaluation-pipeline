"""Identify nested response attempts and preserve the meaning of supplied gold labels."""
import hashlib
import json
import time


def task_key(row):
    state = json.loads(row['state_json'])
    conversation = state.get('conversation')
    if not isinstance(conversation, list) or not conversation:
        raise ValueError('Missing conversation: cannot identify the task.')
    return hashlib.sha256(json.dumps(conversation, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def assess_hf(task, blueprint, reader, start):
    from .assessment_agent import AgentFileOutput, AttemptAssessment, Evidence
    groups = {}
    notes = ['Scores reproduce supplied gold assessments of candidate responses; they are not independent semantic judgments. Jev numeric predictions are not used as candidate answers or thresholded.',
             'Only recorded evaluation principles are covered; reference quality and task completeness require review.']
    for number, row in enumerate(reader, 2):
        if number > 200001 or time.monotonic() - start > 30:
            raise ValueError('File exceeded the agent row or time limit.')
        if None in row or any(v is None for v in row.values()):
            raise ValueError(f'Inconsistent CSV columns at row {number}.')
        state = json.loads(row['state_json'])
        key = task_key(row)
        candidate = state.get('candidate_response')
        principle = state.get('evaluation_principle')
        if not isinstance(candidate, str) or not candidate.strip() or not isinstance(principle, str) or not principle.strip():
            raise ValueError(f'Missing candidate response or evaluation principle at row {number}.')
        aid = hashlib.sha256((key + ':' + candidate).encode()).hexdigest()[:24]
        group = groups.setdefault(aid, dict(conversation=state['conversation'], candidate=candidate, evidence=[], rows=[], principles=set()))
        label = row['gold_label'].strip().lower()
        score = {'true': 1, 'false': 0}.get(label)
        reason = 'Supplied gold_label records whether this candidate fulfils the principle; no independent regrading was performed.'
        if row.get('gold_score', '').strip() and (score is None or float(row['gold_score']) != score):
            score = None
            reason = 'gold_label and gold_score conflict or are invalid.'
        if principle in group['principles']:
            score = None
            reason = 'Repeated assessment of the same principle requires review.'
        group['principles'].add(principle)
        group['rows'].append(row['row_id'])
        group['evidence'].append(Evidence(requirement_id='SOURCE-GOLD', source_rows=[number],
            check=principle, expected='true (candidate fulfils the supplied principle)', actual=label,
            status='pass' if score == 1 else 'fail' if score == 0 else 'unverified', reason=reason))
    if not groups:
        raise ValueError('No Hugging Face assessment records found.')
    attempts = []
    for aid, group in groups.items():
        evidence = group['evidence']
        if blueprint.get('completion_requirements') or blueprint.get('scoring_requirements'):
            evidence.append(Evidence(requirement_id='CLIENT-EXTRA', source_rows=[], check='additional requirements',
                expected=None, actual=None, status='unverified', reason='Additional client requirements are not established by source labels.'))
        failed = any(e.status == 'fail' for e in evidence)
        passed = all(e.status == 'pass' for e in evidence)
        score = 0 if failed else 1 if passed else None
        attempts.append(AttemptAssessment(task_id=task.id, attempt_id='ATT-' + aid,
            source_task=group['conversation'], candidate_response=group['candidate'],
            source_attempt_id=';'.join(group['rows']), source_file=task.filename,
            score=score, status='failure' if failed else 'success' if passed else 'unverified',
            reasoning='Source-provided assessment: ' + ('at least one recorded principle failed.' if failed else 'all recorded principles passed.' if passed else 'evidence requires review.'),
            evidence=evidence, task_review_notes=notes))
    return AgentFileOutput(attempts=attempts, mapping=dict(
        task='state_json.conversation', attempt='state_json.candidate_response',
        criterion='state_json.evaluation_principle', score='gold_label, cross-checked with gold_score',
        scoring_mode='source_provided_labels', grouping='conversation + candidate response'), task_review_notes=notes)
