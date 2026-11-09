"""Load the downloaded HF sample through the app without inventing binary predictions."""
import csv
import hashlib
import io
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select
from pipeline.db import sessions
from pipeline.intake import IntakeService
from pipeline.models import ClientTaskRevision
from pipeline.csv_uploads import TaskUploadBatch, save_csv_batch
from pipeline.assessment_agent import AssessmentJob, run_agent
from pipeline.hf_evidence import task_key
from collections import defaultdict

ROOT = Path(__file__).resolve().parents[1]


def main():
    prepare_llm = '--prepare-llm' in sys.argv
    source = ROOT / 'artifacts/huggingface/67bfbf10004345afa393084b6f079c8f'
    raw = (source / 'sample.jsonl').read_bytes()
    manifest = json.loads((source / 'manifest.json').read_text(encoding='utf-8'))
    digest = hashlib.sha256(raw).hexdigest()
    if digest != manifest['sha256']:
        raise ValueError('Sample checksum does not match its manifest.')
    rows = [json.loads(line) for line in raw.decode('utf-8').splitlines()]
    grouped = defaultdict(list)
    for row in rows:
        grouped[task_key(row)].append(row)
    note = ('Tasks grouped by identical conversation; attempts grouped by candidate response. '
            'Scores reproduce supplied gold_label assessments, cross-checked against gold_score. '
            'No independent semantic grading or Jev score thresholding. Original fields retained.')
    if prepare_llm:
        note = 'Prepared for independent LLM assessment of task responses and evaluation principles. Source labels must be withheld from the evaluator. No LLM results have been generated yet.'
    factory = sessions(f'sqlite:///{(ROOT / "acceptance.db").as_posix()}')
    with factory.begin() as session:
        key = ('hf-independent-llm-demo-v1-' if prepare_llm else 'hf-candidate-gold-demo-v2-') + digest
        batch = session.scalar(select(TaskUploadBatch).where(TaskUploadBatch.request_key == key))
        if batch is None:
            tid = IntakeService(session).save(dict(
                client_name='DEMO - Hugging Face recorded predictions',
                task_name='HF - independent LLM assessment' if prepare_llm else 'Jev Policy Verification - task and attempt scores',
                required_capabilities='Assess each candidate response against its conversation and evaluation principle.' if prepare_llm else 'Inspect supplied policy-verification reference labels and recorded prediction evidence.',
                outside_scope='Generating new candidate responses, executing uploaded code or claiming external fact verification.' if prepare_llm else 'New model execution, probability threshold selection, or independent policy judgment.',
                quantities=[dict(category='Recorded policy verification', minimum_tasks=len(grouped), attempts_per_task=1)],
                comments=note))
            revision = session.scalar(select(ClientTaskRevision).where(ClientTaskRevision.task_id == tid))
            uploads = []
            for index, (key_hash, task_rows) in enumerate(grouped.items(), 1):
                buffer = io.StringIO(newline='')
                writer = csv.DictWriter(buffer, fieldnames=list(task_rows[0]))
                writer.writeheader()
                writer.writerows(task_rows)
                upload = io.BytesIO(buffer.getvalue().encode('utf-8'))
                upload.name = f'hf-task-{index:03d}-{key_hash[:12]}.csv'
                uploads.append(upload)
            bid = save_csv_batch(session, revision.id, uploads, key)
        else:
            bid = batch.id
        jid = None if prepare_llm else run_agent(session, bid)
        job = session.get(AssessmentJob, jid) if jid else None
        report = dict(batch_id=bid, assessment_id=jid, summary=job.summary if job else {'tasks': len(grouped), 'state': 'ready_for_llm'},
                      gold_label_counts=dict(Counter(r['gold_label'] for r in rows)),
                      source_sha256=digest, mapping_note=note, approved=bool(job and job.approval))
    (source / 'app_demo_report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
