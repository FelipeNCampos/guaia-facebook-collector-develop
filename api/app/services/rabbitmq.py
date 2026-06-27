from app_shared.amqp import (
    RabbitMQPublisher,
    assert_rabbitmq_connection,
    ensure_platform_topology,
    get_platform_primary_queues,
)


def ensure_rabbitmq_topology(amqp_url: str, settings) -> None:
    ensure_platform_topology(amqp_url, get_platform_primary_queues(settings))
