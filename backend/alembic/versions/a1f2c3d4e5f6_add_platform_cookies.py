"""add platform_cookies

Stores the admin-configured per-platform cookie strings the f2 parser uses
(douyin/weibo/tiktok). One row per platform (platform is the primary key).

Revision ID: a1f2c3d4e5f6
Revises: 019c53b40390
Create Date: 2026-08-26 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a1f2c3d4e5f6"
down_revision: Union[str, None] = "019c53b40390"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "platform_cookies",
        sa.Column("platform", sa.String(length=64), nullable=False),
        sa.Column("cookie", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("platform"),
    )


def downgrade() -> None:
    op.drop_table("platform_cookies")
