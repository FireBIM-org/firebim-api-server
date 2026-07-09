"""Refactor IfcEntity hierarchy and delete IfcRelationship

Revision ID: 431d56c0f065
Revises: 9fc4fdbbb203
Create Date: 2026-05-28 12:44:44.490338

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

# revision identifiers, used by Alembic.
revision: str = '431d56c0f065'
down_revision: Union[str, None] = '9fc4fdbbb203'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add parent_id to ifc_entities
    op.add_column('ifc_entities', sa.Column('parent_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.create_index(op.f('ix_ifc_entities_parent_id'), 'ifc_entities', ['parent_id'], unique=False)
    op.create_foreign_key('fk_ifc_entities_parent_id', 'ifc_entities', 'ifc_entities', ['parent_id'], ['id'])

    # 2. Drop ifc_relationships table
    op.drop_index('ix_ifc_relationships_model_id', table_name='ifc_relationships')
    op.drop_index('ix_ifc_relationships_related_id', table_name='ifc_relationships')
    op.drop_index('ix_ifc_relationships_relating_id', table_name='ifc_relationships')
    op.drop_index('ix_ifc_relationships_type', table_name='ifc_relationships')
    op.drop_table('ifc_relationships')


def downgrade() -> None:
    # 1. Recreate ifc_relationships table
    op.create_table('ifc_relationships',
        sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), autoincrement=False, nullable=False),
        sa.Column('model_id', sqlmodel.sql.sqltypes.AutoString(), autoincrement=False, nullable=False),
        sa.Column('type', sqlmodel.sql.sqltypes.AutoString(), autoincrement=False, nullable=False),
        sa.Column('relating_id', sqlmodel.sql.sqltypes.AutoString(), autoincrement=False, nullable=False),
        sa.Column('related_id', sqlmodel.sql.sqltypes.AutoString(), autoincrement=False, nullable=False),
        sa.ForeignKeyConstraint(['model_id'], ['ifc_models.id'], name='ifc_relationships_model_id_fkey'),
        sa.PrimaryKeyConstraint('id', name='ifc_relationships_pkey')
    )
    op.create_index('ix_ifc_relationships_type', 'ifc_relationships', ['type'], unique=False)
    op.create_index('ix_ifc_relationships_relating_id', 'ifc_relationships', ['relating_id'], unique=False)
    op.create_index('ix_ifc_relationships_related_id', 'ifc_relationships', ['related_id'], unique=False)
    op.create_index('ix_ifc_relationships_model_id', 'ifc_relationships', ['model_id'], unique=False)

    # 2. Remove parent_id from ifc_entities
    op.drop_constraint('fk_ifc_entities_parent_id', 'ifc_entities', type_='foreignkey')
    op.drop_index(op.f('ix_ifc_entities_parent_id'), table_name='ifc_entities')
    op.drop_column('ifc_entities', 'parent_id')
