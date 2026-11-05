"""enable pg_trgm extension

Revision ID: 1bd5755ec205
Revises: e0a7091d6e0e
Create Date: 2026-11-05 16:56:32.607838

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1bd5755ec205'
down_revision: Union[str, None] = 'e0a7091d6e0e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
