from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

try:
    import pika
    from pika.spec import BasicProperties
except ImportError:  # pragma: no cover - legacy RabbitMQ support is optional.
    pika = None
    BasicProperties = Any

from app_shared.config import Settings

CONSUMER_HEARTBEAT_SECONDS = 1800
CONSUMER_BLOCKED_CONNECTION_TIMEOUT_SECONDS = 1800

FACEBOOK_PRIMARY_QUEUES = (
    "facebook.perfil",
    "facebook.video",
    "facebook.reel",
    "facebook.post",
    "facebook.storie",
    "facebook.foto",
    "facebook.permalink",
)


def build_retry_queue_name(queue_name: str) -> str:
    return f"retry.{queue_name}"


def build_dead_letter_queue_name(queue_name: str) -> str:
    return f"dead_letter.{queue_name}"


def get_platform_primary_queues(settings: Settings) -> tuple[str, ...]:
    return (settings.user_requests_queue, *FACEBOOK_PRIMARY_QUEUES)


def _require_pika():
    if pika is None:
        raise RuntimeError("Suporte RabbitMQ legado indisponivel: pacote pika nao instalado")
    return pika


def assert_rabbitmq_connection(amqp_url: str) -> None:
    pika_module = _require_pika()
    connection = pika_module.BlockingConnection(pika_module.URLParameters(amqp_url))
    connection.close()


def build_consumer_connection_parameters(amqp_url: str) -> pika.URLParameters:
    pika_module = _require_pika()
    parameters = pika_module.URLParameters(amqp_url)
    parameters.heartbeat = CONSUMER_HEARTBEAT_SECONDS
    parameters.blocked_connection_timeout = CONSUMER_BLOCKED_CONNECTION_TIMEOUT_SECONDS
    return parameters


def declare_durable_queue(channel, queue_name: str, arguments: dict[str, Any] | None = None) -> None:
    channel.queue_declare(queue=queue_name, durable=True, arguments=arguments or None)


def declare_retry_topology(channel, queue_name: str) -> None:
    declare_durable_queue(channel, queue_name)
    declare_durable_queue(
        channel,
        build_retry_queue_name(queue_name),
        arguments={
            "x-dead-letter-exchange": "",
            "x-dead-letter-routing-key": queue_name,
        },
    )
    declare_durable_queue(channel, build_dead_letter_queue_name(queue_name))


def ensure_platform_topology(amqp_url: str, queue_names: tuple[str, ...]) -> None:
    pika_module = _require_pika()
    connection = pika_module.BlockingConnection(pika_module.URLParameters(amqp_url))
    channel = connection.channel()
    for queue_name in queue_names:
        declare_retry_topology(channel, queue_name)
    connection.close()


def get_retry_attempt(properties: BasicProperties | None) -> int:
    headers = getattr(properties, "headers", None) or {}
    value = headers.get("x-retry-attempt", 0)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def calculate_retry_delay_ms(base_delay_ms: int, next_attempt: int) -> int:
    return base_delay_ms * (2 ** max(0, next_attempt - 1))


def merge_headers(
    properties: BasicProperties | None,
    extra_headers: dict[str, Any] | None = None,
) -> dict[str, Any]:
    headers = dict(getattr(properties, "headers", None) or {})
    if extra_headers:
        headers.update(extra_headers)
    return headers


def _build_properties(
    *,
    source_properties: BasicProperties | None = None,
    headers: dict[str, Any] | None = None,
    expiration_ms: int | None = None,
) -> BasicProperties:
    pika_module = _require_pika()
    merged_headers = merge_headers(source_properties, headers)
    content_type = getattr(source_properties, "content_type", None) or "application/json"
    return pika_module.BasicProperties(
        delivery_mode=2,
        content_type=content_type,
        headers=merged_headers or None,
        expiration=str(expiration_ms) if expiration_ms is not None else None,
    )


def publish_raw_message(
    channel,
    queue_name: str,
    body: bytes,
    *,
    source_properties: BasicProperties | None = None,
    headers: dict[str, Any] | None = None,
    expiration_ms: int | None = None,
) -> None:
    channel.basic_publish(
        exchange="",
        routing_key=queue_name,
        body=body,
        properties=_build_properties(
            source_properties=source_properties,
            headers=headers,
            expiration_ms=expiration_ms,
        ),
    )


class RabbitMQPublisher:
    def __init__(self, amqp_url: str, queue_name: str):
        self.amqp_url = amqp_url
        self.queue_name = queue_name

    def publish(
        self,
        message: dict,
        *,
        headers: dict[str, Any] | None = None,
        expiration_ms: int | None = None,
    ) -> None:
        pika_module = _require_pika()
        connection = pika_module.BlockingConnection(pika_module.URLParameters(self.amqp_url))
        channel = connection.channel()
        declare_durable_queue(channel, self.queue_name)
        publish_raw_message(
            channel,
            self.queue_name,
            json.dumps(message).encode("utf-8"),
            headers=headers,
            expiration_ms=expiration_ms,
        )
        connection.close()


def publish_to_retry_or_dlq(
    channel,
    *,
    queue_name: str,
    body: bytes,
    properties: BasicProperties | None,
    error_type: str,
    error_message: str,
    retryable: bool,
    max_attempts: int,
    base_delay_ms: int,
) -> str:
    current_attempt = get_retry_attempt(properties)
    base_headers = {
        "x-error-type": error_type,
        "x-error-message": error_message,
        "x-original-queue": queue_name,
    }

    if retryable and current_attempt < max_attempts:
        next_attempt = current_attempt + 1
        publish_raw_message(
            channel,
            build_retry_queue_name(queue_name),
            body,
            source_properties=properties,
            headers={**base_headers, "x-retry-attempt": next_attempt},
            expiration_ms=calculate_retry_delay_ms(base_delay_ms, next_attempt),
        )
        return "retry"

    publish_raw_message(
        channel,
        build_dead_letter_queue_name(queue_name),
        body,
        source_properties=properties,
        headers={
            **base_headers,
            "x-retry-attempt": current_attempt,
            "x-failed-at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return "dead_letter"


def get_queue_message_count(amqp_url: str, queue_name: str) -> int:
    pika_module = _require_pika()
    connection = pika_module.BlockingConnection(pika_module.URLParameters(amqp_url))
    channel = connection.channel()
    declare_ok = channel.queue_declare(queue=queue_name, passive=True)
    connection.close()
    return declare_ok.method.message_count
