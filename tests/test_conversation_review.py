import csv
import hashlib
import io
import json
from pathlib import Path
import pytest
from sqlalchemy import select
from pipeline.db import sessions
from pipeline.intake import IntakeService
from pipeline.models import ClientTaskRevision
from pipeline.csv_uploads import save_csv_batch
from pipeline.assessment_agent import AssessmentJob, AssessmentToolCall, AssessedAttempt, run_agent, llm_job_filter
from pipeline.conversation_review import ConversationReviewAdapter


def example(tmp_path):
    data = [dict(definition='An instrument for measuring temperature.', answer='thermometer'),
            dict(definition='An optical instrument for observing distant stars.', answer='planetarium'),
            dict(definition='', answer='biography'), dict(definition='A handheld timer for elapsed time.', answer='')]
    def encoded(rows):
        out = io.StringIO(newline='')
        writer = csv.DictWriter(out, fieldnames=['definition', 'answer']); writer.writeheader(); writer.writerows(rows)
        return out.getvalue().encode()
    raw = encoded(data)
    source = tmp_path / 'original.csv'; source.write_bytes(raw)
    factory = sessions(f'sqlite:///{(tmp_path / "db.sqlite").as_posix()}')
    with factory.begin() as session:
        tid = IntakeService(session).save(dict(client_name='Review test', task_name='Definitions',
            required_capabilities='Identify the word from its definition.', outside_scope='No external execution.',
            quantities=[dict(category='Definition to word', minimum_tasks=4, attempts_per_task=1)]))
        revision = session.scalar(select(ClientTaskRevision).where(ClientTaskRevision.task_id == tid))
        blueprint = revision.snapshot
        file = io.BytesIO(raw); file.name = 'definitions.csv'
        uploads = [file]
        bid = save_csv_batch(session, revision.id, uploads, 'conversation-test', tmp_path / 'files')
    records = [dict(record=n, **row, reward=reward, expected_term=word, reasoning=reason)
        for n, (row, reward, word, reason) in enumerate(zip(data, [1, 0, None, 0],
            ['thermometer', 'telescope', None, 'stopwatch'],
            ['A thermometer measures temperature.', 'A planetarium is not the specified observing instrument.',
             'No task definition exists to judge this answer.', 'The clear task has no answer.']), 1)]
    review = dict(assessment_source='assistant_conversation', source_sha256=hashlib.sha256(raw).hexdigest(),
        requirements_sha256=hashlib.sha256(json.dumps(blueprint, sort_keys=True).encode()).hexdigest(),
        records=records, instruction_review=dict(summary='Instructions reviewed.', concerns=[]))
    path = tmp_path / 'review.json'; path.write_text(json.dumps(review))
    return factory, bid, source, path


def test_conversation_judgments_are_bound_to_source_and_never_claim_api_calls(tmp_path, monkeypatch):
    factory, bid, source, path = example(tmp_path)
    original = source.read_bytes()
    def forbidden(*args, **kwargs):
        raise AssertionError('Conversation import must not make model API requests.')
    monkeypatch.setattr('urllib.request.urlopen', forbidden)
    with factory.begin() as session:
        jid = run_agent(session, bid, ConversationReviewAdapter(path, source))
        job = session.get(AssessmentJob, jid)
        assert job.summary['assessment_source'] == 'assistant_conversation'
        assert job.summary['success'] == 1 and job.summary['failure'] == 2 and job.summary['unverified'] == 1
        assert job.approval is None
        assert session.scalar(select(AssessmentJob.id).where(AssessmentJob.id == jid, llm_job_filter())) == jid
        calls = list(session.scalars(select(AssessmentToolCall).where(AssessmentToolCall.job_id == jid)))
        assert all(c.payload['model_calls'] == 0 and c.payload['tool'] == 'import_conversation_llm_review' for c in calls)
        results = list(session.scalars(select(AssessedAttempt).where(AssessedAttempt.job_id == jid)))
        missing = next(a.result for a in results if not a.result['source_task'])
        assert missing['score'] is None and missing['source_task'] == []
        assert all(e['source_rows'] == [4] for e in missing['evidence'])
        assert len({a.result['attempt_id'] for a in results}) == 4
        assert job.summary['report']['calibration'] is None
        assert job.summary['report']['overall'] == 'fail'
    assert source.read_bytes() == original


def test_modified_source_requires_a_new_conversation_review(tmp_path):
    _, _, source, path = example(tmp_path)
    source.write_bytes(source.read_bytes() + b'changed')
    with pytest.raises(ValueError, match='changed'):
        ConversationReviewAdapter(path, source)


def test_absent_definition_cannot_be_given_an_imported_positive_reward(tmp_path):
    _, _, source, path = example(tmp_path)
    review = json.loads(path.read_text())
    review['records'][2]['reward'] = 1
    path.write_text(json.dumps(review))
    with pytest.raises(ValueError, match='missing definition'):
        ConversationReviewAdapter(path, source)
