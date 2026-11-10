"""Bulk task-file storage. A CSV file represents one task, with no imposed schema."""
import csv
import hashlib
import io
import shutil
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import ForeignKey, String, select
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base
from .models import ClientTaskRevision, data_type
from .intake import new_id
from .storage import artifact_root

MAX_FILES = 10000
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_BATCH_BYTES = 500 * 1024 * 1024
ROOT = artifact_root() / 'task_uploads'

class TaskUploadBatch(Base):
    __tablename__ = 'task_upload_batches'
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    requirement_revision_id: Mapped[int] = mapped_column(ForeignKey('client_task_revisions.id'))
    created_at: Mapped[str]
    file_count: Mapped[int]
    total_bytes: Mapped[int]
    request_key: Mapped[str] = mapped_column(unique=True)

class UploadedTaskCSV(Base):
    __tablename__ = 'uploaded_task_csvs'
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey('task_upload_batches.id'), index=True)
    filename: Mapped[str]
    path: Mapped[str]
    sha256: Mapped[str]
    byte_count: Mapped[int]
    row_count: Mapped[int]
    preview: Mapped[list] = mapped_column(data_type)

def save_csv_batch(session, revision_id, uploads, request_key, artifact_root=None, progress=None):
    """Streams each uploaded file to disk; caller owns the DB transaction."""
    old = session.scalar(select(TaskUploadBatch).where(TaskUploadBatch.request_key == request_key))
    if old:
        if old.requirement_revision_id != revision_id:
            raise ValueError('This upload request belongs to another requirements revision.')
        return old.id
    if session.get(ClientTaskRevision, revision_id) is None:
        raise ValueError('Choose saved client requirements before uploading.')
    if not 1 <= len(uploads) <= MAX_FILES:
        raise ValueError('Choose between 1 and 10,000 CSV files.')
    bid = new_id('BATCH')
    directory = (Path(artifact_root) if artifact_root else ROOT) / bid
    directory.mkdir(parents=True, exist_ok=False)
    batch = TaskUploadBatch(id=bid, requirement_revision_id=revision_id,
        created_at=datetime.now(timezone.utc).isoformat(), file_count=len(uploads), total_bytes=0, request_key=request_key)
    session.add(batch); session.flush()
    try:
        for index, upload in enumerate(uploads):
            name = str(upload.name)
            if Path(name).suffix.lower() != '.csv':
                raise ValueError(f'{name}: only CSV files are supported.')
            # Keep the full UUID within the 40-character primary/foreign keys.
            task_id = new_id('DATA')
            target = directory / f'{task_id}.csv'
            digest = hashlib.sha256(); size = 0
            upload.seek(0)
            with target.open('wb') as destination:
                while chunk := upload.read(1024 * 1024):
                    size += len(chunk); batch.total_bytes += len(chunk)
                    if size > MAX_FILE_BYTES or batch.total_bytes > MAX_BATCH_BYTES:
                        raise ValueError('Upload exceeds 50 MiB per file or 500 MiB per batch. Split the upload into smaller batches.')
                    digest.update(chunk); destination.write(chunk)
            if not size:
                raise ValueError(f'{name}: file is empty.')
            rows = 0; preview = []
            try:
                csv.field_size_limit(MAX_FILE_BYTES)
                with target.open('r', encoding='utf-8-sig', newline='') as source:
                    for row in csv.reader(source, strict=True):
                        rows += 1
                        if len(preview) < 5:
                            preview.append([cell[:500] for cell in row[:30]])
            except (UnicodeError, csv.Error) as exc:
                raise ValueError(f'{name}: could not read as UTF-8 CSV: {exc}') from exc
            if not rows:
                raise ValueError(f'{name}: no CSV records found.')
            session.add(UploadedTaskCSV(id=task_id, batch_id=bid, filename=name, path=str(target),
                sha256=digest.hexdigest(), byte_count=size, row_count=rows, preview=preview))
            if (index + 1) % 100 == 0:
                session.flush()
            if progress:
                progress(index + 1, len(uploads))
        session.flush()
    except BaseException:
        shutil.rmtree(directory)
        raise
    return bid
