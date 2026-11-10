"""Import actual conversation-authored LLM judgments; never imitate model API calls."""
import csv
import hashlib
import json
import re
from pathlib import Path
from .assessment_agent import AgentFileOutput, AttemptAssessment, Evidence
from .evaluation_types import Check, CheckSet, InstructionReview, requirements, reward_from_checks
from .llm_assessment import ExtractedAttempt, Extraction, PackageReview


class ConversationReviewAdapter:
    assessment_source = 'assistant_conversation'

    def __init__(self, review_path, source_path):
        raw = Path(review_path).read_bytes()
        self.document = json.loads(raw)
        if self.document.get('assessment_source') != self.assessment_source:
            raise ValueError('Review must explicitly identify its conversation-based source.')
        source = Path(source_path).read_bytes()
        if hashlib.sha256(source).hexdigest() != self.document['source_sha256']:
            raise ValueError('The source dataset changed after the conversation review.')
        import io
        rows = list(csv.DictReader(io.StringIO(source.decode('utf-8-sig'))))
        records = self.document['records']
        if [r['record'] for r in records] != list(range(1, len(rows) + 1)):
            raise ValueError('The review must contain exactly one judgment per source record, in order.')
        self.records = {}
        for record, row in zip(records, rows):
            if any(record[k] != row[k] for k in ('definition', 'answer')):
                raise ValueError('Reviewed content does not match the original dataset.')
            reward = record['reward']
            if reward is not None and (type(reward) is not int or reward not in (0, 1)):
                raise ValueError('Imported rewards must be 0, 1 or null.')
            if not record['reasoning'].strip():
                raise ValueError('Every reward needs a conversation-authored explanation.')
            if not record['definition'].strip() and reward is not None:
                raise ValueError('A missing definition cannot receive a verified reward in this review.')
            if reward == 1 and not record['answer'].strip():
                raise ValueError('An empty answer cannot pass this task.')
            self.records[record['record']] = record
        self.name = 'conversation-llm-v1:' + hashlib.sha256(raw).hexdigest()[:16]
        self.audit = []

    def _bound_record(self, task):
        match = re.fullmatch(r'definition-(\d{3})\.csv', task.filename)
        if not match or int(match.group(1)) not in self.records:
            raise ValueError('No conversation judgment is bound to this source file.')
        record = self.records[int(match.group(1))]
        raw = Path(task.path).read_bytes()
        if hashlib.sha256(raw).hexdigest() != task.sha256:
            raise ValueError('Original uploaded file checksum changed.')
        import io
        reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig')))
        rows = list(reader)
        if reader.fieldnames != ['definition', 'answer'] or len(rows) != 1:
            raise ValueError('This review is bound to one original definition/answer row per file.')
        if rows[0] != {k: record[k] for k in ('definition', 'answer')}:
            raise ValueError('Uploaded content differs from the reviewed source record.')
        return record

    def review_instructions(self, blueprint):
        expected = self.document.get('requirements_sha256')
        actual = hashlib.sha256(json.dumps(blueprint, sort_keys=True).encode()).hexdigest()
        if not expected or expected != actual:
            raise ValueError('The instructions differ from those reviewed in the conversation.')
        return InstructionReview.model_validate(self.document['instruction_review'])

    def inspect_file(self, task, blueprint):
        self.review_instructions(blueprint)
        records = self._bound_records(task)
        rows, attempts, packages = [], [], []
        for record, source_row in records:
            record_rows, extracted = self._inspect_record(record, blueprint)
            rows.extend(dict(source_row=source_row, data=row['data']) for row in record_rows)
            for item in extracted.attempts:
                item.source_rows = [source_row]
                attempts.append(item)
            for package in extracted.packages:
                package.source_rows = [source_row]
                packages.append(package)
        return rows, Extraction(attempts=attempts, packages=packages, notes=['Source-bound conversation review; no API calls.'])

    def _bound_records(self, task):
        raw = Path(task.path).read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != task.sha256:
            raise ValueError('Original uploaded file checksum changed.')
        if digest == self.document['source_sha256']:
            return [(record, n + 1) for n, record in sorted(self.records.items())]
        return [(self._bound_record(task), 2)]

    def _inspect_record(self, record, blueprint):
        definition = record['definition']
        category = 'Definition to word' if definition.strip() else None
        task_text = definition or f'Unresolved record {record["record"]}: no definition supplied.'
        item = ExtractedAttempt(task=task_text, candidate=record['answer'], criteria=['Return a term matching the supplied definition.'],
            reference_answer=None, source_rows=[2], category=category)
        package = []
        for requirement in requirements(blueprint, task_level=True):
            code = requirement['requirement_id']
            status, actual, reason, fix = 'unverified', None, 'This additional requirement was not assessed in this conversation.', 'Review this requirement against the source evidence.'
            if code == 'USE-001':
                status = 'pass' if definition else 'fail'
                actual = definition or 'Definition field is empty.'
                reason = 'The supplied text poses a definition-to-word identification task.' if definition else 'Without a definition, this record does not specify the concept the agent must identify.'
                fix = '' if definition else 'Restore the original definition; do not infer it from the candidate word.'
            elif code == 'SCOPE-001':
                status, actual = 'pass', 'Only definition text and a candidate word are present.'
                reason, fix = 'No browser, image interpretation or executable task instructions are required by this record.', ''
            elif code == 'COMP-001':
                status, actual = ('pass', definition) if definition else ('fail', 'No definition supplied.')
                reason = 'The target concept can be determined from the provided definition.' if definition else 'The expected concept cannot be determined from an empty definition.'
                fix = '' if definition else 'Supply the missing task definition and re-evaluate this record.'
            elif code == 'COMP-002':
                status, actual = 'fail', 'The original CSV has only definition and answer; no vendor grader or supplied reference is present.'
                reason = 'This conversation review is not an executed or supplied vendor grader. The default package requirement is not met by the submitted two-column record.'
                fix = 'Supply the required grader and reference or reproducible method, or obtain an explicit client revision accepting semantic review as the method.'
            elif code == 'COMP-003':
                status, actual = 'unverified', 'No task-specific candidate instructions for empty or invalid definitions were supplied.'
                reason = 'The client specifies how the evaluator labels missing data, but that does not establish how an agent was instructed to handle invalid task inputs.'
                fix = 'Specify the expected candidate behavior for empty or invalid definitions where applicable.'
            elif code == 'OTHER-001':
                status, actual = 'pass', 'Original two-column authored test records are retained; no reference reward column was supplied.'
                reason, fix = 'The comments describe test data and explicitly do not claim verified independent executions.', ''
            package.append(Check(requirement_id=code, applicable=True, status=status, expected=requirement['rule'], actual=actual,
                reason=reason, suggested_fix=fix))
        extracted = Extraction(attempts=[item], notes=['Task and response were reviewed by the assistant in this conversation; no API call was made.'],
            packages=[PackageReview(task=task_text, category=category, source_rows=[2], checks=package)])
        return [dict(source_row=2, data={k: record[k] for k in ('definition', 'answer')})], extracted

    def assess_file(self, task, blueprint):
        self.review_instructions(blueprint)
        outputs = [self._assess_record(task, record, source_row) for record, source_row in self._bound_records(task)]
        return AgentFileOutput(attempts=[a for output in outputs for a in output.attempts],
            mapping=outputs[0].mapping, task_review_notes=outputs[0].task_review_notes)

    def _assess_record(self, task, record, source_row):
        definition, answer = record['definition'], record['answer']
        expected = record['expected_term']
        content_status = 'pass' if record['reward'] == 1 else 'fail' if record['reward'] == 0 else 'unverified'
        checks = [Check(requirement_id='CONTEXT-001', applicable=True, status='pass' if definition else 'unverified',
            expected='A supplied definition identifying the target concept.', actual=definition or None,
            reason='The task definition is present.' if definition else 'The target definition is absent; no target concept is inferred from the answer.',
            suggested_fix='' if definition else 'Restore the original task definition.'),
            Check(requirement_id='RESPONSE-001', applicable=True, status='pass' if answer else 'fail' if definition else 'unverified',
                expected='A candidate term answering a specified definition.', actual=answer,
                reason='A candidate answer is supplied.' if answer else 'No answer was supplied to the clear task.' if definition else 'Both task context and response are absent.',
                suggested_fix='' if answer else 'Check whether the blank is an actual empty execution result or lost data. Obtain a separate corrected execution if needed.'),
            Check(requirement_id='CONTENT-001', applicable=True, status=content_status,
                expected=f'A semantically matching term, such as {expected} (assistant-derived; not supplied gold).' if expected else 'A term matching the missing definition; cannot be established.',
                actual=answer, reason=record['reasoning'], suggested_fix='' if content_status == 'pass' else
                'Restore the missing definition before reassessment.' if not definition else 'Preserve the failed response as recorded. If a successful example is needed, obtain a separate corrected execution.')]
        if reward_from_checks(checks) != record['reward']:
            raise ValueError('The imported reward conflicts with its completion-check evidence.')
        aid = 'ATT-' + hashlib.sha256((task.id + ':conversation-review:' + str(source_row)).encode()).hexdigest()[:24]
        result = AttemptAssessment(task_id=task.id, attempt_id=aid, source_attempt_id=f'record-{record["record"]} (source row {source_row})',
            source_file=task.filename, score=record['reward'], status='success' if record['reward'] == 1 else 'failure' if record['reward'] == 0 else 'unverified',
            reasoning=record['reasoning'], source_task=[dict(role='user', content=definition)] if definition else [],
            candidate_response=answer, category='Definition to word' if definition else None,
            source_task_key=hashlib.sha256(' '.join(definition.split()).encode()).hexdigest() if definition else None,
            execution_id=None, grouping_basis='inferred_source_rows' if definition else 'unresolved_missing_definition',
            evidence=[Evidence(requirement_id=c.requirement_id, source_rows=[source_row], check={
                'CONTEXT-001': 'Task definition available', 'RESPONSE-001': 'Answer provided',
                'CONTENT-001': 'Semantic match to the definition'}[c.requirement_id],
                expected=c.expected, actual=c.actual, status=c.status, reason=c.reason) for c in checks],
            check_fixes={c.requirement_id: c.suggested_fix for c in checks if c.status != 'pass'})
        return AgentFileOutput(attempts=[result], mapping=dict(scoring_mode='conversation_llm_review',
            assessment_source=self.assessment_source, definition='definition', candidate='answer', reward='Source-bound assistant judgment from this conversation'),
            task_review_notes=['Reviewed by the assistant in the conversation, then imported. No model API calls or executed vendor graders.',
                'Expected terms in the evidence are assistant-derived, not supplied references.',
                'No original execution IDs are present. Human-reviewed grader calibration and independent execution provenance remain unverified.'])
