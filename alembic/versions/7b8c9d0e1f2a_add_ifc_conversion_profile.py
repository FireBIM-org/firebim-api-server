"""Add IFC conversion profile metadata.

Revision ID: 7b8c9d0e1f2a
Revises: 1e9e75ea63ae
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "7b8c9d0e1f2a"
down_revision: Union[str, None] = "1e9e75ea63ae"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ifc_models", sa.Column("conversion_profile_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.add_column("ifc_models", sa.Column("conversion_profile_name", sqlmodel.sql.sqltypes.AutoString(), nullable=True))


def downgrade() -> None:
    op.drop_column("ifc_models", "conversion_profile_name")
    op.drop_column("ifc_models", "conversion_profile_id")
