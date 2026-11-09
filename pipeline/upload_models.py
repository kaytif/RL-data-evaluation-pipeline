from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base
from .models import data_type

class DataSubmission(Base):
    __tablename__ = 'data_submissions'
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    requirement_revision_id: Mapped[int] = mapped_column(ForeignKey('client_task_revisions.id'))
    fingerprint: Mapped[str] = mapped_column(unique=True)
    created_at: Mapped[str]
    snapshot: Mapped[dict] = mapped_column(data_type)
    report: Mapped[dict] = mapped_column(data_type)
    files: Mapped[list] = mapped_column(data_type)

class ImportedRecord(Base):
    __tablename__ = 'imported_records'
    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[str] = mapped_column(ForeignKey('data_submissions.id'), index=True)
    role: Mapped[str]
    source_file: Mapped[str]
    source_row: Mapped[int]
    payload: Mapped[dict] = mapped_column(data_type)
