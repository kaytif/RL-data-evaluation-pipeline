"""Agent-proposed attempt labels, evidence, tool logs and approval."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None

def upgrade():
    jt = sa.JSON().with_variant(JSONB(), 'postgresql')
    op.create_table('assessment_jobs', sa.Column('id', sa.String(40), primary_key=True),
        sa.Column('batch_id', sa.String(40), sa.ForeignKey('task_upload_batches.id'), nullable=False),
        sa.Column('fingerprint', sa.String, nullable=False, unique=True),
        sa.Column('created_at', sa.String, nullable=False), sa.Column('adapter', sa.String, nullable=False),
        sa.Column('blueprint', jt, nullable=False), sa.Column('summary', jt, nullable=False), sa.Column('approval', jt))
    op.create_table('assessed_attempts', sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('job_id', sa.String(40), sa.ForeignKey('assessment_jobs.id'), nullable=False),
        sa.Column('task_id', sa.String(40), sa.ForeignKey('uploaded_task_csvs.id'), nullable=False),
        sa.Column('result', jt, nullable=False))
    op.create_index('ix_assessed_attempts_job_id', 'assessed_attempts', ['job_id'])
    op.create_table('assessment_tool_calls', sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('job_id', sa.String(40), sa.ForeignKey('assessment_jobs.id'), nullable=False),
        sa.Column('task_id', sa.String(40), sa.ForeignKey('uploaded_task_csvs.id'), nullable=False),
        sa.Column('payload', jt, nullable=False))

def downgrade():
    op.drop_table('assessment_tool_calls')
    op.drop_table('assessed_attempts')
    op.drop_table('assessment_jobs')
