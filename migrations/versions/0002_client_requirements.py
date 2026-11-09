"""Client requirements intake and saved revisions."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('clients',
        sa.Column('id', sa.String(40), primary_key=True),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('created_at', sa.String(), nullable=False))
    op.create_table('client_tasks',
        sa.Column('id', sa.String(40), primary_key=True),
        sa.Column('client_id', sa.String(40), sa.ForeignKey('clients.id'), nullable=False),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        *[sa.Column(field, sa.Text(), nullable=False) for field in ['intended_use', 'task_coverage',
            'record_completeness', 'outcome_evidence', 'integrity_restrictions']],
        sa.Column('created_at', sa.String(), nullable=False),
        sa.Column('updated_at', sa.String(), nullable=False))
    op.create_table('client_task_revisions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('task_id', sa.String(40), sa.ForeignKey('client_tasks.id'), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('snapshot', sa.JSON().with_variant(JSONB(), 'postgresql'), nullable=False),
        sa.Column('saved_at', sa.String(), nullable=False),
        sa.UniqueConstraint('task_id', 'revision'))

def downgrade():
    op.drop_table('client_task_revisions')
    op.drop_table('client_tasks')
    op.drop_table('clients')
