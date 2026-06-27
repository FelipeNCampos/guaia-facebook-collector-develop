from uuid import UUID

from app.services.extractor import FacebookExtractor
from app.services.extractor_slot import ExtractorSlotTimeoutError, PostgresExtractorSlotLimiter
from app.services.media_archiver import FacebookMediaArchiver
from app.services.repository import FacebookRunnerRepository
from app_shared.config import Settings
from app_shared.observability import log_event
from app_shared.processing import RetryableProcessingError, TerminalProcessingError

RUNNER_QUEUE_TO_TYPE = {
    "facebook.perfil": "perfil",
    "facebook.video": "video",
    "facebook.reel": "reel",
    "facebook.post": "post",
    "facebook.storie": "storie",
    "facebook.foto": "foto",
    "facebook.permalink": "permalink",
}

SUPPORTED_RUNNER_TYPES = {"perfil", "video", "reel", "post", "storie", "foto", "permalink"}
MULTI_RUNNER_TYPES = {"all", "multi", "*"}
TERMINAL_RECORD_ERRORS = {
    "login_obrigatorio",
    "dados_publicacao_nao_encontrados",
    "dados_perfil_nao_encontrados",
}


class FacebookRunnerProcessor:
    def __init__(
        self,
        settings: Settings,
        repository: FacebookRunnerRepository | None = None,
        extractor: FacebookExtractor | None = None,
        slot_limiter: PostgresExtractorSlotLimiter | None = None,
        media_archiver: FacebookMediaArchiver | None = None,
        logger=None,
    ):
        self.settings = settings
        self.repository = repository or FacebookRunnerRepository(settings.db_dsn)
        self.extractor = extractor or FacebookExtractor(settings)
        self.slot_limiter = slot_limiter or PostgresExtractorSlotLimiter(settings, logger=logger)
        self.media_archiver = media_archiver or FacebookMediaArchiver(settings, logger=logger)
        self.logger = logger

    def process_message(self, message: dict, *, attempt: int = 0, max_attempts: int = 3) -> None:
        request_id, runner_type, fonte, perfil_slug = self._validate_message(message)

        try:
            with self.slot_limiter.acquire() as extractor_slot:
                self.repository.update_request_link_status(request_id, fonte, "processing", "")
                if self.logger:
                    log_event(
                        self.logger,
                        "info",
                        "facebook_extractor_slot_acquired",
                        request_id=str(request_id),
                        queue_name=self.settings.facebook_runner_queue,
                        runner_type=runner_type,
                        fonte=fonte,
                        extractor_slot=extractor_slot,
                        concurrency_limit=self.settings.facebook_extractor_concurrency_limit,
                    )
                record = self.extractor.enrich_link(runner_type, fonte, perfil_slug=perfil_slug)
                record = self.media_archiver.archive(record, runner_type, query_context=message)
        except ExtractorSlotTimeoutError as exc:
            raise RetryableProcessingError("extractor_concurrency_limited", str(exc)) from exc
        except Exception as exc:
            if attempt >= max_attempts:
                record = self._build_failure_record(fonte, runner_type, perfil_slug, str(exc))
                self.repository.upsert_domain_record(runner_type, record)
                self.repository.update_request_link_status(request_id, fonte, "failed", str(exc))
                self.repository.refresh_request_status(request_id)
                raise TerminalProcessingError("processing_error", str(exc)) from exc
            raise RetryableProcessingError("processing_error", str(exc)) from exc

        self.repository.upsert_domain_record(runner_type, record)
        if self.settings.facebook_fetch_comments:
            self.repository.upsert_comments(runner_type, record)
        self.repository.update_request_link_status(
            request_id,
            fonte,
            record["status"],
            record.get("error", "") or "",
        )
        self.repository.refresh_request_status(request_id)
        if record["status"] == "failed" and (record.get("error") or "") in TERMINAL_RECORD_ERRORS:
            raise TerminalProcessingError(record["error"], record["error"])
        if self.logger:
            log_event(
                self.logger,
                "info",
                "facebook_link_processed",
                request_id=str(request_id),
                queue_name=self.settings.facebook_runner_queue,
                runner_type=runner_type,
                fonte=fonte,
                status=record["status"],
                error=record.get("error", "") or "",
            )

    def _validate_message(self, message: dict) -> tuple[UUID, str, str, str]:
        required_fields = ("request_id", "user_id", "tipo", "fonte", "perfil", "metadata")
        missing_fields = [field_name for field_name in required_fields if field_name not in message]
        if missing_fields:
            raise TerminalProcessingError("invalid_payload", f"Missing required fields: {', '.join(missing_fields)}")

        try:
            request_id = UUID(message["request_id"])
            runner_type = message["tipo"]
            fonte = message["fonte"]
            perfil_slug = message.get("perfil") or ""
        except Exception as exc:
            raise TerminalProcessingError("invalid_payload", str(exc)) from exc

        if runner_type not in SUPPORTED_RUNNER_TYPES:
            raise TerminalProcessingError("invalid_payload", f"Unsupported runner type: {runner_type}")
        if self.settings.facebook_runner_type not in MULTI_RUNNER_TYPES and runner_type != self.settings.facebook_runner_type:
            raise TerminalProcessingError(
                "invalid_payload",
                f"Runner type mismatch: expected {self.settings.facebook_runner_type}, received {runner_type}"
            )

        return request_id, runner_type, fonte, perfil_slug

    def _build_failure_record(self, fonte: str, runner_type: str, perfil_slug: str, error: str) -> dict:
        if runner_type == "perfil":
            return {
                "fonte": fonte,
                "status": "failed",
                "error": error,
                "data": "",
                "perfil": perfil_slug,
                "verificado": "",
            }

        return {
            "fonte": fonte,
            "status": "failed",
            "error": error,
            "data": "",
            "perfil": perfil_slug,
            "verificado": "",
            "descricao": "",
            "num_comentarios": "",
            "num_visualizacoes": "",
            "num_reacoes": "",
        }
