"""Load 100 definition/answer rows with deliberate omissions and run real quality checks."""
import argparse
import csv
import hashlib
import io
import json
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select
from scripts.create_definition_dataset import ITEMS
from pipeline.db import sessions
from pipeline.intake import IntakeService
from pipeline.models import ClientTaskRevision
from pipeline.csv_uploads import TaskUploadBatch, save_csv_batch
from pipeline.assessment_agent import AssessmentJob, run_agent
from pipeline.evaluation_reports import run_readiness

ROOT = Path(__file__).resolve().parents[1]


def make_rows():
    pairs = [line.split('|', 1) for line in ITEMS.splitlines()]
    assert len(pairs) == 100
    missing_definitions = {11, 23, 47, 71, 95}
    missing_answers = {7, 15, 31, 39, 55, 63, 79, 87}
    missing_both = {20, 60}
    omitted = missing_definitions | missing_answers | missing_both
    incorrect = {n for n in range(1, 101) if n not in omitted and n % 4 == 0}
    rows, omissions = [], []
    for n, (word, definition) in enumerate(pairs, 1):
        answer = pairs[(n + 12) % 100][0] if n in incorrect else word
        missing = []
        if n in missing_definitions | missing_both:
            definition = ''; missing.append('definition')
        if n in missing_answers | missing_both:
            answer = ''; missing.append('answer')
        rows.append(dict(definition=definition, answer=answer))
        if missing:
            omissions.append(dict(record=n, combined_csv_row=n + 1, file=f'definition-{n:03}.csv', missing=missing))
    construction = dict(records=100, intended_correct_complete=100 - len(omitted) - len(incorrect),
        intended_incorrect_complete=len(incorrect), records_with_missing_data=len(omitted),
        missing_definition_cells=len(missing_definitions | missing_both),
        missing_answer_cells=len(missing_answers | missing_both), omissions=omissions)
    return rows, construction


def encode(rows):
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=['definition', 'answer'])
    writer.writeheader(); writer.writerows(rows)
    return output.getvalue().encode('utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--assess', action='store_true', help='Also run live LLM assessment using OPENAI_API_KEY.')
    args = parser.parse_args()
    if args.assess and not os.getenv('OPENAI_API_KEY'):
        raise SystemExit('Set OPENAI_API_KEY before requesting live assessment. No simulated rewards will be substituted.')
    rows, construction = make_rows()
    directory = ROOT / 'artifacts/definition_quality_demo'
    directory.mkdir(parents=True, exist_ok=True)
    combined = encode(rows)
    (directory / 'definitions_missing_data_100.csv').write_bytes(combined)
    factory = sessions(f'sqlite:///{(ROOT / "acceptance.db").as_posix()}')
    with factory.begin() as session:
        request_key = 'definition-quality-demo-v1-' + hashlib.sha256(combined).hexdigest()
        batch = session.scalar(select(TaskUploadBatch).where(TaskUploadBatch.request_key == request_key))
        if batch is None:
            tid = IntakeService(session).save(dict(client_name='Vocabulary assessment',
                task_name='100 definitions - missing-data evaluation',
                required_capabilities='Identify the English word or term described by each definition. The answer column is the recorded candidate response, not a reference. Assess semantic correctness and allow valid synonyms, ignoring capitalization. Preserve empty cells: a missing definition provides insufficient task context; an observed empty answer to a clear definition fails to answer the task. Do not invent missing text. Check structural omissions independently of rewards.',
                outside_scope='Generating replacement answers or definitions, browsing, code execution.',
                quantities=[dict(category='Definition to word', minimum_tasks=100, attempts_per_task=1)],
                comments='Authored test dataset with deliberate missing values and mixed answer quality. Only definition and answer columns. Source row/file identifies a proposed recorded attempt, not independently verified execution provenance. A completely empty row is an unresolved record, not proof of an agent execution. No reference rewards or intended answer mix are supplied to the evaluator.'))
            revision = session.scalar(select(ClientTaskRevision).where(ClientTaskRevision.task_id == tid))
            uploads = []
            for n, row in enumerate(rows, 1):
                upload = io.BytesIO(encode([row]))
                upload.name = f'definition-{n:03}.csv'
                uploads.append(upload)
            bid = save_csv_batch(session, revision.id, uploads, request_key)
        else:
            bid = batch.id
        readiness_id = run_readiness(session, bid)
        readiness = session.get(AssessmentJob, readiness_id).summary['report']
        jid = None
        if args.assess:
            from pipeline.llm_assessment import OpenAIJudge
            judge = OpenAIJudge(); judge.test_connection()
            jid = run_agent(session, bid, judge, progress=lambda n, total: print(f'Processed {n}/{total}', flush=True))
        outcome = dict(batch_id=bid, readiness_id=readiness_id, assessment_id=jid,
            quality=readiness['quality'], quality_findings=[c for c in readiness['issues'] if c['phase'] == 'Data quality'],
            construction=construction, live_llm_run=jid is not None,
            note='Construction mix is not an LLM result. The structural checks were run through the real app; no model rewards were fabricated.')
    (directory / 'quality_outcomes.json').write_text(json.dumps(outcome, indent=2), encoding='utf-8')
    from pipeline.evaluation_reports import issues_csv
    (directory / 'missing_data_issues.csv').write_bytes(issues_csv(outcome['quality_findings']))
    lines = ['# 100 definitions: data-quality results', '',
        'These are observed structural-check results, not LLM reward judgments.', '',
        '| Check | Observed |', '| --- | --- |',
        f"| Records | {readiness['quality']['data_rows']} |",
        f"| Records with missing values | {construction['records_with_missing_data']} |",
        f"| Missing definitions | {construction['missing_definition_cells']} |",
        f"| Missing answers | {construction['missing_answer_cells']} |",
        f"| Total empty cells | {readiness['quality']['empty_cells']} |",
        f"| Unreadable files | {readiness['quality']['files_with_errors']} |", '',
        'Two records are missing both fields. Original CSV columns remain definition and answer.', '',
        '## Affected records', '', '| Record | Combined CSV row | Uploaded file | Missing |', '| --- | --- | --- | --- |']
    lines += [f"| {r['record']} | {r['combined_csv_row']} | {r['file']} | {', '.join(r['missing'])} |" for r in construction['omissions']]
    lines += ['', '## Reward assessment', '',
        'Live LLM assessment completed.' if jid else 'Not run: no model rewards were assigned. Enter a funded API key in the app and run assessment from a network-enabled local session.',
        '', 'The 85 complete records were authored as 62 intended-correct and 23 intended-incorrect answers. This describes dataset construction, not independently assessed rewards.',
        '', f'Batch: {bid}', f'Readiness report: {readiness_id}']
    (directory / 'data_quality_results.md').write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps(dict(batch_id=bid, readiness_id=readiness_id, assessment_id=jid,
        quality=outcome['quality'], construction={k: v for k, v in construction.items() if k != 'omissions'},
        live_llm_run=jid is not None), indent=2))


if __name__ == '__main__':
    main()
