"""Human-confirmed reference cases evaluated through the same extraction/grading adapter."""
import csv
import hashlib
import io
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from .assessment_agent import AssessmentJob
from .evaluation_reports import load_batch
from .intake import new_id


class CalibrationCase(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(min_length=1)
    task: str = Field(min_length=1)
    candidate: str = Field(min_length=1)
    reference_answer: str | None
    criteria: list[str] = Field(min_length=1)
    expected_reward: Literal[0, 1] | None
    reviewer_reason: str = Field(min_length=1)


def starter_cases():
    """Proposed labels: the UI requires human review and confirmation before use."""
    cases = [
        ('correct', 'Name the instrument used to measure temperature. Answer with the word only.', 'thermometer', 'thermometer', 1, 'The candidate names the correct instrument.'),
        ('incorrect', 'Name the instrument used to measure temperature. Answer with the word only.', 'telescope', 'thermometer', 0, 'A telescope is not used to measure temperature.'),
        ('synonym', 'Give a word meaning joyful.', 'happy', None, 1, 'Happy is a valid synonym of joyful.'),
        ('format', 'Name the instrument used to measure temperature. Answer with the word only.', 'The answer is thermometer.', 'thermometer', 0, 'The answer violates the explicit word-only format.'),
        ('math', 'What is 12 plus 7? Answer with the number only.', '19', '19', 1, 'The arithmetic and format are correct.'),
        ('wrong-math', 'What is 12 plus 7? Answer with the number only.', '20', '19', 0, 'The arithmetic is incorrect.'),
        ('missing-evidence', 'Report the exact contents of result.txt after the run.', 'The run finished.', None, None, 'No file content or execution evidence establishes the requested result.'),
    ]
    return [dict(id=id, task=task, candidate=candidate, reference_answer=reference,
                 criteria=['Satisfy the task and its explicit answer format using the supplied evidence.'],
                 expected_reward=reward, reviewer_reason=reason)
            for id, task, candidate, reference, reward, reason in cases]


def validate_cases(raw):
    if not isinstance(raw, list) or not 2 <= len(raw) <= 30:
        raise ValueError('Provide between 2 and 30 reviewed calibration cases in a JSON array.')
    if any(not isinstance(c, dict) or type(c.get('expected_reward')) not in (int, type(None)) for c in raw):
        raise ValueError('Reference rewards must be integer 0 or 1, or null for unverified.')
    cases = [CalibrationCase.model_validate(c) for c in raw]
    if len({c.id for c in cases}) != len(cases):
        raise ValueError('Calibration case IDs must be unique.')
    if not {0, 1} <= {c.expected_reward for c in cases}:
        raise ValueError('Include at least one reviewed reward-1 case and one reward-0 case.')
    if len(json.dumps(raw).encode()) > 64000:
        raise ValueError('Calibration set exceeds the 64 KB limit.')
    return cases


def run_calibration(session, batch_id, judge, raw_cases, reviewer, confirmed=False, progress=None):
    if not confirmed or not reviewer.strip():
        raise ValueError('Review the reference labels, enter a reviewer name, and confirm before calibration.')
    cases = validate_cases(raw_cases)
    _, blueprint, _ = load_batch(session, batch_id)
    fingerprint = hashlib.sha256(json.dumps(['calibration-v1', batch_id, judge.name, raw_cases,
        blueprint, reviewer.strip()], sort_keys=True).encode()).hexdigest()
    old = session.scalar(select(AssessmentJob).where(AssessmentJob.fingerprint == fingerprint))
    if old:
        return old.id
    results = []
    with tempfile.TemporaryDirectory(prefix='rl-calibration-') as temp:
        for i, case in enumerate(cases):
            # Neither the reference reward nor its human explanation is sent to the model.
            data = dict(task=case.task, candidate_response=case.candidate,
                        expected_answer=case.reference_answer or '', criterion='; '.join(case.criteria),
                        attempt_id=f'cal-{i:03}')
            output = io.StringIO(newline='')
            writer = csv.DictWriter(output, fieldnames=list(data))
            writer.writeheader(); writer.writerow(data)
            raw = output.getvalue().encode()
            path = Path(temp) / f'{i}.csv'; path.write_bytes(raw)
            task = SimpleNamespace(id=f'CAL-{i:03}', filename=f'case-{i:03}.csv', path=str(path),
                                   sha256=hashlib.sha256(raw).hexdigest())
            calibration_blueprint = {**blueprint,
                'required_capabilities': 'Identify the supplied task and candidate response. Apply the case criteria. Expected_answer, when populated, is reference content; an empty reference is absent.',
                'completion_requirements': [], 'scoring_requirements': [], 'comments': '',
                'quantities': [], 'outside_scope': 'External browsing or execution of uploaded code.',
                'completion_defaults': []}
            assessed = judge.assess_file(task, calibration_blueprint)
            if len(assessed.attempts) != 1:
                raise ValueError(f'Calibration case {case.id} did not produce exactly one attempt.')
            result = assessed.attempts[0]
            predicted = result.score
            outcome = ('agreement' if predicted == case.expected_reward else
                       'false_pass' if predicted == 1 and case.expected_reward == 0 else
                       'false_failure' if predicted == 0 and case.expected_reward == 1 else 'unresolved_or_disagreement')
            results.append(dict(case_id=case.id, expected_reward=case.expected_reward,
                actual_reward=predicted, outcome=outcome, reasoning=result.reasoning,
                human_reason=case.reviewer_reason))
            if progress:
                progress(i + 1, len(cases))
    total = len(results)
    binary = [r for r in results if r['expected_reward'] is not None]
    report = dict(kind='calibration', reviewed_by=reviewer.strip(), cases=results,
        reference_cases=[c.model_dump() for c in cases],
        agreement=sum(r['outcome'] == 'agreement' for r in results) / total,
        binary_accuracy=sum(r['expected_reward'] == r['actual_reward'] for r in binary) / len(binary),
        false_passes=sum(r['outcome'] == 'false_pass' for r in results),
        false_failures=sum(r['outcome'] == 'false_failure' for r in results),
        unresolved=sum(r['outcome'] == 'unresolved_or_disagreement' for r in results),
        limitations='Measures agreement only on these human-confirmed reference cases, using the same extraction and per-check grading pipeline. It is not general grader accuracy or evidence of training improvement. Case-specific criteria are used; dataset-specific additions are not imposed on unrelated calibration tasks.')
    job = AssessmentJob(id=new_id('ASSESS'), batch_id=batch_id, fingerprint=fingerprint,
        created_at=datetime.now(timezone.utc).isoformat(), adapter='calibration:' + judge.name,
        blueprint=blueprint, summary=dict(kind='calibration', report=report), approval=None)
    session.add(job); session.flush()
    return job.id
