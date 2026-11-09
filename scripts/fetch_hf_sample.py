"""Fetch a small raw sample for schema inspection; never manufacture task scores."""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path
from uuid import uuid4

DATASET = 'MaziyarPanahi/Jev-Policy-Verification-180K'

def write_sample(rows, destination, metadata, limit=100):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    counts = {}; size = 0; count = 0
    with (destination / 'sample.jsonl').open('wb') as output:
        for row in islice(rows, limit):
            if not isinstance(row, dict):
                raise ValueError('Expected dataset rows to be objects.')
            encoded = (json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')
            if size + len(encoded) > 10 * 1024 * 1024:
                break
            output.write(encoded); size += len(encoded); count += 1
            for key, value in row.items():
                counts.setdefault(key, set()).add(type(value).__name__)
    manifest = {**metadata, 'sample_rows': count, 'sample_bytes': size,
        'field_types': {k: sorted(v) for k,v in counts.items()},
        'sha256': hashlib.sha256((destination / 'sample.jsonl').read_bytes()).hexdigest(),
        'note': 'Raw source records only. Task/attempt semantics and label meanings have not been inferred or assessed.'}
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return manifest

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit', type=int, default=100)
    parser.add_argument('--split', default=None)
    args = parser.parse_args()
    if not 1 <= args.limit <= 1000:
        parser.error('--limit must be between 1 and 1000')
    try:
        from datasets import load_dataset
        from huggingface_hub import HfApi
    except ImportError:
        parser.exit(1, 'Install the optional downloader: python -m pip install -r requirements-huggingface.txt\n')
    try:
        info = HfApi().dataset_info(DATASET)
        ds = load_dataset(DATASET, revision=info.sha, streaming=True)
        splits = list(ds)
        split = args.split or ('train' if 'train' in splits else splits[0])
        if split not in splits:
            raise ValueError(f'Unknown split {split}; available: {splits}')
        directory = Path(__file__).resolve().parents[1] / 'artifacts' / 'huggingface' / uuid4().hex
        manifest = write_sample(iter(ds[split]), directory,
            {'dataset': DATASET, 'revision': info.sha, 'split': split,
             'fetched_at': datetime.now(timezone.utc).isoformat()}, args.limit)
        print(f'Saved {manifest["sample_rows"]} raw records to {directory}')
        print('Fields:', ', '.join(manifest['field_types']))
        print('Next: inspect sample.jsonl and manifest.json before mapping tasks, attempts or success labels.')
    except Exception as exc:
        # Do not print request objects, headers, environment variables or tokens.
        parser.exit(1, f'Download failed ({type(exc).__name__}). Check network access and dataset permissions. If authentication is required, run hf auth login in your terminal.\n')

if __name__ == '__main__':
    main()
