"""Structured post-training rubric, preserving prior intake records."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('client_task_rubrics',
        sa.Column('task_id', sa.String(40), sa.ForeignKey('client_tasks.id'), primary_key=True),
        sa.Column('payload', sa.JSON().with_variant(JSONB(), 'postgresql'), nullable=False))

def downgrade():
    op.drop_table('client_task_rubrics')
