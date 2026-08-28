"""add music_songs

Persists search results (musicdl SongInfo dicts) keyed by a content hash so
song ids are stable across searches and can drive the music import endpoint.

Revision ID: 11eabd9721d9
Revises: a1f2c3d4e5f6
Create Date: 2026-08-28 19:33:14.340101

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '11eabd9721d9'
down_revision: Union[str, None] = 'a1f2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "music_songs",
        sa.Column("song_id", sa.String(length=36), nullable=False),
        sa.Column("song_key", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("song_name", sa.String(length=512), nullable=False),
        sa.Column("singers", sa.String(length=512), nullable=True),
        sa.Column("album", sa.String(length=512), nullable=True),
        sa.Column("cover_url", sa.Text(), nullable=True),
        sa.Column("duration_s", sa.Integer(), nullable=True),
        sa.Column("ext", sa.String(length=16), nullable=True),
        sa.Column("song_info", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("song_id"),
        sa.UniqueConstraint("song_key"),
    )


def downgrade() -> None:
    op.drop_table("music_songs")
