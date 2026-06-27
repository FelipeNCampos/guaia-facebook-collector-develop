from datetime import datetime
from uuid import UUID

from psycopg import connect
from psycopg.rows import dict_row

from app_shared.request_status import RequestStatusCode, legacy_status_from_current

CONTENT_COLUMNS = (
    "fonte",
    "status",
    "error",
    "data",
    "perfil",
    "verificado",
    "descricao",
    "num_comentarios",
    "num_visualizacoes",
    "num_reacoes",
    "media_url",
    "media_type",
    "drive_file_id",
    "drive_url",
)

TABLE_COLUMNS = {
    "perfil": ("fonte", "status", "error", "data", "perfil", "verificado"),
    "video": CONTENT_COLUMNS,
    "reel": CONTENT_COLUMNS,
    "post": CONTENT_COLUMNS,
    "storie": CONTENT_COLUMNS,
    "foto": CONTENT_COLUMNS,
    "permalink": CONTENT_COLUMNS,
}


class FacebookRunnerRepository:
    def __init__(self, db_dsn: str):
        self.db_dsn = db_dsn

    def update_request_link_status(self, request_id: UUID, fonte: str, status: str, error: str = "") -> None:
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

    def claim_queued_links(self, limit: int = 1) -> list[dict]:
        with connect(self.db_dsn, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    WITH next_links AS (
                        SELECT
                          rl.id,
                          rl.request_id,
                          r.user_id,
                          r.id_query,
                          rl.tipo,
                          rl.fonte,
                          rl.perfil_slug,
                          rl.attempt_count
                        FROM request_links rl
                        INNER JOIN requests r ON r.id = rl.request_id
                        WHERE rl.status = 'queued'
                        ORDER BY rl.updated_at ASC, rl.created_at ASC
                        LIMIT %s
                        FOR UPDATE SKIP LOCKED
                    )
                    UPDATE request_links rl
                    SET status = 'processing',
                        error = '',
                        attempt_count = next_links.attempt_count + 1,
                        updated_at = NOW()
                    FROM next_links
                    WHERE rl.id = next_links.id
                    RETURNING
                      next_links.request_id,
                      next_links.user_id,
                      next_links.id_query,
                      next_links.tipo,
                      next_links.fonte,
                      next_links.perfil_slug,
                      rl.attempt_count;
                    """,
                    (limit,),
                )
                rows = [dict(row) for row in cursor.fetchall()]
            connection.commit()
        return rows

    def requeue_request_link(self, request_id: UUID, fonte: str, error: str = "") -> None:
        self.update_request_link_status(request_id, fonte, "queued", error)

    def upsert_domain_record(self, runner_type: str, record: dict) -> None:
        columns = TABLE_COLUMNS[runner_type]
        placeholders = ", ".join(["%s"] * len(columns))
        update_columns = ", ".join(
            f"{column_name} = EXCLUDED.{column_name}" for column_name in columns if column_name != "fonte"
        )
        sql = f"""
            INSERT INTO {runner_type} ({", ".join(columns)})
            VALUES ({placeholders})
            ON CONFLICT (fonte)
            DO UPDATE SET
              {update_columns},
              ultima_att = NOW();
        """
        values = [self._normalize_value(column_name, record.get(column_name)) for column_name in columns]

        with connect(self.db_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, values)
            connection.commit()

    def upsert_comments(self, runner_type: str, record: dict) -> None:
        comments = record.get("_comments") or []
        source_url = record.get("fonte") or ""
        if not comments or not source_url:
            return

        rows = []
        for comment in comments:
            content = comment.get("content") or comment.get("text") or ""
            comment_id = comment.get("comment_id") or ""
            if not content or not comment_id:
                continue
            rows.append(
                (
                    source_url,
                    runner_type,
                    comment_id,
                    comment.get("author_name") or comment.get("author") or "",
                    comment.get("author_id") or "",
                    content,
                    self._normalize_value("num_reacoes", comment.get("likes")),
                    self._parse_timestamp(comment.get("created_at")),
                )
            )

        if not rows:
            return

        with connect(self.db_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.executemany(
                    """
                    INSERT INTO facebook_comments (
                      source_url,
                      runner_type,
                      comment_id,
                      author_name,
                      author_id,
                      content,
                      likes,
                      created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (source_url, comment_id)
                    DO UPDATE SET
                      author_name = EXCLUDED.author_name,
                      author_id = EXCLUDED.author_id,
                      content = EXCLUDED.content,
                      likes = EXCLUDED.likes,
                      created_at = EXCLUDED.created_at,
                      collected_at = NOW();
                    """,
                    rows,
                )
            connection.commit()

    def refresh_request_status(self, request_id: UUID) -> None:
        with connect(self.db_dsn, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                      COUNT(*) FILTER (WHERE status IN ('queued', 'processing')) AS pending_count,
                      COUNT(*) FILTER (WHERE status = 'finished') AS finished_count,
                      COUNT(*) FILTER (WHERE status = 'failed') AS failed_count
                    FROM request_links
                    WHERE request_id = %s;
                    """,
                    (request_id,),
                )
                counts = cursor.fetchone()

                status_current, request_error = self._resolve_request_status(counts)
                if status_current == RequestStatusCode.PROCESSING:
                    cursor.execute(
                        """
                        UPDATE requests
                        SET status_current = %s,
                            status = %s,
                            updated_at = NOW()
                        WHERE id = %s;
                        """,
                        (
                            int(status_current),
                            legacy_status_from_current(status_current),
                            request_id,
                        ),
                    )
                else:
                    cursor.execute(
                        """
                        UPDATE requests
                        SET status_current = %s,
                            status = %s,
                            error = %s,
                            updated_at = NOW()
                        WHERE id = %s;
                        """,
                        (
                            int(status_current),
                            legacy_status_from_current(status_current, error=request_error),
                            request_error,
                            request_id,
                        ),
                    )
            connection.commit()

    def _resolve_request_status(self, counts: dict[str, int]) -> tuple[RequestStatusCode, str | None]:
        pending_count = counts["pending_count"] or 0
        finished_count = counts["finished_count"] or 0
        failed_count = counts["failed_count"] or 0

        if pending_count > 0:
            return RequestStatusCode.PROCESSING, None
        if finished_count > 0 and failed_count == 0:
            return RequestStatusCode.COMPLETED, None
        if finished_count > 0 and failed_count > 0:
            return RequestStatusCode.COMPLETED, "partial_results"
        if failed_count > 0:
            return RequestStatusCode.COMPLETED, "all_items_failed"
        return RequestStatusCode.PROCESSING, None

    def _normalize_value(self, column_name: str, value):
        if column_name == "data":
            return self._parse_timestamp(value)
        if column_name == "verificado":
            return self._parse_boolean(value)
        if column_name in {"num_comentarios", "num_visualizacoes", "num_reacoes"}:
            if value in ("", None):
                return None
            return int(value)
        if column_name in {"error", "perfil", "descricao", "media_url", "media_type", "drive_file_id", "drive_url"}:
            return value or ""
        return value

    def _parse_boolean(self, value):
        if value in ("", None):
            return None
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().lower()
        if normalized in {"true", "1", "yes", "sim"}:
            return True
        if normalized in {"false", "0", "no", "nao", "nÃ£o"}:
            return False
        return None

    def _parse_timestamp(self, value):
        if value in ("", None):
            return None
        if isinstance(value, datetime):
            return value
        for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(str(value), pattern)
            except ValueError:
                continue
        return None
