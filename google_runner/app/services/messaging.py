from app_shared.amqp import RabbitMQPublisher, declare_durable_queue, ensure_platform_topology, get_platform_primary_queues


def ensure_runner_topology(amqp_url: str, settings) -> None:
    ensure_platform_topology(amqp_url, get_platform_primary_queues(settings))
