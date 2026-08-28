"""enforce one active original transfer per presentation version

Revision ID: 2f6c1e9a4b70
Revises: fe31b5d9e012
"""

import sqlalchemy as sa
from alembic import op

revision = "2f6c1e9a4b70"
down_revision = "fe31b5d9e012"
branch_labels = None
depends_on = None

_ACTIVE = "('queued','available','transferring','retry_wait','verifying')"


def upgrade() -> None:
    # Preserve every historical row for audit, but terminalize all except the newest received
    # intent before installing the active/current invariant.
    op.execute(
        f"""
        WITH ranked AS (
            SELECT transfer_session_id,
                   row_number() OVER (
                       PARTITION BY presentation_version_id
                       ORDER BY created_at DESC, transfer_session_id DESC
                   ) AS desired_rank
            FROM media_transfer_sessions
            WHERE state IN {_ACTIVE}
        )
        UPDATE media_transfer_sessions AS sessions
        SET state='cancelled',
            error_detail='Superseded while enforcing one desired original transfer.',
            last_progress_at=now(),
            updated_at=now()
        FROM ranked
        WHERE sessions.transfer_session_id=ranked.transfer_session_id
          AND ranked.desired_rank > 1
        """
    )
    op.execute(
        """
        UPDATE transfer_jobs AS jobs
        SET status='cancelled',
            required_capabilities='[]'::jsonb,
            claimed_by_worker_id=NULL,
            lease_expires_at=NULL,
            heartbeat_at=NULL,
            completed_at=now(),
            error_code='superseded_original',
            last_error='Superseded while enforcing one desired original transfer.',
            updated_at=now()
        FROM media_transfer_sessions AS sessions
        WHERE jobs.transfer_job_id=sessions.transfer_session_id
          AND sessions.state='cancelled'
          AND jobs.transfer_type='presentation_media.central_pull'
          AND jobs.status <> 'cancelled'
        """
    )
    op.create_index(
        "uq_site_media_transfer_active_original_version",
        "media_transfer_sessions",
        ["presentation_version_id"],
        unique=True,
        postgresql_where=sa.text(
            "state IN ('queued','available','transferring','retry_wait','verifying')"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_site_media_transfer_active_original_version",
        table_name="media_transfer_sessions",
    )
