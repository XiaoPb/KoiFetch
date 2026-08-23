"""add download_tasks.token_id

Stores the one-time token ``tid`` claim on the row that first served the
bubble file, so the download-file API (Task 9) can enforce single use
atomically without an external registry.

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
