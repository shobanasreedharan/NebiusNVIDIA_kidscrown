import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

from app.transcript import TranscriptProvider

logger = logging.getLogger(__name__)


class _RedisCircuitBreaker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._open_until = 0.0

    def is_open(self) -> bool:
        with self._lock:
            return time.monotonic() < self._open_until

    def record_failure(self, seconds: float) -> bool:
        with self._lock:
            if time.monotonic() < self._open_until:
                return False
            self._open_until = time.monotonic() + seconds
            return True

    def record_success(self) -> None:
        with self._lock:
            self._open_until = 0.0


_breakers_lock = threading.Lock()
_breakers: dict[int, _RedisCircuitBreaker] = {}


def _breaker_for(redis_client) -> _RedisCircuitBreaker:
    try:
        breaker = getattr(redis_client, "_kidscrown_circuit_breaker", None)
        if breaker is None:
            breaker = _RedisCircuitBreaker()
            setattr(redis_client, "_kidscrown_circuit_breaker", breaker)
        return breaker
    except Exception:  # clients with slots use the shared identity registry below
        pass
    key = id(redis_client)
    with _breakers_lock:
        return _breakers.setdefault(key, _RedisCircuitBreaker())


def _is_connection_failure(exc: Exception) -> bool:
    failure_types = [ConnectionError, TimeoutError]
    try:
        from redis.exceptions import ConnectionError as RedisConnectionError
        from redis.exceptions import TimeoutError as RedisTimeoutError

        failure_types.extend((RedisConnectionError, RedisTimeoutError))
    except ImportError:
        pass
    return isinstance(exc, tuple(failure_types))

# Stored when the inner provider returned None (inconclusive), so a retry
# later the same day doesn't pay for the same ambiguous fetch again.
_AMBIGUOUS = "__ambiguous__"


def seconds_until_end_of_day(tz_name: str, now: datetime | None = None) -> int:
    tz = ZoneInfo(tz_name)
    local_now = now.astimezone(tz) if now else datetime.now(tz)
    next_midnight = (local_now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    # Subtract in UTC so DST days come out right.
    delta = next_midnight.astimezone(timezone.utc) - local_now.astimezone(timezone.utc)
    return max(60, int(delta.total_seconds()))


def build_redis_client(
    url: str, connect_timeout: float = 0.3, socket_timeout: float = 0.3
):
    import redis.asyncio as redis

    # Short timeouts so a dead Redis can't stall /classify.
    return redis.from_url(
        url,
        decode_responses=True,
        socket_connect_timeout=connect_timeout,
        socket_timeout=socket_timeout,
    )


@lru_cache
def get_redis_client(
    url: str, connect_timeout: float = 0.3, socket_timeout: float = 0.3
):
    # ClassificationService is built per request; reuse one client (and its
    # connection pool) instead of opening a new one every time.
    return build_redis_client(url, connect_timeout, socket_timeout)


class RedisCachedTranscriptProvider:
    """Wraps one provider (Tavily) with a same-day Redis cache."""

    def __init__(self, inner: TranscriptProvider, redis_client, tz_name: str,
                 prefix: str = "kc:tavily:transcript:", breaker_seconds: float = 60.0):
        self._inner = inner
        self._redis = redis_client
        self._tz_name = tz_name
        self._prefix = prefix
        self._breaker_seconds = breaker_seconds
        self._breaker = _breaker_for(redis_client)
        self.last_redis_seconds = 0.0

    async def get_transcript(self, video_id: str) -> str | None:
        key = f"{self._prefix}{video_id}"
        self.last_redis_seconds = 0.0
        cached = None
        if not self._breaker.is_open():
            started = time.perf_counter()
            try:
                cached = await self._redis.get(key)
                self._breaker.record_success()
            except Exception as exc:  # noqa: BLE001
                self._record_failure("get", exc)
            finally:
                self.last_redis_seconds += time.perf_counter() - started
        if cached is not None:
            return None if cached == _AMBIGUOUS else cached

        # Exceptions from the inner provider propagate and are NOT cached.
        result = await self._inner.get_transcript(video_id)

        if not self._breaker.is_open():
            started = time.perf_counter()
            try:
                await self._redis.set(
                    key,
                    _AMBIGUOUS if result is None else result,
                    ex=seconds_until_end_of_day(self._tz_name),
                )
                self._breaker.record_success()
            except Exception as exc:  # noqa: BLE001
                self._record_failure("set", exc)
            finally:
                self.last_redis_seconds += time.perf_counter() - started
        return result

    def _record_failure(self, operation: str, exc: Exception) -> None:
        if _is_connection_failure(exc):
            if self._breaker.record_failure(self._breaker_seconds):
                logger.warning(
                    "Redis %s failed; circuit open for %.0fs (error=%s)",
                    operation,
                    self._breaker_seconds,
                    type(exc).__name__,
                )
        else:
            logger.warning("Redis %s failed, continuing (error=%s)", operation, type(exc).__name__)