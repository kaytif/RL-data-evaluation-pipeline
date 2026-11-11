import hashlib
import json
from types import SimpleNamespace
import pytest
from pipeline.llm_assessment import OpenAIJudge, Extraction, Judgment, clean
from pipeline.evaluation_types import CheckSet


def task_file(tmp_path, text):
    path = tmp_path / 'task.csv'
    path.write_text(text, encoding='utf-8')
    return SimpleNamespace(id='DATA-TASK-test', filename=path.name, path=str(path),
                           sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def extracted(candidate='wrong', rows=None):
    return Extraction(attempts=[dict(task='Say hello', candidate=candidate, criteria=['correct greeting'],
        reference_answer='hello', source_rows=rows or [2])], notes=[])


def test_blinding_removes_nested_prior_labels():
    result = clean(dict(gold_label='true', gold_score=1, jev_prediction='.99', reward=1,
        state_json=json.dumps(dict(conversation=[dict(content='Say hello', role='user')],
            candidate_response='wrong', evaluation_principle='accuracy', reward=1))))
    assert set(result) == {'state_json'}
    assert 'reward' not in result['state_json']
    assert result['state_json']['candidate_response'] == 'wrong'


def test_independent_reward_not_gold_and_evidence(tmp_path, monkeypatch):
    task = task_file(tmp_path, 'prompt,actual_answer,expected_answer,gold_label,jev_prediction\nSay hello,wrong,hello,true,0.99\n')
    judge = OpenAIJudge('test-key', cache_root=tmp_path / 'cache')
    calls = []
    def call(instructions, payload, output_type):
        calls.append(payload)
        return extracted() if output_type.__name__ == 'Extraction' else CheckSet(checks=[dict(
            requirement_id='CRIT-001', applicable=True, status='fail', expected='hello', actual='wrong',
            reason='The response does not provide the required greeting.', suggested_fix='Supply a separate execution with the correct greeting.')])
    monkeypatch.setattr(judge, 'call', call)
    result = judge.assess_file(task, {})
    assert result.attempts[0].score == 0
    assert result.attempts[0].evidence[0].source_rows == [2]
    assert 'gold_label' not in json.dumps(calls)
    assert 'jev_prediction' not in json.dumps(calls)


@pytest.mark.parametrize('prompt_column', ['prompt', 'definition'])
def test_plain_answers_are_graded_without_model_extraction_or_reference(tmp_path, monkeypatch, prompt_column):
    task = task_file(tmp_path, f'{prompt_column},answer\nName the instrument used to measure temperature.,thermometer\nGive the opposite of hot.,hot\n')
    judge = OpenAIJudge('test-key', cache_root=tmp_path / 'cache')
    calls = []
    def call(instructions, payload, output_type):
        assert output_type is CheckSet, 'Simple source columns must not use model extraction.'
        calls.append(payload)
        item = payload['attempt']
        assert item['reference_answer'] is None
        status = 'pass' if item['candidate'] == 'thermometer' else 'fail'
        return CheckSet(checks=[dict(requirement_id=r['requirement_id'], applicable=True, status=status,
            expected='The correct instrument or an opposite word.', actual=item['candidate'],
            reason='The instrument is correct.' if status == 'pass' else 'Repeating hot does not give its opposite.',
            suggested_fix='') for r in payload['requirements']])
    monkeypatch.setattr(judge, 'call', call)
    results = judge.assess_file(task, {})
    assert [item.score for item in results.attempts] == [1, 0]
    assert [p['attempt']['candidate'] for p in calls] == ['thermometer', 'hot']
    assert [item.evidence[0].source_rows for item in results.attempts] == [[2], [3]]


def test_flat_source_reference_is_optional_and_only_read_from_explicit_column(tmp_path, monkeypatch):
    task = task_file(tmp_path, 'prompt,answer,expected_answer\nSay hello,wrong,hello\n')
    judge = OpenAIJudge('test-key', cache_root=tmp_path / 'cache')
    monkeypatch.setattr(judge, 'call', lambda *args: pytest.fail('No extraction model request is needed.'))
    _, extracted = judge.inspect_file(task, {})
    assert extracted.attempts[0].candidate == 'wrong'
    assert extracted.attempts[0].reference_answer == 'hello'


def test_answer_column_is_not_assumed_candidate_when_another_response_column_exists(tmp_path, monkeypatch):
    task = task_file(tmp_path, 'prompt,answer,candidate_response\nSay hello,hello,wrong\n')
    judge = OpenAIJudge('test-key', cache_root=tmp_path / 'cache')
    calls = []
    def call(instructions, payload, output_type):
        calls.append(output_type)
        return extracted()
    monkeypatch.setattr(judge, 'call', call)
    _, output = judge.inspect_file(task, {})
    assert calls == [Extraction]
    assert output.attempts[0].candidate == 'wrong'
    assert output.attempts[0].reference_answer == 'hello'


@pytest.mark.parametrize('extraction', [extracted(rows=[99]), extracted(candidate='invented')])
def test_reject_fabricated_source(tmp_path, monkeypatch, extraction):
    task = task_file(tmp_path, 'prompt,actual_answer,expected_answer\nSay hello,wrong,hello\n')
    judge = OpenAIJudge('test-key')
    monkeypatch.setattr(judge, 'call', lambda *args: extraction)
    with pytest.raises(ValueError):
        judge.assess_file(task, {})


def test_missing_key_is_explicit(monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    with pytest.raises(ValueError, match='API key'):
        OpenAIJudge()


def test_real_transport_shape_and_cache(tmp_path, monkeypatch):
    seen = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self):
            return json.dumps(dict(status='completed', id='resp-test', model='test-model', usage={'input_tokens': 10},
                output=[dict(content=[dict(type='output_text', text=Judgment(reward=None,
                    reasoning='Missing evidence.', evidence='No execution trace.').model_dump_json())])])).encode()
    def send(req, timeout):
        seen.append(json.loads(req.data))
        return Response()
    monkeypatch.setattr('pipeline.llm_assessment.request.urlopen', send)
    judge = OpenAIJudge('test-key', cache_root=tmp_path)
    a = judge.call('Assess', {'candidate': 'test'}, Judgment)
    b = judge.call('Assess', {'candidate': 'test'}, Judgment)
    assert a == b and a.reward is None
    assert len(seen) == 1
    assert seen[0]['store'] is False
    assert seen[0]['text']['format']['strict'] is True
    assert judge.audit[-1]['cache_hit'] is True


def test_connection_error_explains_windows_denial(monkeypatch):
    from urllib.error import URLError
    exc = OSError('blocked')
    exc.winerror = 10013
    def blocked(*args, **kwargs):
        raise URLError(exc)
    monkeypatch.setattr('pipeline.llm_assessment.request.urlopen', blocked)
    judge = OpenAIJudge('test-key')
    with pytest.raises(RuntimeError, match='10013'):
        judge.test_connection()
    with pytest.raises(RuntimeError, match='10013'):
        judge.call('Assess', {'test': 'blocked-connection'}, Judgment)


def test_preflight_does_not_generate_tokens(monkeypatch):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return b'{"id":"test-model"}'
    def send(req, timeout):
        assert req.get_method() == 'GET'
        assert req.data is None
        return Response()
    monkeypatch.setattr('pipeline.llm_assessment.request.urlopen', send)
    assert 'Connected' in OpenAIJudge('test-key', model='test-model').test_connection()


@pytest.mark.parametrize('code', ['insufficient_quota', 'credit_balance_exhausted', 'project_spend_limit_exceeded'])
def test_quota_errors_stop_without_retry(tmp_path, monkeypatch, code):
    from urllib.error import HTTPError
    import io
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise HTTPError('https://api.openai.com/v1/responses', 429, 'Too many requests', {},
                        io.BytesIO(json.dumps({'error': {'code': code}}).encode()))
    monkeypatch.setattr('pipeline.llm_assessment.request.urlopen', fail)
    with pytest.raises(RuntimeError, match='credits|limit'):
        OpenAIJudge('test-key', cache_root=tmp_path).call('Assess', {'case': code}, Judgment)
    assert len(calls) == 1


def test_temporary_rate_limit_retries_are_bounded(tmp_path, monkeypatch):
    from urllib.error import HTTPError
    import io
    calls = []
    sleeps = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise HTTPError('https://api.openai.com/v1/responses', 429, 'Too many requests', {'Retry-After': '1'},
                        io.BytesIO(b'{"error":{"code":"rate_limit_exceeded"}}'))
    monkeypatch.setattr('pipeline.llm_assessment.request.urlopen', fail)
    monkeypatch.setattr('pipeline.llm_assessment.time.sleep', sleeps.append)
    with pytest.raises(RuntimeError, match='temporarily rate-limited'):
        OpenAIJudge('test-key', cache_root=tmp_path).call('Assess', {'case': 'temporary'}, Judgment)
    assert len(calls) == 3 and sleeps == [1, 2]


def test_page_recovers_stale_judge_before_connection_action(tmp_path, monkeypatch):
    from pathlib import Path
    from urllib.error import URLError
    from streamlit.testing.v1 import AppTest
    from pipeline import llm_assessment
    from test_assessment_agent import setup
    url, _, _ = setup(tmp_path, b'prompt,actual_answer,expected_answer\nSay hello,hello,hello\n')
    monkeypatch.setenv('DATABASE_URL', url)
    monkeypatch.setenv('OPENAI_API_KEY', 'test-key')
    class StaleJudge:
        pass
    monkeypatch.setattr(llm_assessment, 'OpenAIJudge', StaleJudge)
    def unavailable(*args, **kwargs):
        raise URLError('test offline')
    monkeypatch.setattr('urllib.request.urlopen', unavailable)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py'), default_timeout=30).run()
    app.switch_page('pages/3_Assess_tasks.py').run()
    assert not app.exception
    next(b for b in app.button if b.label == 'Test connection').click().run()
    assert not app.exception
    assert any('Could not connect' in e.value for e in app.error)
