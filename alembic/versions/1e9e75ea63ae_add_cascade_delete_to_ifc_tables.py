"""Add cascade delete to IFC tables

Revision ID: 1e9e75ea63ae
Revises: d53d96a08ad4
Create Date: 2026-06-04 14:58:16.251510

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1e9e75ea63ae'
down_revision: Union[str, None] = 'd53d96a08ad4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. check_results -> ifc_models
    op.execute("ALTER TABLE check_results DROP CONSTRAINT IF EXISTS check_results_ifc_model_id_fkey")
    op.create_foreign_key('check_results_ifc_model_id_fkey', 'check_results', 'ifc_models', ['ifc_model_id'], ['id'], ondelete='CASCADE')

    # 2. ifc_entities -> ifc_models
    op.execute("ALTER TABLE ifc_entities DROP CONSTRAINT IF EXISTS ifc_entities_model_id_fkey")
    op.create_foreign_key('ifc_entities_model_id_fkey', 'ifc_entities', 'ifc_models', ['model_id'], ['id'], ondelete='CASCADE')

    # 3. ifc_entities -> ifc_entities (parent_id)
    op.execute("ALTER TABLE ifc_entities DROP CONSTRAINT IF EXISTS fk_ifc_entities_parent_id")
    op.execute("ALTER TABLE ifc_entities DROP CONSTRAINT IF EXISTS ifc_entities_parent_id_fkey")
    op.create_foreign_key('fk_ifc_entities_parent_id', 'ifc_entities', 'ifc_entities', ['parent_id'], ['global_id'], ondelete='CASCADE')

    # 4. ifc_properties -> ifc_entities
    op.execute("ALTER TABLE ifc_properties DROP CONSTRAINT IF EXISTS ifc_properties_entity_id_fkey")
    op.create_foreign_key('ifc_properties_entity_id_fkey', 'ifc_properties', 'ifc_entities', ['entity_id'], ['global_id'], ondelete='CASCADE')

    # 5. ifc_quantities -> ifc_entities
    op.execute("ALTER TABLE ifc_quantities DROP CONSTRAINT IF EXISTS ifc_quantities_entity_id_fkey")
    op.create_foreign_key('ifc_quantities_entity_id_fkey', 'ifc_quantities', 'ifc_entities', ['entity_id'], ['global_id'], ondelete='CASCADE')


def downgrade() -> None:
    # Revert to original foreign keys (without ON DELETE CASCADE)
    # Note: Target columns for entity_id might have been 'id' in older migrations,
    # but we follow the current 'global_id' PK from models.py

    op.drop_constraint('check_results_ifc_model_id_fkey', 'check_results', type_='foreignkey')
    op.create_foreign_key('check_results_ifc_model_id_fkey', 'check_results', 'ifc_models', ['ifc_model_id'], ['id'])

    op.drop_constraint('ifc_entities_model_id_fkey', 'ifc_entities', type_='foreignkey')
    op.create_foreign_key('ifc_entities_model_id_fkey', 'ifc_entities', 'ifc_models', ['model_id'], ['id'])

    op.drop_constraint('fk_ifc_entities_parent_id', 'ifc_entities', type_='foreignkey')
    op.create_foreign_key('fk_ifc_entities_parent_id', 'ifc_entities', 'ifc_entities', ['parent_id'], ['global_id'])

    op.drop_constraint('ifc_properties_entity_id_fkey', 'ifc_properties', type_='foreignkey')
    op.create_foreign_key('ifc_properties_entity_id_fkey', 'ifc_properties', 'ifc_entities', ['entity_id'], ['global_id'])

    op.drop_constraint('ifc_quantities_entity_id_fkey', 'ifc_quantities', type_='foreignkey')
    op.create_foreign_key('ifc_quantities_entity_id_fkey', 'ifc_quantities', 'ifc_entities', ['entity_id'], ['global_id'])
