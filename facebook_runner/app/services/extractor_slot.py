import time
from contextlib import contextmanager

from psycopg import connect

from app_shared.config import Settings

ADVISORY_LOCK_NAMESPACE = 724_220


class ExtractorSlotTimeoutError(Exception):
    pass


class PostgresExtractorSlotLimiter:
    def __init__(self, settings: Settings, logger=None):
        self.settings = settings
        self.logger = logger

    @contextmanager
    def acquire(self):
        limit = max(1, self.settings.facebook_extractor_concurrency_limit)
        wait_seconds = max(1, self.settings.facebook_extractor_slot_wait_seconds)
        deadline = time.monotonic() + wait_seconds

        with connect(self.settings.db_dsn) as connection:
            slot = self._try_acquire_slot(connection, limit)
            while slot is None and time.monotonic() < deadline:
                time.sleep(1)
                slot = self._try_acquire_slot(connection, limit)

            if slot is None:
                raise ExtractorSlotTimeoutError(
                    f"extractor concurrency limit reached after {wait_seconds}s"
                )

            try:
                yield slot
            finally:
                self._release_slot(connection, slot)

    def _try_acquire_slot(self, connection, limit: int) -> int | None:
        with connection.cursor() as cursor:
            for slot in range(limit):
                cursor.execute("SELECT pg_try_advisory_lock(%s, %s);", (ADVISORY_LOCK_NAMESPACE, slot))
                if cursor.fetchone()[0]:
                    return slot
        return None

    def _release_slot(self, connection, slot: int) -> None:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s, %s);", (ADVISORY_LOCK_NAMESPACE, slot))
