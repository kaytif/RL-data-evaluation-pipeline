"""Persist dataset submissions, provenance, and imported records."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None

def upgrade():
    jt = sa.JSON().with_variant(JSONB(), 'postgresql')
    op.create_table('data_submissions',
        sa.Column('id', sa.String(40), primary_key=True),
        sa.Column('requirement_revision_id', sa.Integer, sa.ForeignKey('client_task_revisions.id'), nullable=False),
        sa.Column('fingerprint', sa.String, unique=True, nullable=False),
        sa.Column('created_at', sa.String, nullable=False),
        sa.Column('snapshot', jt, nullable=False), sa.Column('report', jt, nullable=False), sa.Column('files', jt, nullable=False))
    op.create_table('imported_records', sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('submission_id', sa.String(40), sa.ForeignKey('data_submissions.id'), nullable=False),
        sa.Column('role', sa.String, nullable=False), sa.Column('source_file', sa.String, nullable=False),
        sa.Column('source_row', sa.Integer, nullable=False), sa.Column('payload', jt, nullable=False))
    op.create_index('ix_imported_records_submission_id', 'imported_records', ['submission_id'])

def downgrade():
    op.drop_table('imported_records')
    op.drop_table('data_submissions')
