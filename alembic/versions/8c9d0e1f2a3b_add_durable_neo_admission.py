"""Add revision-bound uploads, ingestion jobs, attempts and transactional outbox.

Revision ID: 8c9d0e1f2a3b
Revises: 7b8c9d0e1f2a
"""

import sqlalchemy as sa
from alembic import op

revision = "8c9d0e1f2a3b"
down_revision = "7b8c9d0e1f2a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ifc_models", sa.Column("ingestion_backend", sa.String(), nullable=False, server_default="legacy"))
    for name in (
        "latest_job_id",
        "latest_successful_job_id",
        "latest_successful_graph_id",
        "latest_successful_revision_id",
    ):
        op.add_column("ifc_models", sa.Column(name, sa.String(), nullable=True))
    op.add_column("ifc_models", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "ifc_upload_sessions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("request_id", sa.String(), nullable=False),
        sa.Column("request_digest", sa.String(), nullable=False),
        sa.Column("model_id", sa.String(), nullable=False, unique=True),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("staging_key", sa.String(), nullable=False, unique=True),
        sa.Column("expected_size", sa.Integer(), nullable=False),
        sa.Column("expected_sha256", sa.String(), nullable=False),
        sa.Column("profile_id", sa.String(), nullable=False),
        sa.Column("revision_id", sa.String(), nullable=False),
        sa.Column("revision_digest", sa.String(), nullable=False),
        sa.Column("revision_snapshot", sa.JSON(), nullable=False),
        sa.Column("runtime_fingerprint", sa.String(), nullable=False),
        sa.Column("runtime_config", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("job_id", sa.String(), nullable=True, unique=True),
        sa.Column("raw_key", sa.String(), nullable=True),
        sa.Column("raw_sha256", sa.String(), nullable=True),
        sa.Column("raw_size", sa.Integer(), nullable=True),
        sa.Column("source_schema", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "request_id", name="uq_upload_owner_request"),
    )
    op.create_table(
        "ingestion_jobs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("model_id", sa.String(), sa.ForeignKey("ifc_models.id"), nullable=False),
        sa.Column("upload_session_id", sa.String(), sa.ForeignKey("ifc_upload_sessions.id"), nullable=False),
        sa.Column("request_id", sa.String(), nullable=False),
        sa.Column("request_digest", sa.String(), nullable=False),
        sa.Column("raw_key", sa.String(), nullable=False),
        sa.Column("raw_sha256", sa.String(), nullable=False),
        sa.Column("raw_size", sa.Integer(), nullable=False),
        sa.Column("source_schema", sa.String(), nullable=False),
        sa.Column("profile_id", sa.String(), nullable=False),
        sa.Column("revision_id", sa.String(), nullable=False),
        sa.Column("revision_digest", sa.String(), nullable=False),
        sa.Column("revision_snapshot", sa.JSON(), nullable=False),
        sa.Column("runtime_fingerprint", sa.String(), nullable=False),
        sa.Column("runtime_config", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("stage", sa.String(), nullable=False),
        sa.Column("dispatch_generation", sa.Integer(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("fencing_token", sa.Integer(), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancellation_requested", sa.Boolean(), nullable=False),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("result_graph_id", sa.String(), nullable=True),
        sa.Column("report", sa.JSON(), nullable=False),
        sa.Column("checkpoints", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "request_id", name="uq_ingestion_owner_request"),
    )
    op.create_table(
        "ingestion_attempts",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("job_id", sa.String(), sa.ForeignKey("ingestion_jobs.id"), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("fencing_token", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("stage", sa.String(), nullable=False),
        sa.Column("runtime_fingerprint", sa.String(), nullable=False),
        sa.Column("diagnostics", sa.JSON(), nullable=False),
        sa.Column("artifacts", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("job_id", "number", name="uq_ingestion_attempt_number"),
    )
    op.create_table(
        "ingestion_outbox",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("job_id", sa.String(), sa.ForeignKey("ingestion_jobs.id"), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(), nullable=False),
        sa.Column("task_name", sa.String(), nullable=False),
        sa.Column("dispatch_attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.UniqueConstraint("job_id", "generation", name="uq_ingestion_outbox_generation"),
        sa.UniqueConstraint("job_id", "request_id", name="uq_ingestion_outbox_request"),
    )
    for table, columns in {
        "ifc_upload_sessions": ("user_id",),
        "ingestion_jobs": ("user_id", "model_id", "status"),
        "ingestion_attempts": ("job_id",),
        "ingestion_outbox": ("job_id", "available_at", "delivered_at"),
    }.items():
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])
    op.create_index(
        "uq_ingestion_active_model",
        "ingestion_jobs",
        ["model_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running', 'retry_wait')"),
        sqlite_where=sa.text("status IN ('queued', 'running', 'retry_wait')"),
    )


def downgrade() -> None:
    for table in ("ingestion_outbox", "ingestion_attempts", "ingestion_jobs", "ifc_upload_sessions"):
        op.drop_table(table)
    for name in (
        "deleted_at",
        "latest_successful_revision_id",
        "latest_successful_graph_id",
        "latest_successful_job_id",
        "latest_job_id",
        "ingestion_backend",
    ):
        op.drop_column("ifc_models", name)
