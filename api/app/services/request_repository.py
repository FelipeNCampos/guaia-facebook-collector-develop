from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg import connect
from psycopg.rows import dict_row
from psycopg.types.json import Json

from app_shared.request_status import (
    FACEBOOK_QUERY_SOURCE,
    FACEBOOK_SOURCE_TYPE,
    RequestStatusCode,
    legacy_status_from_current,
)


class RequestRepository:
    def __init__(self, db_dsn: str):
        self.db_dsn = db_dsn

    def create_request_for_ip(
        self,
        *,
        ip_request: str,
        id_query: int,
        subject: str,
        start_date: datetime | None,
        end_date: datetime | None,
        status_current: RequestStatusCode,
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        filters = self._build_legacy_filters(start_date, end_date)
        legacy_status = legacy_status_from_current(status_current, phase="received")

        with connect(self.db_dsn, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO usuarios (ip_request, numero_requests)
                    VALUES (%s, 1)
                    ON CONFLICT (ip_request)
                    DO UPDATE SET
                      numero_requests = usuarios.numero_requests + 1,
                      updated_at = NOW()
                    RETURNING id, ip_request, numero_requests, created_at, updated_at;
                    """,
                    (ip_request,),
                )
                user_row = cursor.fetchone()
                cursor.execute(
                    """
                    INSERT INTO requests (
                      user_id,
                      id_query,
                      subject,
                      start_date,
                      end_date,
                      status_current,
                      query_source,
                      source_type,
                      termo,
                      filters,
                      payload,
                      status
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING
                      id,
                      user_id,
                      id_query,
                      subject,
                      start_date,
                      end_date,
                      status_current,
                      query_source,
                      source_type,
                      status,
                      error,
                      created_at,
                      updated_at;
                    """,
                    (
                        user_row["id"],
                        id_query,
                        subject,
                        start_date,
                        end_date,
                        int(status_current),
                        FACEBOOK_QUERY_SOURCE,
                        FACEBOOK_SOURCE_TYPE,
                        subject,
                        Json(filters),
                        Json(payload),
                        legacy_status,
                    ),
                )
                request_row = cursor.fetchone()
            connection.commit()
        return user_row, request_row

    def update_request_status(
        self,
        request_id: UUID,
        status_current: RequestStatusCode | int,
        *,
        error: str | None = None,
        legacy_status: str | None = None,
    ) -> dict[str, Any]:
        current = RequestStatusCode(int(status_current))
        resolved_legacy_status = legacy_status or legacy_status_from_current(current, error=error)

        with connect(self.db_dsn, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE requests
                    SET status_current = %s,
                        status = %s,
                        error = %s,
                        updated_at = NOW()
                    WHERE id = %s
                    RETURNING
                      id,
                      user_id,
                      id_query,
                      subject,
                      start_date,
                      end_date,
                      status_current,
                      query_source,
                      source_type,
                      status,
                      error,
                      created_at,
                      updated_at;
                    """,
                    (int(current), resolved_legacy_status, error, request_id),
                )
                request_row = cursor.fetchone()
            connection.commit()
        return request_row

    def get_request_metadata(self, request_id: UUID) -> dict[str, Any] | None:
        return self._get_request_metadata("r.id = %s", (request_id,))

    def get_request_metadata_by_query(self, id_query: int) -> dict[str, Any] | None:
        return self._get_request_metadata("r.id_query = %s", (id_query,))

    def requeue_failed_request_by_query(
        self,
        id_query: int,
        *,
        max_retries: int,
    ) -> dict[str, Any] | None:
        with connect(self.db_dsn, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                      r.id,
                      r.user_id,
                      r.id_query,
                      r.subject,
                      r.start_date,
                      r.end_date,
                      r.status_current,
                      r.query_source,
                      r.source_type,
                      r.status,
                      r.error,
                      r.payload,
                      r.created_at,
                      r.updated_at,
                      u.ip_request,
                      u.numero_requests
                    FROM requests r
                    INNER JOIN usuarios u ON u.id = r.user_id
                    WHERE r.id_query = %s
                    FOR UPDATE;
                    """,
                    (id_query,),
                )
                request_row = cursor.fetchone()
                if request_row is None:
                    return None

                retry_count = self._parse_request_retry_count(request_row.get("payload"))
                if int(request_row["status_current"]) != int(RequestStatusCode.FAILED):
                    return request_row
                if retry_count >= max_retries:
                    return request_row

                next_retry_count = retry_count + 1
                cursor.execute(
                    """
                    DELETE FROM request_links
                    WHERE request_id = %s;
                    """,
                    (request_row["id"],),
                )
                cursor.execute(
                    """
                    UPDATE requests
                    SET status_current = %s,
                        status = %s,
                        error = NULL,
                        payload = jsonb_set(
                          jsonb_set(
                            COALESCE(payload, '{}'::jsonb),
                            '{request_retry_attempts}',
                            to_jsonb(%s::int),
                            true
                          ),
                          '{worker_attempts}',
                          '0'::jsonb,
                          true
                        ),
                        updated_at = NOW()
                    WHERE id = %s
                    RETURNING
                      id,
                      user_id,
                      id_query,
                      subject,
                      start_date,
                      end_date,
                      status_current,
                      query_source,
                      source_type,
                      status,
                      error,
                      payload,
                      created_at,
                      updated_at;
                    """,
                    (
                        int(RequestStatusCode.PENDING),
                        legacy_status_from_current(RequestStatusCode.PENDING, phase="received"),
                        next_retry_count,
                        request_row["id"],
                    ),
                )
                updated_row = cursor.fetchone()
            connection.commit()
        return {**updated_row, "ip_request": request_row["ip_request"], "numero_requests": request_row["numero_requests"]}

    def get_request_data(self, request_id: UUID) -> dict[str, list[dict[str, Any]]]:
        data = self._empty_data()

        with connect(self.db_dsn, row_factory=dict_row) as connection:
            profile_records = self._fetch_domain_records(connection, request_id, "perfil", ("perfil",))
            video_records = self._fetch_domain_records(connection, request_id, "video", ("video",))
            video_records.extend(self._fetch_domain_records(connection, request_id, "reel", ("reel",)))
            post_records = self._fetch_domain_records(connection, request_id, "post", ("post",))
            post_records.extend(self._fetch_domain_records(connection, request_id, "storie", ("storie",)))
            post_records.extend(self._fetch_domain_records(connection, request_id, "foto", ("foto",)))
            post_records.extend(self._fetch_domain_records(connection, request_id, "permalink", ("permalink",)))

            data["profiles"] = profile_records
            data["videos"] = video_records
            data["posts"] = post_records

            joined_sources = {
                record["fonte"]
                for record in [*profile_records, *video_records, *post_records]
            }
            data["items"] = self._fetch_fallback_items(connection, request_id, joined_sources)

        return data

    def _get_request_metadata(self, where_clause: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
        with connect(self.db_dsn, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT
                      r.id AS request_id,
                      r.id_query,
                      r.user_id,
                      u.ip_request,
                      u.numero_requests,
                      r.subject,
                      r.query_source,
                      r.source_type,
                      r.status_current,
                      r.created_at,
                      r.updated_at,
                      r.start_date,
                      r.end_date
                    FROM requests r
                    INNER JOIN usuarios u ON u.id = r.user_id
                    WHERE {where_clause};
                    """,
                    params,
                )
                return cursor.fetchone()

    def _fetch_domain_records(
        self,
        connection,
        request_id: UUID,
        table_name: str,
        request_link_types: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        type_placeholders = ", ".join(["%s"] * len(request_link_types))
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT t.*, rl.tipo
                FROM request_links rl
                INNER JOIN {table_name} t ON t.fonte = rl.fonte
                WHERE rl.request_id = %s
                  AND rl.status = 'finished'
                  AND rl.tipo IN ({type_placeholders})
                ORDER BY rl.created_at ASC;
                """,
                (request_id, *request_link_types),
            )
            records = [self._serialize_domain_record(row) for row in cursor.fetchall()]

        for record in records:
            record["comments"] = self._fetch_comments_for_source(connection, record.get("fonte") or "")
        return records

    def _fetch_comments_for_source(self, connection, source_url: str) -> list[dict[str, Any]]:
        if not source_url:
            return []
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                  comment_id,
                  author_name,
                  author_id,
                  content,
                  likes,
                  created_at,
                  collected_at
                FROM facebook_comments
                WHERE source_url = %s
                ORDER BY collected_at ASC;
                """,
                (source_url,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def _fetch_fallback_items(
        self,
        connection,
        request_id: UUID,
        joined_sources: set[str],
    ) -> list[dict[str, Any]]:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT tipo, fonte, perfil_slug, status, error, created_at, updated_at
                FROM request_links
                WHERE request_id = %s
                  AND status = 'finished'
                ORDER BY created_at ASC;
                """,
                (request_id,),
            )
            rows = cursor.fetchall()

        fallback_items = []
        for row in rows:
            if row["fonte"] in joined_sources:
                continue
            # `items` is a compatibility bucket for finalized records that do not
            # yet have a dedicated typed projection in the public response.
            fallback_items.append(
                {
                    "tipo": row["tipo"],
                    "fonte": row["fonte"],
                    "perfil": row["perfil_slug"] or "",
                    "status": row["status"],
                    "error": row["error"] or "",
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            )
        return fallback_items

    def _serialize_domain_record(self, row: dict[str, Any]) -> dict[str, Any]:
        serialized = dict(row)
        serialized["tipo"] = row["tipo"]
        return serialized

    def _build_legacy_filters(
        self,
        start_date: datetime | None,
        end_date: datetime | None,
    ) -> dict[str, Any]:
        filters: dict[str, Any] = {}
        if start_date is not None:
            filters["after"] = start_date.isoformat()
        if end_date is not None:
            filters["before"] = end_date.isoformat()
        return filters

    def _parse_request_retry_count(self, payload) -> int:
        if not isinstance(payload, dict):
            return 0
        try:
            return int(payload.get("request_retry_attempts") or 0)
        except (TypeError, ValueError):
            return 0

    def _empty_data(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "profiles": [],
            "posts": [],
            "videos": [],
            "pages": [],
            "groups": [],
            "items": [],
        }
