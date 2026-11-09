import json
from scripts.fetch_hf_sample import write_sample

def test_sample_preserves_nested_records_and_bounds_iteration(tmp_path):
    consumed = []
    def rows():
        for n in range(100):
            consumed.append(n)
            yield {'id': n, 'messages': [{'role': 'user', 'content': 'example'}], 'label': 'unknown'}
    directory = tmp_path / 'sample'
    manifest = write_sample(rows(), directory, {'dataset': 'test', 'revision': 'abc', 'split': 'train'}, limit=3)
    assert consumed == [0, 1, 2]
    assert manifest['sample_rows'] == 3
    assert manifest['field_types']['messages'] == ['list']
    data = [json.loads(line) for line in (directory / 'sample.jsonl').read_text().splitlines()]
    assert data[0]['messages'][0]['content'] == 'example'
    assert 'score' not in data[0]
