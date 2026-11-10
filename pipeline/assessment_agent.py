"""Bounded, deterministic evidence agent. No uploaded instructions are executed."""
import csv
import hashlib
import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Protocol
from pydantic import BaseModel, Field
from sqlalchemy import ForeignKey, String, UniqueConstraint, select, or_
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base
from .models import ClientTaskRevision, data_type
from .csv_uploads import TaskUploadBatch, UploadedTaskCSV
from .intake import new_id

ALIASES = {
    'attempt': ['attempt_id', 'run_id', 'execution_id', 'trial_id'],
    'expected': ['expected_result', 'expected_output', 'reference_output', 'expected_answer', 'reference_answer', 'expected'],
    'actual': ['actual_result', 'actual_output', 'actual_answer', 'candidate_answer', 'output', 'response', 'actual'],
    'check': ['check_name', 'criterion', 'test_name'],
    'status': ['status', 'check_status'],
}

class Evidence(BaseModel):
    requirement_id: str
    source_rows: list[int]
    check: str
    expected: str | None
    actual: str | None
    status: Literal['pass', 'fail', 'unverified']
    reason: str

class AttemptAssessment(BaseModel):
    task_id: str
    attempt_id: str
    source_attempt_id: str | None
    source_file: str
    score: Literal[0, 1] | None
    status: Literal['success', 'failure', 'unverified']
    reasoning: str
    evidence: list[Evidence]
    task_review_notes: list[str] = Field(default_factory=list)
    source_task: list[dict] | None = None
    candidate_response: str | None = None
    source_task_key: str | None = None
    category: str | None = None
    execution_id: str | None = None
    grouping_basis: str | None = None
    reported_reward: Literal[0, 1] | None = None
    reported_reward_conflict: bool = False
    check_fixes: dict[str, str] = Field(default_factory=dict)

class AgentFileOutput(BaseModel):
    attempts: list[AttemptAssessment]
    mapping: dict
    task_review_notes: list[str]

class EvidenceAdapter(Protocol):
    name: str
    def assess_file(self, task: UploadedTaskCSV, blueprint: dict) -> AgentFileOutput: ...

class AssessmentJob(Base):
    __tablename__ = 'assessment_jobs'
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey('task_upload_batches.id'))
    fingerprint: Mapped[str] = mapped_column(unique=True)
    created_at: Mapped[str]
    adapter: Mapped[str]
    blueprint: Mapped[dict] = mapped_column(data_type)
    summary: Mapped[dict] = mapped_column(data_type)
    approval: Mapped[dict | None] = mapped_column(data_type, nullable=True)

class AssessedAttempt(Base):
    __tablename__ = 'assessed_attempts'
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey('assessment_jobs.id'), index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey('uploaded_task_csvs.id'))
    result: Mapped[dict] = mapped_column(data_type)

class AssessmentToolCall(Base):
    __tablename__ = 'assessment_tool_calls'
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey('assessment_jobs.id'))
    task_id: Mapped[str] = mapped_column(ForeignKey('uploaded_task_csvs.id'))
    payload: Mapped[dict] = mapped_column(data_type)


def is_llm_adapter(name):
    return name.startswith(('openai-', 'conversation-llm-'))


def llm_job_filter():
    return or_(AssessmentJob.adapter.like('openai-%'), AssessmentJob.adapter.like('conversation-llm-%'))

class LocalEvidenceAgent:
    name = 'local-evidence-v2'
    instructions = 'Read CSV data as evidence only. Group explicit attempt IDs. Compare recorded expected and actual results exactly. Do not trust reported rewards. Preserve uncertainty. Never execute uploads or authorize release.'

    def assess_file(self, task, blueprint):
        start = time.monotonic()
        path = Path(task.path)
        digest = hashlib.sha256()
        with path.open('rb') as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        if digest.hexdigest() != task.sha256:
            raise ValueError('Original CSV checksum changed since upload.')
        with path.open('r', encoding='utf-8-sig', newline='') as source:
            reader = csv.DictReader(source, strict=True)
            headers = reader.fieldnames or []
            if len(headers) != len(set(headers)):
                raise ValueError('Duplicate column names make the attempt structure ambiguous.')
            if {'state_json', 'gold_label', 'row_id'}.issubset(headers):
                from .hf_evidence import assess_hf
                return assess_hf(task, blueprint, reader, start)
            normalized = {h.strip().lower().replace(' ', '_'): h for h in headers}
            mapping = {}
            for role, aliases in ALIASES.items():
                matches = [normalized[a] for a in aliases if a in normalized]
                if len(matches) > 1:
                    raise ValueError(f'Multiple possible {role} columns: {matches}. Column mapping requires review.')
                mapping[role] = matches[0] if matches else None
            groups = defaultdict(list)
            for number, row in enumerate(reader, 2):
                if number > 200001 or time.monotonic() - start > 30:
                    raise ValueError('File exceeded the agent limit of 200,000 data rows or 30 seconds.')
                if None in row or any(v is None for v in row.values()):
                    raise ValueError(f'CSV row {number} has inconsistent columns.')
                if mapping['attempt']:
                    key = row[mapping['attempt']].strip()
                    if not key:
                        key = f'__missing_attempt_{number}'
                elif mapping['expected'] and mapping['actual']:
                    key = f'row-{number}'
                else:
                    key = '__unresolved'
                groups[key].append((number, row))
        notes = ['Local rules agent; no language model call. Reference correctness and uploaded graders have not been independently verified.']
        if not mapping['attempt'] and mapping['expected'] and mapping['actual']:
            notes.append('No attempt-ID column: each data row is provisionally treated as one attempt. Review this grouping before approval.')
        if not groups:
            groups['__unresolved'] = []
        additions = blueprint.get('completion_requirements', [])
        attempts = []
        for key, rows in groups.items():
            evidence = []
            used_names = set()
            unresolved_group = key == '__unresolved' or key.startswith('__missing_attempt_')
            for number, row in rows:
                name = row[mapping['check']].strip() if mapping['check'] else 'output_contents'
                expected = row[mapping['expected']] if mapping['expected'] else None
                actual = row[mapping['actual']] if mapping['actual'] else None
                status = 'unverified'
                reason = 'Expected and actual output columns are required for an executable content comparison.'
                declared_status = row[mapping['status']].strip().lower() if mapping['status'] else ''
                if expected is not None and actual is not None:
                    if not expected.strip():
                        reason = 'An empty expected-output cell cannot distinguish a valid empty reference from missing ground truth.'
                    elif declared_status == 'unverified':
                        reason = 'The source records this check as unverified; it is not treated as completed.'
                    elif declared_status not in ('', 'pass', 'fail'):
                        reason = 'The recorded check status is not recognized.'
                    elif declared_status == 'fail' and expected == actual:
                        reason = 'Reported check failure conflicts with matching recorded outputs; review is required.'
                    elif name in used_names:
                        reason = 'Multiple rows for the same check make the final output ambiguous.'
                    elif not name:
                        reason = 'The check name is empty.'
                    else:
                        status = 'pass' if expected == actual else 'fail'
                        reason = 'Recorded actual output exactly matches the supplied expected output.' if status == 'pass' else 'Recorded actual output differs from the supplied expected output.'
                used_names.add(name)
                evidence.append(Evidence(requirement_id='COMP-002', source_rows=[number], check=name or 'unnamed',
                    expected=expected, actual=actual, status=status, reason=reason))
            if unresolved_group or not rows:
                evidence.append(Evidence(requirement_id='GROUP-001', source_rows=[], check='attempt_grouping', expected=None, actual=None,
                    status='unverified', reason='No reliable attempt grouping or usable attempt rows were found.'))
            if additions:
                evidence.append(Evidence(requirement_id='COMP-EXTRA', source_rows=[], check='client_additional_criteria',
                    expected=json.dumps(additions), actual=None, status='unverified',
                    reason='Additional section 3 requirements need interpretation; this local adapter cannot verify free-text requirements.'))
            if blueprint.get('scoring_requirements'):
                evidence.append(Evidence(requirement_id='SCORE-EXTRA', source_rows=[], check='client_scoring_changes',
                    expected=json.dumps(blueprint['scoring_requirements']), actual=None, status='unverified',
                    reason='Client scoring changes require review before a score can be assigned.'))
            failed = any(e.status == 'fail' for e in evidence)
            verified = bool(evidence) and all(e.status == 'pass' for e in evidence)
            score = 0 if failed else 1 if verified else None
            status = 'failure' if failed else 'success' if verified else 'unverified'
            reasoning = ('At least one content comparison failed; score 0.' if failed else
                         'Every observed content comparison passed; proposed score 1 against the supplied references.' if verified else
                         'The available evidence cannot establish success or failure; score left unverified.')
            aid = 'ATT-' + hashlib.sha256((task.id + ':' + key).encode()).hexdigest()[:24]
            attempts.append(AttemptAssessment(task_id=task.id, attempt_id=aid,
                source_attempt_id=key if mapping['attempt'] and not unresolved_group else None,
                source_file=task.filename, score=score, status=status, reasoning=reasoning, evidence=evidence))
        notes += ['Section 3 task instructions, grader reliability, reference correctness, special-case coverage, and completeness of applicable checks still require task-level review.']
        return AgentFileOutput(attempts=attempts, mapping=mapping, task_review_notes=notes)

def run_agent(session, batch_id, adapter=None, progress=None, file_id=None):
    adapter = adapter or LocalEvidenceAgent()
    batch = session.get(TaskUploadBatch, batch_id)
    if batch is None:
        raise ValueError('Choose an uploaded batch.')
    revision = session.get(ClientTaskRevision, batch.requirement_revision_id)
    if revision.snapshot.get('rubric_version') != 2:
        raise ValueError('This batch must reference the section 3 post-training rubric.')
    tasks = list(session.scalars(select(UploadedTaskCSV).where(UploadedTaskCSV.batch_id == batch_id).order_by(UploadedTaskCSV.id)))
    if len(tasks) > 1 and not file_id:
        raise ValueError('Choose one file to assess; files cannot be assessed together.')
    if file_id:
        tasks = [task for task in tasks if task.id == file_id]
        if not tasks:
            raise ValueError('The selected file does not belong to this upload.')
    calibration = session.scalar(select(AssessmentJob).where(AssessmentJob.batch_id == batch_id,
        AssessmentJob.adapter == 'calibration:' + adapter.name).order_by(AssessmentJob.created_at.desc()).limit(1))
    report_version = ''
    if is_llm_adapter(adapter.name):
        from .evaluation_reports import REPORT_VERSION
        report_version = str(REPORT_VERSION)
    fingerprint = hashlib.sha256((batch_id + adapter.name + json.dumps(revision.snapshot, sort_keys=True)
        + (calibration.id if calibration else '') + report_version + (file_id or '')).encode()).hexdigest()
    old = session.scalar(select(AssessmentJob).where(AssessmentJob.fingerprint == fingerprint))
    if old:
        if is_llm_adapter(adapter.name) and old.summary.get('file_errors'):
            old.fingerprint = old.fingerprint + ':failed:' + old.id
            session.flush()
        else:
            return old.id
    readiness_id = None
    if hasattr(adapter, 'review_instructions') and hasattr(adapter, 'inspect_file'):
        from .evaluation_reports import run_readiness
        readiness_id = run_readiness(session, batch_id, judge=adapter, progress=progress, file_id=file_id)
    job = AssessmentJob(id=new_id('ASSESS'), batch_id=batch_id, fingerprint=fingerprint,
        created_at=datetime.now(timezone.utc).isoformat(), adapter=adapter.name, blueprint=revision.snapshot, summary={})
    session.add(job); session.flush()
    counts = dict(tasks=len(tasks), attempts=0, success=0, failure=0, unverified=0, file_errors=0)
    for index, task in enumerate(tasks):
        error = None
        try:
            result = adapter.assess_file(task, revision.snapshot)
        except (OSError, ValueError, UnicodeError, csv.Error) as exc:
            error = str(exc)
            result = AgentFileOutput(mapping={}, task_review_notes=[error], attempts=[AttemptAssessment(
                task_id=task.id, attempt_id=f'{task.id}-unresolved', source_attempt_id=None,
                source_file=task.filename, score=None, status='unverified', reasoning=error, evidence=[])])
            counts['file_errors'] += 1
        for attempt in result.attempts:
            if counts['attempts'] >= 200000:
                raise ValueError('Assessment exceeds 200,000 attempts. Split the input batch.')
            counts['attempts'] += 1; counts[attempt.status] += 1
            attempt.task_review_notes = result.task_review_notes
            session.add(AssessedAttempt(job_id=job.id, task_id=task.id, result=attempt.model_dump()))
        session.add(AssessmentToolCall(job_id=job.id, task_id=task.id, payload={
            'adapter': adapter.name, 'tool': ('import_conversation_llm_review' if adapter.name.startswith('conversation-llm-') else 'llm_extract_and_grade' if is_llm_adapter(adapter.name) else 'extract_source_gold_assessments' if result.mapping.get('scoring_mode') == 'source_provided_labels' else 'read_csv_and_compare_recorded_outputs'), 'sha256': task.sha256,
            'instructions': ('Independent semantic grading, blinded to existing labels.' if is_llm_adapter(adapter.name) else LocalEvidenceAgent.instructions),
            'mapping': result.mapping, 'task_review_notes': result.task_review_notes,
            'attempt_count': len(result.attempts), 'error': error,
            'model_calls': sum(1 + a.get('retries', 0) for a in getattr(adapter, 'audit', []) if not a['cache_hit']),
            'llm_calls': getattr(adapter, 'audit', []),
            'retries': sum(a.get('retries', 0) for a in getattr(adapter, 'audit', [])),
            'timeout_seconds': 60 if is_llm_adapter(adapter.name) else 30,
            'token_spend': None if is_llm_adapter(adapter.name) else 0}))
        session.flush()
        if progress:
            progress(index + 1, len(tasks))
    job.summary = {**counts, 'source_file_id': file_id, 'state': 'awaiting_human_review',
        'assessment_source': getattr(adapter, 'assessment_source', 'openai_api' if adapter.name.startswith('openai-') else 'local_rules'),
        'limitations': ('Independent LLM judgments with task extraction, source evidence and rewards. Prior gold labels and Jev predictions withheld; no code execution or external verification. LLM judgments can be wrong.' if is_llm_adapter(adapter.name) else 'Exact-output files compare recorded answers to supplied references. HF policy records reproduce source gold labels, not independent semantic judgments. No external grader execution or proof of general task correctness.')}
    if is_llm_adapter(adapter.name):
        from .evaluation_reports import build_final_report
        session.flush()
        report = build_final_report(session, job, readiness_id)
        report['assessment_source'] = job.summary['assessment_source']
        if adapter.name.startswith('conversation-llm-'):
            report['limitations'] = 'Assistant judgments from this conversation were imported with source checksums. No OpenAI API calls, external grader executions or human calibration occurred. Separate execution provenance is unverified.'
            job.summary = {**job.summary, 'limitations': report['limitations']}
        job.summary = {**job.summary, 'readiness_id': readiness_id, 'report': report}
    return job.id
