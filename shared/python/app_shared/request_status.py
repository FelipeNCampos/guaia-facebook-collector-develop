from enum import IntEnum


class RequestStatusCode(IntEnum):
    PENDING = 1
    PROCESSING = 2
    COMPLETED = 3
    COMPLETED_WITH_MISSING = 4
    FAILED = 5
    CANCELLED = 6


FACEBOOK_QUERY_SOURCE = 5
FACEBOOK_SOURCE_TYPE = 3


def status_current_from_legacy(status: str | None) -> RequestStatusCode:
    normalized = (status or "").strip().lower()
    if normalized in {"received", "queued"}:
        return RequestStatusCode.PENDING
    if normalized == "processing":
        return RequestStatusCode.PROCESSING
    if normalized == "finished":
        return RequestStatusCode.COMPLETED
    if normalized in {"failed", "failed_captcha"}:
        return RequestStatusCode.FAILED
    return RequestStatusCode.PENDING


def legacy_status_from_current(
    status_current: int | RequestStatusCode,
    *,
    error: str | None = None,
    phase: str | None = None,
) -> str:
    current = RequestStatusCode(int(status_current))

    if current == RequestStatusCode.PENDING:
        return "received" if phase == "received" else "queued"
    if current == RequestStatusCode.PROCESSING:
        return "processing"
    if current in {RequestStatusCode.COMPLETED, RequestStatusCode.COMPLETED_WITH_MISSING}:
        return "finished"
    if current == RequestStatusCode.FAILED:
        return "failed_captcha" if error == "google_captcha_detected" else "failed"
    return "failed"
