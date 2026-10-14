"""onboarding: first-run wizard progress and profile links

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-05 09:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0002'
down_revision: str | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('users') as batch_op:
        batch_op.add_column(sa.Column('github_url', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('portfolio_url', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('profile_links', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'),
                                      nullable=True))
        batch_op.add_column(sa.Column('onboarding_step', sa.Integer(), server_default=sa.text('1'), nullable=False))
        batch_op.add_column(sa.Column('onboarding_completed_at', sa.DateTime(timezone=True), nullable=True))
    # People who were already using HireFlow aren't sent through the first-run onboarding.
    op.execute("UPDATE users SET onboarding_completed_at = created_at, onboarding_step = 8 WHERE onboarding_completed_at IS NULL")


def downgrade() -> None:
    with op.batch_alter_table('users') as batch_op:
        batch_op.drop_column('onboarding_completed_at')
        batch_op.drop_column('onboarding_step')
        batch_op.drop_column('profile_links')
        batch_op.drop_column('portfolio_url')
        batch_op.drop_column('github_url')
