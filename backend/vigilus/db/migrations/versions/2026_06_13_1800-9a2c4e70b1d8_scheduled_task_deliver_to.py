"""scheduled_task.deliver_to for channel delivery

Revision ID: 9a2c4e70b1d8
Revises: 45cbf17bbdbf
Create Date: 2026-06-13 18:00:00.000000+00:00

Adds a nullable JSON ``deliver_to`` column on ``scheduled_tasks``. When set to
``{"platform": "...", "chat_id": "..."}`` a successful scheduled run also
pushes its summary to that channel via the gateway.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9a2c4e70b1d8'
down_revision: Union[str, None] = '45cbf17bbdbf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'scheduled_tasks',
        sa.Column('deliver_to', sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('scheduled_tasks', 'deliver_to')
