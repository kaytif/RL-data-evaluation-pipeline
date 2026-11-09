"""One uploaded CSV per task, grouped in batches."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('task_upload_batches',
        sa.Column('id', sa.String(40), primary_key=True),
        sa.Column('requirement_revision_id', sa.Integer, sa.ForeignKey('client_task_revisions.id'), nullable=False),
        sa.Column('created_at', sa.String, nullable=False), sa.Column('file_count', sa.Integer, nullable=False),
        sa.Column('total_bytes', sa.Integer, nullable=False), sa.Column('request_key', sa.String, nullable=False, unique=True))
    op.create_table('uploaded_task_csvs',
        sa.Column('id', sa.String(40), primary_key=True),
        sa.Column('batch_id', sa.String(40), sa.ForeignKey('task_upload_batches.id'), nullable=False),
        sa.Column('filename', sa.String, nullable=False), sa.Column('path', sa.String, nullable=False),
        sa.Column('sha256', sa.String, nullable=False), sa.Column('byte_count', sa.Integer, nullable=False),
        sa.Column('row_count', sa.Integer, nullable=False),
        sa.Column('preview', sa.JSON().with_variant(JSONB(), 'postgresql'), nullable=False))
    op.create_index('ix_uploaded_task_csvs_batch_id', 'uploaded_task_csvs', ['batch_id'])

def downgrade():
    op.drop_table('uploaded_task_csvs')
    op.drop_table('task_upload_batches')
