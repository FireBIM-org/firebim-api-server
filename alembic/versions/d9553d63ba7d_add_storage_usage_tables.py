"""Add storage usage accounting tables

Revision ID: d9553d63ba7d
Revises: 1e9e75ea63ae
Create Date: 2026-11-12 22:00:00.000000

The API also creates these tables with create_all at startup, so every step is guarded and the upgrade is a no-op
on a database that already has them.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd9553d63ba7d'
down_revision: Union[str, None] = '1e9e75ea63ae'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    if not _has_table('storage_usage'):
        op.create_table(
            'storage_usage',
            sa.Column('scope', sa.String(), nullable=False),
            sa.Column('used_bytes', sa.BigInteger(), nullable=False),
            sa.Column('object_count', sa.BigInteger(), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('last_reconciled_at', sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint('scope'),
        )
    if not _has_table('storage_usage_user'):
        op.create_table(
            'storage_usage_user',
            sa.Column('user_id', sa.String(), nullable=False),
            sa.Column('used_bytes', sa.BigInteger(), nullable=False),
            sa.Column('object_count', sa.BigInteger(), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint('user_id'),
        )
    if not _has_table('storage_usage_prefix'):
        op.create_table(
            'storage_usage_prefix',
            sa.Column('prefix', sa.String(), nullable=False),
            sa.Column('used_bytes', sa.BigInteger(), nullable=False),
            sa.Column('object_count', sa.BigInteger(), nullable=False),
            sa.PrimaryKeyConstraint('prefix'),
        )
    if not _has_table('user_activity'):
        op.create_table(
            'user_activity',
            sa.Column('user_id', sa.String(), nullable=False),
            sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint('user_id'),
        )


def downgrade() -> None:
    for name in ('user_activity', 'storage_usage_prefix', 'storage_usage_user', 'storage_usage'):
        if _has_table(name):
            op.drop_table(name)
