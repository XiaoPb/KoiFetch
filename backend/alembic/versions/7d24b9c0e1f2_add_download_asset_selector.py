"""add download_tasks.asset_selector

Persists the exact prepared asset selector without changing the existing
completion timestamp/TTL contract.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7d24b9c0e1f2"
down_revision: Union[str, None] = "11eabd9721d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("download_tasks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("asset_selector", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("download_tasks", schema=None) as batch_op:
        batch_op.drop_column("asset_selector")
