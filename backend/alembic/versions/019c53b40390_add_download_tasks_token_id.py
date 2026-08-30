"""add download_tasks.token_id

Adds a nullable column for the download token ``tid`` claim, allowing the
download-file API (Task 9) to bind a short-lived link to the task/filename and
retain an audit identifier without an external registry.

Revision ID: 019c53b40390
Revises: 56320d63278e
Create Date: 2026-01-01 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "019c53b40390"
down_revision: Union[str, None] = "56320d63278e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite cannot ALTER in place; batch mode recreates the table.
    with op.batch_alter_table("download_tasks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("token_id", sa.String(length=36), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("download_tasks", schema=None) as batch_op:
        batch_op.drop_column("token_id")
