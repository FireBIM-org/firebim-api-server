"""Register ready immutable graph revisions at the ingestion transaction boundary."""

import sqlalchemy as sa

from alembic import op

revision = "9d0e1f2a3b4c"
down_revision = "8c9d0e1f2a3b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "published_graphs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("graph_uri", sa.String(), nullable=False, unique=True),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("model_id", sa.String(), sa.ForeignKey("ifc_models.id"), nullable=False),
        sa.Column("job_id", sa.String(), sa.ForeignKey("ingestion_jobs.id"), nullable=False, unique=True),
        *(
            sa.Column(name, sa.String(), nullable=False)
            for name in (
                "backend",
                "status",
                "profile_id",
                "revision_id",
                "revision_digest",
                "input_sha256",
                "runtime_fingerprint",
                "execution_fingerprint",
            )
        ),
        *(
            sa.Column(name, sa.Integer(), nullable=False)
            for name in (
                "fencing_token",
                "attempt_number",
                "triple_count",
            )
        ),
        *(
            sa.Column(name, sa.JSON(), nullable=False)
            for name in (
                "destination",
                "provenance",
                "ontology_artifacts",
                "manifest",
                "report",
            )
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("user_id", "model_id"):
        op.create_index(f"ix_published_graphs_{column}", "published_graphs", [column])


def downgrade() -> None:
    op.drop_table("published_graphs")
