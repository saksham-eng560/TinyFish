"""guardrails: pause everything, and held sends (undo window, daily and per-company limits)

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-05 12:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0003'
down_revision: str | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('users') as batch_op:
        batch_op.add_column(sa.Column('automation_paused_at', sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table('applications') as batch_op:
        batch_op.add_column(sa.Column('send_after', sa.DateTime(timezone=True), nullable=True))
        batch_op.create_index(batch_op.f('ix_applications_send_after'), ['send_after'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('applications') as batch_op:
        batch_op.drop_index(batch_op.f('ix_applications_send_after'))
        batch_op.drop_column('send_after')
    with op.batch_alter_table('users') as batch_op:
        batch_op.drop_column('automation_paused_at')
