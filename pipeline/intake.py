"""Stage-one requirement storage. No model inference or evaluation occurs here."""
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import select
from .models import Client, ClientTask, ClientTaskRevision, ClientTaskRubric
from .rubric import RequirementsInput

CATEGORIES = [
    ('intended_use', '1. Intended use', 'What will the data evaluate, and which capabilities matter?',
     'Evaluate agents’ ability to complete multi-step terminal tasks.'),
    ('task_coverage', '2. Task coverage', 'Specify task categories, quantities, and distribution.',
     'At least 100 traces across file processing, debugging, and database tasks; at least 20 per category.'),
    ('record_completeness', '3. Record completeness', 'Specify required fields and evidence within each record.',
     'Every record must include a task ID, model, reward, and complete action/tool-observation history.'),
    ('outcome_evidence', '4. Outcome and grading evidence', 'Specify the success/failure mix and how outcomes must be supported.',
     'Include successful and failed attempts. Each reward must link to grader results; unsupported labels require review.'),
    ('integrity_restrictions', '5. Data integrity and restrictions', 'Specify duplicate policy, provenance, permitted sources, and exclusions.',
     'No duplicate execution records. Preserve source/version information and exclude credentials or personal data.'),
]

def new_id(prefix):
    return f'{prefix}-{uuid4().hex}'

class IntakeService:
    def __init__(self, session):
        self.s = session

    def save(self, values, client_id=None, task_id=None, expected_revision=None):
        data = RequirementsInput.model_validate(values)
        now = datetime.now(timezone.utc).isoformat()
        task = self.s.scalar(select(ClientTask).where(ClientTask.id == task_id).with_for_update()) if task_id else None
        if task_id and task is None:
            raise ValueError('Saved task no longer exists. Refresh the page.')
        if task:
            if task.revision != expected_revision:
                raise ValueError('This task changed since you opened it. Reopen it before saving.')
            client = self.s.get(Client, task.client_id)
            if client_id != client.id:
                raise ValueError('An existing task must stay linked to its original client.')
            if data.client_name != client.name:
                raise ValueError('The client name must match the saved client.')
            task.revision += 1
        else:
            client = self.s.get(Client, client_id) if client_id else None
            if client_id and client is None:
                raise ValueError('Selected client no longer exists.')
            if client and client.name != data.client_name:
                raise ValueError('The client name must match the saved client.')
            if client is None:
                client = Client(id=new_id('CL'), name=data.client_name, created_at=now)
                self.s.add(client); self.s.flush()
            task = ClientTask(id=new_id('TASK'), client_id=client.id, revision=1, created_at=now)
            self.s.add(task)
        task.name = data.task_name
        task.updated_at = now
        # Keep original columns readable without deleting pre-rubric records.
        task.intended_use = data.required_capabilities
        task.task_coverage = str(data.totals)
        task.record_completeness = 'See structured post-training rubric.'
        task.outcome_evidence = 'See structured post-training rubric.'
        task.integrity_restrictions = data.comments
        self.s.flush()
        snapshot = {**data.snapshot(), 'client_id': client.id, 'task_id': task.id}
        rubric = self.s.get(ClientTaskRubric, task.id)
        if rubric is None:
            self.s.add(ClientTaskRubric(task_id=task.id, payload=snapshot))
        else:
            rubric.payload = snapshot
        self.s.add(ClientTaskRevision(task_id=task.id, revision=task.revision,
            snapshot=snapshot, saved_at=now))
        return task.id
