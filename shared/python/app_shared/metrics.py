from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram


class RunnerMetrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.messages_received_total = Counter(
            "runner_messages_received_total",
            "Total messages received by runner consumers.",
            ("service", "queue"),
            registry=self.registry,
        )
        self.messages_processed_total = Counter(
            "runner_messages_processed_total",
            "Total messages processed by runner consumers.",
            ("service", "queue", "outcome"),
            registry=self.registry,
        )
        self.processing_failures_total = Counter(
            "runner_processing_failures_total",
            "Total runner processing failures.",
            ("service", "queue", "error_type"),
            registry=self.registry,
        )
        self.message_processing_seconds = Histogram(
            "runner_message_processing_seconds",
            "Processing duration per runner message.",
            ("service", "queue"),
            registry=self.registry,
        )
        self.last_success_unixtime = Gauge(
            "runner_last_success_unixtime",
            "Unix timestamp of last successful runner message.",
            ("service", "queue"),
            registry=self.registry,
        )
        self.google_captcha_total = Counter(
            "google_captcha_total",
            "Total captcha detections during Google discovery.",
            registry=self.registry,
        )
        self.facebook_login_required_total = Counter(
            "facebook_login_required_total",
            "Total Facebook login-required terminal failures.",
            registry=self.registry,
        )


class ApiMetrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.requests_created_total = Counter(
            "api_requests_created_total",
            "Total requests created via the API.",
            ("status",),
            registry=self.registry,
        )
        self.healthcheck_total = Counter(
            "api_healthcheck_total",
            "Total dependency health checks executed by the API.",
            ("dependency", "status"),
            registry=self.registry,
        )
        self.rabbitmq_queue_messages = Gauge(
            "rabbitmq_queue_messages",
            "Current RabbitMQ message count per queue.",
            ("queue",),
            registry=self.registry,
        )
