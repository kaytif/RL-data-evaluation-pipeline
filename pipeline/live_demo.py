"""Create an explicitly synthetic example through the real application services."""
import io
from pathlib import Path
from sqlalchemy import select
from .intake import IntakeService
from .models import ClientTaskRevision
from .csv_uploads import TaskUploadBatch, save_csv_batch
from .assessment_agent import run_agent

FIXTURES = Path(__file__).resolve().parents[1] / 'fixtures' / 'live_demo'
DEMO_KEY = 'synthetic-live-demo-v1'

class DemoFile(io.BytesIO):
    def __init__(self, path):
        super().__init__(path.read_bytes())
        self.name = path.name

def load_demo(session, artifact_root=None, progress=None):
    batch = session.scalar(select(TaskUploadBatch).where(TaskUploadBatch.request_key == DEMO_KEY))
    if batch is None:
        tid = IntakeService(session).save(dict(
            client_name='DEMO - Fictional Post-Training Lab',
            task_name='DEMO - CSV, JSON and text attempts',
            required_capabilities='Filter settled transactions, uppercase a customer name, and extract invoice details.',
            outside_scope='Browser interaction, image interpretation, network access, or real personal data.',
            quantities=[dict(category=category, minimum_tasks=1, attempts_per_task=2)
                        for category in ['CSV processing', 'JSON processing', 'Text extraction']],
            comments='Synthetic demonstration only. Recorded expected/actual evidence; no real model executions. Human review remains required.'))
        revision = session.scalar(select(ClientTaskRevision).where(ClientTaskRevision.task_id == tid))
        uploads = [DemoFile(path) for path in sorted(FIXTURES.glob('*.csv'))]
        bid = save_csv_batch(session, revision.id, uploads, DEMO_KEY, artifact_root)
    else:
        bid = batch.id
    jid = run_agent(session, bid, progress=progress)
    return bid, jid
