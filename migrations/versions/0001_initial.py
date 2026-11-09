"""Initial vertical slice schema."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    json_type = sa.JSON().with_variant(JSONB(), 'postgresql')
    op.create_table('projects', sa.Column('id', sa.Integer, primary_key=True), sa.Column('brief', sa.Text, nullable=False), sa.Column('state', sa.String, nullable=False))
    op.create_table('specifications', sa.Column('id', sa.Integer, primary_key=True), sa.Column('project_id', sa.Integer, sa.ForeignKey('projects.id'), nullable=False, unique=True), sa.Column('version', sa.Integer, nullable=False))
    op.create_table('requirements', sa.Column('id', sa.Integer, primary_key=True), sa.Column('specification_id', sa.Integer, sa.ForeignKey('specifications.id'), nullable=False), sa.Column('code', sa.String(50), nullable=False), sa.Column('description', sa.Text, nullable=False), sa.Column('source', sa.Text, nullable=False), sa.Column('criteria', json_type, nullable=False))
    op.create_table('submissions', sa.Column('id', sa.Integer, primary_key=True), sa.Column('project_id', sa.Integer, sa.ForeignKey('projects.id'), nullable=False), sa.Column('specification_id', sa.Integer, sa.ForeignKey('specifications.id'), nullable=False), sa.Column('version', sa.Integer, nullable=False), sa.Column('task_id', sa.String, nullable=False), sa.Column('fixture_hash', sa.String, nullable=False), sa.UniqueConstraint('project_id', 'version'))
    op.create_table('runs', sa.Column('id', sa.Integer, primary_key=True), sa.Column('submission_id', sa.Integer, sa.ForeignKey('submissions.id'), nullable=False), sa.Column('status', sa.String, nullable=False), sa.Column('evidence', json_type, nullable=False))
    op.create_table('findings', sa.Column('id', sa.Integer, primary_key=True), sa.Column('run_id', sa.Integer, sa.ForeignKey('runs.id'), nullable=False), sa.Column('requirement_id', sa.Integer, sa.ForeignKey('requirements.id'), nullable=False), sa.Column('details', json_type, nullable=False), sa.Column('decision', sa.String, nullable=False))
    op.create_table('revisions', sa.Column('id', sa.Integer, primary_key=True), sa.Column('finding_id', sa.Integer, sa.ForeignKey('findings.id'), nullable=False, unique=True), sa.Column('owner', sa.String, nullable=False), sa.Column('status', sa.String, nullable=False), sa.Column('request', sa.Text, nullable=False), sa.Column('recheck_run_id', sa.Integer, sa.ForeignKey('runs.id')))
    op.create_table('approvals', sa.Column('id', sa.Integer, primary_key=True), sa.Column('specification_id', sa.Integer, sa.ForeignKey('specifications.id'), nullable=False), sa.Column('submission_id', sa.Integer, sa.ForeignKey('submissions.id')), sa.Column('kind', sa.String, nullable=False), sa.Column('actor', sa.String, nullable=False), sa.Column('rationale', sa.Text, nullable=False), sa.Column('timestamp', sa.String, nullable=False), sa.UniqueConstraint('specification_id', 'submission_id', 'kind'))
    op.create_table('releases', sa.Column('id', sa.Integer, primary_key=True), sa.Column('submission_id', sa.Integer, sa.ForeignKey('submissions.id'), nullable=False, unique=True), sa.Column('approval_id', sa.Integer, sa.ForeignKey('approvals.id'), nullable=False), sa.Column('manifest', json_type, nullable=False))
    op.create_table('model_calls', sa.Column('id', sa.Integer, primary_key=True), sa.Column('project_id', sa.Integer, sa.ForeignKey('projects.id'), nullable=False), sa.Column('payload', json_type, nullable=False))

def downgrade():
    for table in ['model_calls', 'releases', 'approvals', 'revisions', 'findings', 'runs', 'submissions', 'requirements', 'specifications', 'projects']:
        op.drop_table(table)

