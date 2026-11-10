"""Import already-authored conversation judgments through the normal assessment services."""
import hashlib
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.db import sessions
from pipeline.assessment_agent import AssessmentJob, run_agent
from pipeline.conversation_review import ConversationReviewAdapter
from pipeline.evaluation_reports import load_batch

ROOT = Path(__file__).resolve().parents[1]


def main():
    directory = ROOT / 'artifacts/definition_quality_demo'
    manifest = json.loads((directory / 'quality_outcomes.json').read_text(encoding='utf-8'))
    bid = manifest['batch_id']
    review_path = directory / 'conversation_review.json'
    document = json.loads(review_path.read_text(encoding='utf-8'))
    factory = sessions(f'sqlite:///{(ROOT / "acceptance.db").as_posix()}')
    with factory.begin() as session:
        _, blueprint, files = load_batch(session, bid)
        if len(files) != 1:
            raise ValueError('Choose the single combined definitions file before importing its review.')
        binding = hashlib.sha256(json.dumps(blueprint, sort_keys=True).encode()).hexdigest()
        if document.get('requirements_sha256') not in (None, binding):
            raise ValueError('Saved requirements differ from those bound to the conversation review.')
        document['requirements_sha256'] = binding
        review_path.write_text(json.dumps(document, indent=2), encoding='utf-8')
        adapter = ConversationReviewAdapter(review_path, directory / 'definitions_missing_data_100.csv')
        jid = run_agent(session, bid, adapter, file_id=files[0].id)
        job = session.get(AssessmentJob, jid)
        report = job.summary['report']
        print(json.dumps(dict(assessment_id=jid, assessment_source=job.summary['assessment_source'],
            counts={k: job.summary[k] for k in ('tasks', 'attempts', 'success', 'failure', 'unverified', 'file_errors')},
            dataset_compliance=report['overall'], reward_summary=report['rewards'],
            export_approved=bool(job.approval), api_calls=0), indent=2))


if __name__ == '__main__':
    main()
