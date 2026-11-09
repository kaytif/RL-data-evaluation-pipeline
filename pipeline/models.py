from datetime import datetime, timezone
from sqlalchemy import ForeignKey, JSON, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base

data_type = JSON().with_variant(JSONB(), 'postgresql')

class Client(Base):
    __tablename__ = 'clients'
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[str]

class ClientTask(Base):
    __tablename__ = 'client_tasks'
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    client_id: Mapped[str] = mapped_column(ForeignKey('clients.id'))
    name: Mapped[str] = mapped_column(String(200))
    revision: Mapped[int]
    intended_use: Mapped[str] = mapped_column(Text)
    task_coverage: Mapped[str] = mapped_column(Text)
    record_completeness: Mapped[str] = mapped_column(Text)
    outcome_evidence: Mapped[str] = mapped_column(Text)
    integrity_restrictions: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str]
    updated_at: Mapped[str]

class ClientTaskRevision(Base):
    __tablename__ = 'client_task_revisions'
    __table_args__ = (UniqueConstraint('task_id', 'revision'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey('client_tasks.id'))
    revision: Mapped[int]
    snapshot: Mapped[dict] = mapped_column(data_type)
    saved_at: Mapped[str]

class ClientTaskRubric(Base):
    __tablename__ = 'client_task_rubrics'
    task_id: Mapped[str] = mapped_column(ForeignKey('client_tasks.id'), primary_key=True)
    payload: Mapped[dict] = mapped_column(data_type)

class Project(Base):
    __tablename__ = 'projects'
    id: Mapped[int] = mapped_column(primary_key=True)
    brief: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(default='awaiting_specification_approval')

class Specification(Base):
    __tablename__ = 'specifications'
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey('projects.id'), unique=True)
    version: Mapped[int] = mapped_column(default=1)

class Requirement(Base):
    __tablename__ = 'requirements'
    id: Mapped[int] = mapped_column(primary_key=True)
    specification_id: Mapped[int] = mapped_column(ForeignKey('specifications.id'))
    code: Mapped[str] = mapped_column(String(50))
    description: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    criteria: Mapped[dict] = mapped_column(data_type)

class Submission(Base):
    __tablename__ = 'submissions'
    __table_args__ = (UniqueConstraint('project_id', 'version'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey('projects.id'))
    specification_id: Mapped[int] = mapped_column(ForeignKey('specifications.id'))
    version: Mapped[int]
    task_id: Mapped[str] = mapped_column(default='csv-settled')
    fixture_hash: Mapped[str]

class Run(Base):
    __tablename__ = 'runs'
    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey('submissions.id'))
    status: Mapped[str] = mapped_column(default='running')
    evidence: Mapped[dict] = mapped_column(data_type, default=dict)

class Finding(Base):
    __tablename__ = 'findings'
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey('runs.id'))
    requirement_id: Mapped[int] = mapped_column(ForeignKey('requirements.id'))
    details: Mapped[dict] = mapped_column(data_type)
    decision: Mapped[str] = mapped_column(default='pending')

class Revision(Base):
    __tablename__ = 'revisions'
    id: Mapped[int] = mapped_column(primary_key=True)
    finding_id: Mapped[int] = mapped_column(ForeignKey('findings.id'), unique=True)
    owner: Mapped[str]
    status: Mapped[str] = mapped_column(default='requested')
    request: Mapped[str] = mapped_column(Text)
    recheck_run_id: Mapped[int | None] = mapped_column(ForeignKey('runs.id'))

class Approval(Base):
    __tablename__ = 'approvals'
    __table_args__ = (UniqueConstraint('specification_id', 'submission_id', 'kind'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    specification_id: Mapped[int] = mapped_column(ForeignKey('specifications.id'))
    submission_id: Mapped[int | None] = mapped_column(ForeignKey('submissions.id'))
    kind: Mapped[str]
    actor: Mapped[str]
    rationale: Mapped[str] = mapped_column(Text)
    timestamp: Mapped[str] = mapped_column(default=lambda: datetime.now(timezone.utc).isoformat())

class Release(Base):
    __tablename__ = 'releases'
    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey('submissions.id'), unique=True)
    approval_id: Mapped[int] = mapped_column(ForeignKey('approvals.id'))
    manifest: Mapped[dict] = mapped_column(data_type)

class ModelCall(Base):
    __tablename__ = 'model_calls'
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey('projects.id'))
    payload: Mapped[dict] = mapped_column(data_type)
