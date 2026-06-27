import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import requests
from dateutil import parser as date_parser

import pyGuaIA_API


FACEBOOK_QUERY_SOURCE = int(os.getenv("FACEBOOK_FEEDER_QUERY_SOURCE", "5"))
FACEBOOK_SOURCE_TYPE = int(os.getenv("FACEBOOK_FEEDER_SOURCE_TYPE", "3"))
FACEBOOK_API_BASE_URL = os.getenv("FACEBOOK_API_BASE_URL", "http://api:8080").rstrip("/")
GUAIA_CONFIG_PATH = os.getenv("GUAIA_CONFIG_PATH", "config.yml")
ID_TARGET_TYPE = os.getenv("FACEBOOK_FEEDER_TARGET_TYPES", "1,5")

TOKEN_REFRESH_INTERVAL = int(os.getenv("FACEBOOK_FEEDER_TOKEN_REFRESH_INTERVAL", "600"))
QUERY_POLL_INTERVAL = int(os.getenv("FACEBOOK_FEEDER_POLL_INTERVAL", "30"))
REQUEST_WAIT_TIMEOUT_SECONDS = int(os.getenv("FACEBOOK_FEEDER_WAIT_TIMEOUT_SECONDS", "3600"))
REQUEST_WAIT_POLL_SECONDS = int(os.getenv("FACEBOOK_FEEDER_WAIT_POLL_SECONDS", "20"))
REQUEST_RETRY_LIMIT = int(os.getenv("FACEBOOK_FEEDER_REQUEST_RETRY_LIMIT", "3"))
HTTP_TIMEOUT_SECONDS = float(os.getenv("FACEBOOK_FEEDER_HTTP_TIMEOUT_SECONDS", "15"))

PENDING = 1
PROCESSING = 2
COMPLETED = 3
COMPLETED_WITH_MISSING = 4
FAILED = 5
CANCELLED = 6

FINAL_LOCAL_STATUSES = {COMPLETED, COMPLETED_WITH_MISSING, FAILED, CANCELLED}
RESULT_BUCKETS = ("posts", "videos", "profiles", "pages", "groups", "items")


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("facebook_api_feeder")


def reconnect(api) -> bool:
    try:
        ok = api.connect()
        if ok:
            logger.info("Token JWT renovado com sucesso")
        else:
            logger.warning(
                "Falha ao renovar token | code=%s | reason=%s",
                api.getLastReturnCode(),
                api.getLastReturnReason(),
            )
        return ok
    except Exception as exc:
        logger.error("Erro ao renovar token JWT: %s", exc)
        return False


def is_auth_error(api) -> bool:
    return api.getLastReturnCode() in (401, 403)


def safe_get_unprocessed_queries(api) -> tuple[bool, list[dict[str, Any]]]:
    try:
        ret, queries = api.getUnprocessedQueries()

        if not ret and is_auth_error(api):
            logger.warning("JWT expirado ao buscar queries. Reconectando...")
            if reconnect(api):
                ret, queries = api.getUnprocessedQueries()

        return ret, queries or []

    except ValueError as exc:
        logger.warning("Resposta invalida da API GuaIA: %s", exc)
        return False, []
    except requests.exceptions.RequestException as exc:
        logger.warning("Erro HTTP com GuaIA: %s", exc)
        return False, []


def safe_set_query_status(api, id_query: int, status: int) -> bool:
    try:
        ret, _ = api.setQueryStatus(id_query, status)

        if not ret and is_auth_error(api):
            logger.warning(
                "JWT expirado ao alterar status da query %s. Reconectando...",
                id_query,
            )
            if reconnect(api):
                ret, _ = api.setQueryStatus(id_query, status)

        if not ret:
            logger.error(
                "Falha ao alterar status da query %s para %s | code=%s | reason=%s",
                id_query,
                status,
                api.getLastReturnCode(),
                api.getLastReturnReason(),
            )

        return ret

    except Exception as exc:
        logger.error("Erro ao alterar status da query %s para %s: %s", id_query, status, exc)
        return False


def safe_send_result(api, **kwargs) -> bool:
    try:
        ret, _ = api.sendQuerySingleResult(**kwargs)

        if not ret and is_auth_error(api):
            logger.warning("JWT expirado durante envio de resultado. Reconectando...")
            if reconnect(api):
                ret, _ = api.sendQuerySingleResult(**kwargs)

        if not ret:
            logger.error(
                "Falha ao enviar resultado | code=%s | reason=%s",
                api.getLastReturnCode(),
                api.getLastReturnReason(),
            )

        return ret

    except Exception as exc:
        logger.error("Erro ao enviar resultado: %s", exc)
        return False


def normalise_terms(raw: Any) -> str:
    if isinstance(raw, list):
        return " ".join(str(term).strip() for term in raw if str(term).strip())
    if raw is None:
        return ""
    return str(raw).strip()


def query_subject(query: dict[str, Any]) -> str:
    return normalise_terms(query.get("search_string") or query.get("subject"))


def is_facebook_query(query: dict[str, Any]) -> bool:
    try:
        return int(query.get("query_source")) == FACEBOOK_QUERY_SOURCE
    except (TypeError, ValueError):
        return False


def parse_optional_date(raw: Any) -> str | None:
    if raw in (None, ""):
        return None

    if hasattr(raw, "isoformat"):
        return raw.isoformat()

    try:
        parsed = date_parser.parse(str(raw))
    except Exception as exc:
        logger.warning("Nao foi possivel parsear data '%s': %s", raw, exc)
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def _facebook_api_url(path: str) -> str:
    return f"{FACEBOOK_API_BASE_URL}{path}"


def create_facebook_request(query: dict[str, Any], subject: str) -> dict[str, Any]:
    id_query = int(query["id_query"])
    payload: dict[str, Any] = {
        "id_query": id_query,
        "subject": subject,
        "status_current": PENDING,
        "query_source": FACEBOOK_QUERY_SOURCE,
    }

    start_date = parse_optional_date(query.get("start_date"))
    end_date = parse_optional_date(query.get("end_date"))

    if start_date:
        payload["start_date"] = start_date
    if end_date:
        payload["end_date"] = end_date

    response = requests.post(
        _facebook_api_url("/requests"),
        json=payload,
        timeout=HTTP_TIMEOUT_SECONDS,
    )

    if response.status_code == 201:
        logger.info("Request local Facebook criada | id_query=%s", id_query)
        return response.json()

    if response.status_code == 409:
        logger.info("Request local ja existe | id_query=%s", id_query)
        return get_facebook_request(id_query)

    logger.error(
        "Falha criando request local | id_query=%s | status=%s | body=%s",
        id_query,
        response.status_code,
        response.text[:500],
    )
    response.raise_for_status()
    return response.json()


def get_facebook_request(id_query: int) -> dict[str, Any]:
    response = requests.get(
        _facebook_api_url(f"/requests/query/{id_query}"),
        timeout=HTTP_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()


def retry_facebook_request(id_query: int) -> dict[str, Any]:
    response = requests.post(
        _facebook_api_url(f"/requests/query/{id_query}/retry"),
        timeout=HTTP_TIMEOUT_SECONDS,
    )

    if response.status_code == 409:
        logger.info("Retry nao aplicavel; reaproveitando request local | id_query=%s", id_query)
        return get_facebook_request(id_query)

    response.raise_for_status()
    logger.info("Request local reenfileirada | id_query=%s", id_query)
    return response.json()


def local_status(details: dict[str, Any]) -> int:
    return int(details.get("metadata", {}).get("status_current", PENDING))


def wait_for_facebook_request(
    id_query: int,
    initial_details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    deadline = time.monotonic() + REQUEST_WAIT_TIMEOUT_SECONDS
    details = initial_details

    while time.monotonic() < deadline:
        if details is None:
            details = get_facebook_request(id_query)

        status = local_status(details)
        logger.info("Pipeline Facebook | id_query=%s | status=%s", id_query, status)

        if status in FINAL_LOCAL_STATUSES:
            return details

        time.sleep(REQUEST_WAIT_POLL_SECONDS)
        details = None

    raise TimeoutError(f"Timeout aguardando pipeline Facebook da query {id_query}")


def wait_for_facebook_request_with_retries(initial_details: dict[str, Any]) -> dict[str, Any]:
    id_query = int(initial_details["metadata"]["id_query"])
    details = initial_details

    for attempt in range(REQUEST_RETRY_LIMIT + 1):
        details = wait_for_facebook_request(id_query, details)
        status = local_status(details)

        if status != FAILED:
            return details

        if attempt >= REQUEST_RETRY_LIMIT:
            return details

        logger.warning(
            "Pipeline local falhou; reenfileirando | id_query=%s | tentativa=%s/%s",
            id_query,
            attempt + 1,
            REQUEST_RETRY_LIMIT,
        )
        details = retry_facebook_request(id_query)

    return details


def empty_send_summary() -> dict[str, int]:
    return {"total": 0, "enviados": 0, "falhas": 0}


def merge_send_summary(target: dict[str, int], source: dict[str, int]) -> dict[str, int]:
    target["total"] += source.get("total", 0)
    target["enviados"] += source.get("enviados", 0)
    target["falhas"] += source.get("falhas", 0)
    return target


def iter_result_records(details: dict[str, Any]):
    data = details.get("data") or {}
    seen: set[str] = set()

    for bucket in RESULT_BUCKETS:
        records = data.get(bucket) or []
        for record in records:
            if not isinstance(record, dict):
                continue
            key = str(record.get("fonte") or record.get("url") or record.get("id") or id(record))
            if key in seen:
                continue
            seen.add(key)
            yield bucket, record


def result_send_key(record: dict[str, Any]) -> str:
    return str(record.get("fonte") or record.get("url") or record.get("id") or "")


def derive_primary_source_name(record: dict[str, Any], subject: str = "") -> str:
    profile = str(record.get("perfil") or "").strip()
    if profile:
        return profile

    url = str(record.get("fonte") or "").strip()
    if url:
        try:
            parsed = urlparse(url)
            parts = [part for part in parsed.path.split("/") if part]
            if parts:
                return f"Facebook:{parts[0]}"
            if parsed.netloc:
                return parsed.netloc
        except Exception:
            pass

    if subject:
        return f"Facebook (busca: {subject})"

    return "Facebook"


def publication_text(record: dict[str, Any]) -> str:
    return str(
        record.get("descricao")
        or record.get("description")
        or record.get("texto")
        or record.get("fonte")
        or ""
    )


def publication_date(record: dict[str, Any]) -> str:
    value = record.get("data") or record.get("created_at") or record.get("updated_at")
    parsed = parse_optional_date(value)
    if parsed:
        return parsed
    return datetime.now(timezone.utc).isoformat()


def int_or_zero(value: Any) -> int:
    try:
        if value in (None, ""):
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0


def create_facebook_dto(
    record: dict[str, Any],
    bucket: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id_query": metadata.get("id_query"),
        "request_id": metadata.get("request_id"),
        "subject": metadata.get("subject", ""),
        "query_source": metadata.get("query_source", FACEBOOK_QUERY_SOURCE),
        "source_type": metadata.get("source_type", FACEBOOK_SOURCE_TYPE),
        "bucket": bucket,
        "id": str(record.get("id") or ""),
        "tipo": record.get("tipo") or bucket.rstrip("s"),
        "fonte": record.get("fonte") or "",
        "status": record.get("status") or "",
        "error": record.get("error") or "",
        "data": publication_date(record),
        "perfil": record.get("perfil") or "",
        "verificado": record.get("verificado"),
        "descricao": record.get("descricao") or "",
        "num_comentarios": int_or_zero(record.get("num_comentarios")),
        "num_visualizacoes": int_or_zero(record.get("num_visualizacoes")),
        "num_reacoes": int_or_zero(record.get("num_reacoes")),
        "media_url": record.get("media_url") or "",
        "media_type": record.get("media_type") or "",
        "drive_file_id": record.get("drive_file_id") or "",
        "drive_url": record.get("drive_url") or "",
        "comments": record.get("comments") or [],
        "ultima_att": parse_optional_date(record.get("ultima_att")),
    }


def build_send_payload(
    id_query: int,
    record: dict[str, Any],
    bucket: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    subject = str(metadata.get("subject") or "")
    primary_name = derive_primary_source_name(record, subject)

    return {
        "id_query": id_query,
        "publication_date": publication_date(record),
        "publication_text": publication_text(record),
        "source_type": FACEBOOK_SOURCE_TYPE,
        "other_source_name": "",
        "primary_source_name": primary_name,
        "publication_url": record.get("fonte") or "",
        "allegation": "",
        "comment": "",
        "observation": "",
        "id_target_type": ID_TARGET_TYPE,
        "social_metadata": create_facebook_dto(record, bucket, metadata),
    }


def send_facebook_results(
    api,
    id_query: int,
    details: dict[str, Any],
    sent_keys: set[str] | None = None,
) -> dict[str, int]:
    metadata = details.get("metadata") or {}
    enviados = 0
    falhas = 0
    total = 0

    for bucket, record in iter_result_records(details):
        send_key = result_send_key(record)
        if sent_keys is not None and send_key and send_key in sent_keys:
            continue

        total += 1
        try:
            payload = build_send_payload(id_query, record, bucket, metadata)
            ok = safe_send_result(api, **payload)
            if ok:
                enviados += 1
                if sent_keys is not None and send_key:
                    sent_keys.add(send_key)
            else:
                falhas += 1
        except Exception:
            logger.exception("Erro ao processar resultado local | id_query=%s", id_query)
            falhas += 1

    if total > 0:
        logger.info(
            "Envio incremental concluido | query=%s | total_novos=%s | enviados=%s | falhas=%s",
            id_query,
            total,
            enviados,
            falhas,
        )
    return {"total": total, "enviados": enviados, "falhas": falhas}


def wait_for_facebook_request_with_incremental_sends(
    api,
    initial_details: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, int]]:
    id_query = int(initial_details["metadata"]["id_query"])
    details = initial_details
    sent_keys: set[str] = set()
    send_summary = empty_send_summary()

    for attempt in range(REQUEST_RETRY_LIMIT + 1):
        deadline = time.monotonic() + REQUEST_WAIT_TIMEOUT_SECONDS

        while time.monotonic() < deadline:
            if details is None:
                details = get_facebook_request(id_query)

            status = local_status(details)
            logger.info("Pipeline Facebook | id_query=%s | status=%s", id_query, status)
            merge_send_summary(
                send_summary,
                send_facebook_results(api, id_query, details, sent_keys=sent_keys),
            )

            if status in FINAL_LOCAL_STATUSES:
                break

            time.sleep(REQUEST_WAIT_POLL_SECONDS)
            details = None
        else:
            raise TimeoutError(f"Timeout aguardando pipeline Facebook da query {id_query}")

        status = local_status(details)
        if status != FAILED:
            return details, send_summary

        if attempt >= REQUEST_RETRY_LIMIT:
            return details, send_summary

        logger.warning(
            "Pipeline local falhou; reenfileirando | id_query=%s | tentativa=%s/%s",
            id_query,
            attempt + 1,
            REQUEST_RETRY_LIMIT,
        )
        details = retry_facebook_request(id_query)

    return details, send_summary


def final_guaia_status(local_final_status: int, send_summary: dict[str, int]) -> int:
    del local_final_status, send_summary
    return COMPLETED


def process_query(api, query: dict[str, Any]) -> None:
    id_query = int(query["id_query"])
    subject = query_subject(query)

    logger.info("Processando consulta Facebook | id_query=%s | subject='%s'", id_query, subject)

    if not subject:
        logger.error("Consulta %s sem termo de busca", id_query)
        safe_set_query_status(api, id_query, COMPLETED)
        return

    if not safe_set_query_status(api, id_query, PROCESSING):
        return

    try:
        initial_details = create_facebook_request(query, subject)
        details, send_summary = wait_for_facebook_request_with_incremental_sends(api, initial_details)
    except Exception:
        logger.exception("Falha executando pipeline local Facebook | id_query=%s", id_query)
        safe_set_query_status(api, id_query, COMPLETED)
        return

    local_final_status = local_status(details)

    if local_final_status in {FAILED, CANCELLED}:
        logger.error("Pipeline local finalizou sem sucesso | id_query=%s | status=%s", id_query, local_final_status)
        safe_set_query_status(api, id_query, COMPLETED)
        return

    status = final_guaia_status(local_final_status, send_summary)

    safe_set_query_status(api, id_query, status)
    logger.info("Consulta Facebook finalizada | id_query=%s | status_guaia=%s", id_query, status)


def get_guaia_connection():
    try:
        api = pyGuaIA_API.GuaIA_API(GUAIA_CONFIG_PATH)
        logger.info("Conexao com GuaIA inicializada")
        return api
    except SystemExit:
        logger.exception("Config GuaIA indisponivel: %s", GUAIA_CONFIG_PATH)
        return None
    except Exception:
        logger.exception("Erro ao conectar a API GuaIA")
        return None


def main() -> int:
    logger.info(
        "Iniciando integracao GuaIA -> Facebook | query_source=%s | api=%s",
        FACEBOOK_QUERY_SOURCE,
        FACEBOOK_API_BASE_URL,
    )

    api = get_guaia_connection()
    if not api:
        return 1

    last_token_refresh = time.time()

    while True:
        try:
            if time.time() - last_token_refresh >= TOKEN_REFRESH_INTERVAL:
                reconnect(api)
                last_token_refresh = time.time()

            ret, queries = safe_get_unprocessed_queries(api)

            if not ret:
                logger.warning("Falha ao buscar queries pendentes")
                time.sleep(QUERY_POLL_INTERVAL)
                continue

            if not queries:
                logger.info("Nenhuma query pendente")
                time.sleep(QUERY_POLL_INTERVAL)
                continue

            logger.info("Total de queries pendentes: %s", len(queries))

            for query in queries:
                if not is_facebook_query(query):
                    continue

                try:
                    process_query(api, query)
                except Exception:
                    logger.exception("Erro processando query %s", query.get("id_query"))
                    if query.get("id_query") is not None:
                        safe_set_query_status(api, int(query["id_query"]), COMPLETED)

            time.sleep(QUERY_POLL_INTERVAL)

        except KeyboardInterrupt:
            logger.info("Encerrando feeder Facebook")
            return 0
        except Exception:
            logger.exception("Erro geral no loop principal")
            time.sleep(30)


if __name__ == "__main__":
    sys.exit(main())
