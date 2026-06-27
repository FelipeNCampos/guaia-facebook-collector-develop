from uuid import UUID

from psycopg import connect
from psycopg.rows import dict_row

from app_shared.request_status import RequestStatusCode, legacy_status_from_current


class GoogleRunnerRepository:
    def __init__(self, db_dsn: str):
        self.db_dsn = db_dsn

    def update_request_status(
        self,
        request_id: UUID,
        status_current: RequestStatusCode | int,
        error: str | None = None,
        *,
        legacy_status: str | None = None,
    ) -> None:
        current = RequestStatusCode(int(status_current))
        resolved_legacy_status = legacy_status or legacy_status_from_current(current, error=error)

        with connect(self.db_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE requests
                    SET status_current = %s,
                        status = %s,
                        error = %s,
                        updated_at = NOW()
                    WHERE id = %s;
                    """,
                    (int(current), resolved_legacy_status, error, request_id),
                )
            connection.commit()

    def claim_pending_requests(self, limit: int = 1) -> list[dict]:
        with connect(self.db_dsn, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    WITH next_requests AS (
                        SELECT id
                        FROM requests
                        WHERE status_current = %s
                          AND query_source = %s
                          AND source_type = %s
                        ORDER BY updated_at ASC, created_at ASC
                        LIMIT %s
                        FOR UPDATE SKIP LOCKED
                    )
                    UPDATE requests r
                    SET status_current = %s,
                        status = %s,
                        error = NULL,
                        payload = jsonb_set(
                            COALESCE(r.payload, '{}'::jsonb),
                            '{worker_attempts}',
                            to_jsonb(
                                CASE
                                    WHEN COALESCE(r.payload->>'worker_attempts', '') ~ '^[0-9]+$'
                                        THEN (r.payload->>'worker_attempts')::int + 1
                                    ELSE 1
                                END
                            ),
                            true
                        ),
                        updated_at = NOW()
                    FROM next_requests n
                    WHERE r.id = n.id
                    RETURNING
                        r.id,
                        r.user_id,
                        r.id_query,
                        r.subject,
                        r.start_date,
                        r.end_date,
                        r.status_current,
                        r.query_source,
                        r.source_type,
                        r.payload;
                    """,
                    (
                        int(RequestStatusCode.PENDING),
                        5,
                        3,
                        limit,
                        int(RequestStatusCode.PROCESSING),
                        legacy_status_from_current(RequestStatusCode.PROCESSING),
                    ),
                )
                rows = [dict(row) for row in cursor.fetchall()]
            connection.commit()
        return rows

    def upsert_request_link(
        self,
        request_id: UUID,
        tipo: str,
        fonte: str,
        perfil_slug: str,
        status: str,
        error: str,
    ) -> None:
        with connect(self.db_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO request_links (request_id, tipo, fonte, perfil_slug, status, error)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (request_id, fonte)
                    DO UPDATE SET
                      tipo = EXCLUDED.tipo,
                      perfil_slug = EXCLUDED.perfil_slug,
                      status = EXCLUDED.status,
                      error = EXCLUDED.error,
                      updated_at = NOW();
                    """,
                    (request_id, tipo, fonte, perfil_slug, status, error),
                )
            connection.commit()

    def update_request_link_status(
        self,
        request_id: UUID,
        fonte: str,
        status: str,
        error: str,
    ) -> None:
        with connect(self.db_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE request_links
                    SET status = %s,
                        error = %s,
                        updated_at = NOW()
                    WHERE request_id = %s AND fonte = %s;
                    """,
                    (status, error, request_id, fonte),
                )
            connection.commit()
