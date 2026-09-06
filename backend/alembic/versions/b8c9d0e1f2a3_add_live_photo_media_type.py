"""allow live-photo parse tasks

The domain already exposes ``MediaType.LIVE_PHOTO`` and the application
persists its manifest in ``parse_tasks.metadata``. Existing databases still
carry the original three-value SQLite CHECK constraint, so replace that
constraint without dropping any rows.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, None] = "7d24b9c0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_MEDIA_TYPE_CHECK = "media_type IN ('video', 'image', 'live_photo', 'music')"
_LEGACY_MEDIA_TYPE_CHECK = "media_type IN ('video', 'image', 'music')"


def upgrade() -> None:
    with op.batch_alter_table("parse_tasks", schema=None) as batch_op:
        batch_op.drop_constraint("ck_parse_tasks_media_type", type_="check")
        batch_op.create_check_constraint(
            "ck_parse_tasks_media_type", _MEDIA_TYPE_CHECK
        )


def downgrade() -> None:
    with op.batch_alter_table("parse_tasks", schema=None) as batch_op:
        batch_op.drop_constraint("ck_parse_tasks_media_type", type_="check")
        batch_op.create_check_constraint(
            "ck_parse_tasks_media_type", _LEGACY_MEDIA_TYPE_CHECK
        )
