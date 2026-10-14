"""TinyFish REST client (https://docs.tinyfish.ai): Search, Fetch and the web Agent. The only module that talks to TinyFish.

Plain ``httpx`` against the documented endpoints (no SDK). Errors become ``TinyFishError`` (a ``ScraperError``), so a
scan logs the hint for this source and carries on with the others. The API key travels only in the ``X-API-Key``
header: it is never logged and never put in an exception message.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from urllib.parse import quote

import httpx

from app.config import settings
from app.scrapers.base import RateLimited, ScraperError, retry_after_seconds
from app.services.rate_limiter import rate_limiter
from app.services.tinyfish_types import (
    AgentEvent,
    AgentRunResult,
    FetchedPage,
    FetchFailure,
    FetchResult,
    SearchHit,
    UsageCounters,
)

logger = logging.getLogger(__name__)

PLATFORM = "tinyfish"  # rate_limiter key
NOT_CONFIGURED_HINT = (
    "TinyFish isn't configured. Add TINYFISH_API_KEY to your .env (key: https://agent.tinyfish.ai/api-keys) and restart."
)

MAX_RETRIES = 2  # extra attempts for 409 / 429 (no details) / 502-504 / network errors
MAX_RETRY_WAIT_SECONDS = 20.0
BACKOFF_SECONDS = (2.0, 5.0)
SEARCH_TIMEOUT_SECONDS = 15.0
FETCH_BATCH_SIZE = 10  # TinyFish accepts at most 10 URLs per Fetch request
FETCH_TIMEOUT_SECONDS = 150.0  # TinyFish asks clients to wait at least this long
SEARCH_PER_MINUTE = 30  # the account limit per key
FETCH_URLS_PER_MINUTE = 150
SEARCH_MAX_WAIT_SECONDS = 20.0
FETCH_MAX_WAIT_SECONDS = 30.0
AGENT_SLOT_WAIT_SECONDS = 60.0
AGENT_POLL_SECONDS = 3.0
AGENT_STREAM_READ_TIMEOUT_SECONDS = 60.0  # HEARTBEAT events keep a healthy stream under this
TERMINAL_RUN_STATUSES = ("COMPLETED", "FAILED", "CANCELLED")
QUOTA_PAUSE_SECONDS = 3600

# Test seams: clocks and sleeping live here so tests never wait for real.
_clock: Callable[[], float] = time.monotonic
_sleep: Callable[[float], None] = time.sleep


class TinyFishError(ScraperError):
    pass


class TinyFishNotConfigured(TinyFishError):
    """No API key (or TinyFish switched off): the message says how to set it up."""


class TinyFishAuthError(TinyFishError):
    pass


class TinyFishQuotaError(TinyFishError):
    """Credits, free allowance or a daily limit used up: retrying today won't help."""


class TinyFishRateLimited(TinyFishError, RateLimited):
    pass


class TinyFishUnavailable(TinyFishError):
    pass


class _SseUnavailable(Exception):
    """The streaming Agent endpoint can't be used (before any event): fall back to the synchronous one."""


# ---------------------------------------------------------------------------------------------- throttles
class TokenBucket:
    """``capacity`` tokens per ``per_seconds``, refilled continuously. ``acquire`` reserves tokens and sleeps until
    they are due; it raises ``TinyFishRateLimited`` rather than wait longer than ``max_wait``."""

    def __init__(
        self,
        capacity: float,
        per_seconds: float,
        *,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.capacity = float(capacity)
        self.rate = float(capacity) / float(per_seconds)
        self._clock = clock or (lambda: _clock())
        self._sleep = sleep or (lambda seconds: _sleep(seconds))
        self._tokens = float(capacity)
        self._stamp = self._clock()
        self._lock = threading.Lock()

    def acquire(self, amount: float = 1, *, max_wait: float, what: str = "TinyFish") -> None:
        with self._lock:
            now = self._clock()
            self._tokens = min(self.capacity, self._tokens + (now - self._stamp) * self.rate)
            self._stamp = now
            wait = max(0.0, (amount - self._tokens) / self.rate)
            if wait > max_wait:
                raise TinyFishRateLimited(f"{what} is busy: the per-minute limit is used up (would wait {int(wait)}s). Try again shortly.")
            self._tokens -= amount  # may go negative: the reservation is paid back by waiting
        if wait > 0:
            self._sleep(wait)


def _new_search_bucket() -> TokenBucket:
    return TokenBucket(SEARCH_PER_MINUTE, 60)


def _new_fetch_bucket() -> TokenBucket:
    return TokenBucket(FETCH_URLS_PER_MINUTE, 60)


_search_bucket = _new_search_bucket()  # process-wide: the account limit is per key, not per user
_fetch_bucket = _new_fetch_bucket()
_agent_slots = threading.BoundedSemaphore(max(1, settings.TINYFISH_AGENT_CONCURRENCY))
_http_lock = threading.Lock()
_shared_http: httpx.Client | None = None


def reset_throttles() -> None:
    """Start the Search / Fetch throttles from full (tests)."""
    global _search_bucket, _fetch_bucket
    _search_bucket = _new_search_bucket()
    _fetch_bucket = _new_fetch_bucket()


def _default_http() -> httpx.Client:
    global _shared_http
    with _http_lock:
        if _shared_http is None:
            _shared_http = httpx.Client(follow_redirects=False, headers={"User-Agent": "HireFlow/1.0 (+TinyFish client)"})
        return _shared_http


# ---------------------------------------------------------------------------------------------- helpers
def _text(value: Any, limit: int = 300) -> str:
    return " ".join(str(value).split())[:limit] if value is not None else ""


def _seconds_until_utc_midnight() -> int:
    now = datetime.now(UTC)
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(60, int((midnight - now).total_seconds()) + 5)


def _envelope(response: httpx.Response) -> tuple[str | None, str, Any]:
    """(code, message, details) from ``{"error": {"code", "message", "details"}}`` (or a FastAPI-style ``{"detail": ...}``)."""
    try:
        body = response.json()
    except ValueError:
        return None, _text(response.text), None
    if not isinstance(body, dict):
        return None, _text(body), None
    err = body.get("error")
    if isinstance(err, dict):
        return err.get("code"), _text(err.get("message")), err.get("details")
    if isinstance(err, str):
        return None, _text(err), None
    if "detail" in body:
        return None, _text(body["detail"]), None
    return None, "", None


def _is_quota_429(response: httpx.Response) -> bool:
    """429 with ``error.details`` (or DAILY_LIMIT_EXCEEDED) = the account's own limit; without = slow down and retry."""
    if response.status_code != 429:
        return False
    code, _message, details = _envelope(response)
    return bool(details) or code == "DAILY_LIMIT_EXCEEDED"


def _run_error(error: Any) -> tuple[str | None, str | None]:
    if isinstance(error, dict):
        code = error.get("code")
        return (str(code) if code else None), (_text(error.get("message"), 500) or None)
    if error:
        return None, _text(error, 500)
    return None, None


def _event_from(data: dict[str, Any]) -> AgentEvent:
    def pick(name: str) -> str | None:
        value = data.get(name)
        return value if isinstance(value, str) and value else None

    return AgentEvent(
        type=str(data.get("type") or "").upper(),
        run_id=pick("run_id"),
        purpose=pick("purpose"),
        streaming_url=pick("streaming_url"),
        status=pick("status"),
        raw=data,
    )


def _sse_data(line: str) -> dict[str, Any] | None:
    if not line.startswith("data:"):
        return None
    try:
        data = json.loads(line[5:].strip())
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _run_from_payload(data: dict[str, Any], run_id: str | None = None, streaming_url: str | None = None, steps: int = 0) -> AgentRunResult:
    """An ``AgentRunResult`` from a run object (sync ``/run``, ``GET /v1/runs/{id}``) or an SSE COMPLETE event."""
    code, message = _run_error(data.get("error"))
    result = data.get("result")
    try:
        step_count = int(data.get("num_of_steps") or 0)
    except (TypeError, ValueError):
        step_count = 0
    return AgentRunResult(
        run_id=data.get("run_id") or run_id,
        status=str(data.get("status") or "COMPLETED").upper(),
        steps=max(step_count, steps),
        result=result if isinstance(result, dict) else None,
        error_code=code,
        error_message=message,
        streaming_url=data.get("streaming_url") or streaming_url,
    )


class _RunState:
    """What the SSE stream has told us so far."""

    def __init__(self) -> None:
        self.run_id: str | None = None
        self.streaming_url: str | None = None
        self.progress = 0
        self.result: AgentRunResult | None = None  # set when the stream finished the run (or the run was cancelled)


# ---------------------------------------------------------------------------------------------- client
class TinyFishClient:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        http: httpx.Client | None = None,
        usage: UsageCounters | None = None,
        user_id: str | None = None,
    ) -> None:
        key = api_key if api_key is not None else settings.TINYFISH_API_KEY
        self._api_key = (key or "").strip()
        self._http = http or _default_http()
        self.usage = usage if usage is not None else UsageCounters()
        self.user_id = user_id

    def __repr__(self) -> str:
        return f"TinyFishClient(configured={self.configured}, user_id={self.user_id!r})"

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    # ------------------------------------------------------------------ plumbing
    def _redact(self, text: str) -> str:
        return text.replace(self._api_key, "***") if self._api_key else text

    def _require_key(self) -> str:
        if not self._api_key:
            raise TinyFishNotConfigured(NOT_CONFIGURED_HINT)
        return self._api_key

    def _guard(self) -> None:
        """Every call first: configured, not cooling down after a quota / rate-limit error, inside the hourly budget."""
        self._require_key()
        if rate_limiter.is_paused(PLATFORM):
            raise TinyFishRateLimited("TinyFish is paused after hitting a limit (credits or rate limit). Try again later.")
        if not rate_limiter.allow_request(PLATFORM):
            raise TinyFishRateLimited("TinyFish request budget for this hour is used up.")

    def _bump_user(self, endpoint: str, amount: int = 1) -> None:
        if not self.user_id or amount <= 0:
            return
        try:
            day = datetime.now(UTC).strftime("%Y%m%d")
            rate_limiter.incr_counter(f"tf:use:{self.user_id}:{day}:{endpoint}", 90000, amount)
        except Exception:  # a broken meter must never fail a search
            logger.debug("TinyFish usage meter failed", exc_info=True)

    def _headers(self) -> dict[str, str]:
        return {"X-API-Key": self._require_key(), "Accept": "application/json"}

    def _retry_delay(self, response: httpx.Response, attempt: int) -> float | None:
        """Seconds to wait before trying again, or None when this response isn't retryable (or retries are spent)."""
        if attempt >= MAX_RETRIES:
            return None
        status = response.status_code
        retryable = status in (502, 503, 504) or (status == 429 and not _is_quota_429(response))
        if status == 409:
            retryable = _envelope(response)[0] in (None, "RETRY_REQUIRED")
        if not retryable:
            return None
        wait = retry_after_seconds(response.headers.get("retry-after"))
        if wait is None:
            wait = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)]
        return min(wait, MAX_RETRY_WAIT_SECONDS)

    def _map_error(self, response: httpx.Response, endpoint: str) -> TinyFishError:
        status = response.status_code
        code, message, details = _envelope(response)
        suffix = f" ({code})" if code else ""
        detail = f": {message}" if message else ""
        if status == 401:
            error: TinyFishError = TinyFishAuthError(
                "TINYFISH_API_KEY is invalid or was rejected (HTTP 401). Create a key at https://agent.tinyfish.ai/api-keys and restart."
            )
        elif status == 402 or _is_quota_429(response):
            seconds = _seconds_until_utc_midnight() if code == "DAILY_LIMIT_EXCEEDED" else QUOTA_PAUSE_SECONDS
            rate_limiter.pause_platform(PLATFORM, seconds)
            error = TinyFishQuotaError(f"TinyFish {endpoint} limit or credits used up{suffix}{detail}. Paused for {seconds // 60} min.")
        elif status in (409, 429):
            error = TinyFishRateLimited(f"TinyFish {endpoint} is rate limiting requests (HTTP {status}){suffix}. Try again shortly.")
        elif status >= 500:
            error = TinyFishUnavailable(f"TinyFish {endpoint} is unavailable (HTTP {status}){suffix}.")
        elif status == 400:
            extra = f" {_text(details)}" if details else ""
            error = TinyFishError(f"TinyFish {endpoint} rejected the request{suffix}{detail}{extra}")
        else:
            error = TinyFishError(f"TinyFish {endpoint} returned HTTP {status}{suffix}{detail}")
        return type(error)(self._redact(str(error)))

    def _request(
        self,
        endpoint: str,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        timeout: float | httpx.Timeout,
        guard: bool = True,
        retry_transport: bool = True,
    ) -> httpx.Response:
        """One API call with retry / error mapping. Returns only a 2xx response."""
        if guard:
            self._guard()
        headers = self._headers()
        attempt = 0
        while True:
            started = _clock()
            try:
                response = self._http.request(method, url, params=params, json=body, headers=headers, timeout=timeout)
            except httpx.TransportError as exc:
                if retry_transport and attempt < MAX_RETRIES:
                    _sleep(BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)])
                    attempt += 1
                    continue
                self.usage.add(errors=1)
                raise TinyFishUnavailable(f"TinyFish {endpoint} is unreachable ({type(exc).__name__})") from exc
            logger.debug("tinyfish %s %s -> %s in %.0f ms", endpoint, method, response.status_code, (_clock() - started) * 1000)
            if response.is_success:
                return response
            delay = self._retry_delay(response, attempt)
            if delay is not None:
                logger.info("tinyfish %s HTTP %s: retrying in %.0fs", endpoint, response.status_code, delay)
                _sleep(delay)
                attempt += 1
                continue
            self.usage.add(errors=1)
            raise self._map_error(response, endpoint)

    # ------------------------------------------------------------------ Search
    def search(
        self,
        query: str,
        *,
        purpose: str | None = None,
        location: str | None = None,
        include_domains: Sequence[str] | None = None,
        exclude_domains: Sequence[str] | None = None,
        after_date: date | None = None,
        page: int = 0,
    ) -> list[SearchHit]:
        self._require_key()
        query = " ".join((query or "").split())[:2000]
        if not query:
            raise TinyFishError("TinyFish search needs a query")
        params: dict[str, Any] = {"query": query}
        if purpose:
            params["purpose"] = purpose[:500]
        if location:
            params["location"] = location
        if include_domains:
            params["include_domains"] = ",".join(include_domains)
        if exclude_domains:
            params["exclude_domains"] = ",".join(exclude_domains)
        if after_date:
            params["after_date"] = after_date.isoformat()
        if page:
            params["page"] = max(0, min(int(page), 10))
        self._guard()
        _search_bucket.acquire(1, max_wait=SEARCH_MAX_WAIT_SECONDS, what="TinyFish Search")
        self.usage.add(search_calls=1)
        self._bump_user("search")
        response = self._request(
            "search", "GET", settings.TINYFISH_SEARCH_URL.rstrip("/") + "/", params=params, timeout=SEARCH_TIMEOUT_SECONDS, guard=False
        )
        return self._parse_search(response)

    @staticmethod
    def _parse_search(response: httpx.Response) -> list[SearchHit]:
        try:
            data = response.json()
        except ValueError:
            return []
        rows = data.get("results") if isinstance(data, dict) else None
        hits: list[SearchHit] = []
        for index, row in enumerate(rows if isinstance(rows, list) else [], start=1):
            if not isinstance(row, dict) or not isinstance(row.get("url"), str) or not row["url"]:
                continue
            try:
                position = int(row.get("position") or index)
            except (TypeError, ValueError):
                position = index
            raw_date = row.get("date")
            hits.append(
                SearchHit(
                    position=position,
                    title=str(row.get("title") or ""),
                    snippet=str(row.get("snippet") or ""),
                    url=row["url"],
                    site_name=str(row.get("site_name") or ""),
                    date=str(raw_date) if raw_date not in (None, "") else None,  # free text: kept as it came
                )
            )
        return hits

    # ------------------------------------------------------------------ Fetch
    def fetch(
        self,
        urls: Sequence[str],
        *,
        format: Literal["markdown", "html", "json"] = "markdown",
        links: bool = True,
        purpose: str | None = None,
        ttl: int | None = 0,
        per_url_timeout_ms: int = 45000,
        include_selectors: Sequence[str] | None = None,
        exclude_selectors: Sequence[str] | None = None,
    ) -> FetchResult:
        """Read pages in batches of 10. A page that can't be read is a ``FetchFailure`` in the result, never an exception;
        an HTTP-level error (bad key, quota, 5xx) raises."""
        self._require_key()
        unique = list(dict.fromkeys(u for u in urls if u))
        result = FetchResult(pages=[], failures=[])
        for start in range(0, len(unique), FETCH_BATCH_SIZE):
            batch = unique[start : start + FETCH_BATCH_SIZE]
            body: dict[str, Any] = {
                "urls": batch,
                "format": format,
                "links": links,
                "iframe_links": links,
                "per_url_timeout_ms": per_url_timeout_ms,
            }
            if ttl is not None:
                body["ttl"] = ttl
            if purpose:
                body["purpose"] = purpose[:500]
            if include_selectors:
                body["include_selectors"] = list(include_selectors)
            if exclude_selectors:
                body["exclude_selectors"] = list(exclude_selectors)
            self._guard()
            _fetch_bucket.acquire(len(batch), max_wait=FETCH_MAX_WAIT_SECONDS, what="TinyFish Fetch")
            self.usage.add(fetch_calls=1, fetch_urls=len(batch))
            self._bump_user("fetch", len(batch))
            response = self._request(
                "fetch", "POST", settings.TINYFISH_FETCH_URL.rstrip("/") + "/", body=body, timeout=FETCH_TIMEOUT_SECONDS, guard=False
            )
            self._parse_fetch(response, format, result)
        return result

    @staticmethod
    def _parse_fetch(response: httpx.Response, requested_format: str, into: FetchResult) -> None:
        try:
            data = response.json()
        except ValueError:
            data = None
        if not isinstance(data, dict):
            return
        for row in data.get("results") or []:
            if not isinstance(row, dict) or not row.get("url"):
                continue
            text = row.get("text")
            if text is None:
                text = ""
            elif not isinstance(text, str):
                text = json.dumps(text)  # format=json
            latency = row.get("latency_ms")
            into.pages.append(
                FetchedPage(
                    url=row["url"],
                    final_url=row.get("final_url") or row["url"],
                    title=str(row.get("title") or ""),
                    text=text,
                    format=str(row.get("format") or requested_format),
                    links=[link for link in (row.get("links") or []) if isinstance(link, str)],
                    latency_ms=float(latency) if isinstance(latency, int | float) else None,
                )
            )
        for row in data.get("errors") or []:
            if not isinstance(row, dict) or not row.get("url"):
                continue
            status = row.get("status")
            into.failures.append(
                FetchFailure(
                    url=row["url"],
                    code=str(row.get("error") or row.get("code") or "unknown"),
                    status=status if isinstance(status, int) else None,
                )
            )

    # ------------------------------------------------------------------ Agent
    def run_agent(
        self,
        url: str,
        goal: str,
        *,
        output_schema: dict | None = None,
        stealth: bool = False,
        proxy_country: str | None = None,
        max_duration_seconds: int | None = None,
        on_event: Callable[[AgentEvent], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> AgentRunResult:
        """Run the web Agent on ``url`` and wait for it. Run-level failures (SERVICE_BUSY, BILLING_REJECTED, TIMEOUT, ...)
        come back in the result's ``error_code``; only HTTP-level problems raise."""
        self._require_key()
        if should_cancel and should_cancel():
            return AgentRunResult(run_id=None, status="CANCELLED")
        max_duration = max(10, int(max_duration_seconds or settings.TINYFISH_AGENT_MAX_DURATION_SECONDS))
        payload: dict[str, Any] = {
            "url": url,
            "goal": goal,
            "browser_profile": "stealth" if stealth else "lite",
            "agent_config": {"max_duration_seconds": max_duration},
        }
        if proxy_country:
            payload["proxy_config"] = {"enabled": True, "country_code": proxy_country}
        if output_schema:
            payload["output_schema"] = output_schema
        self._guard()
        if not _agent_slots.acquire(timeout=AGENT_SLOT_WAIT_SECONDS):
            raise TinyFishRateLimited("both TinyFish agent slots are busy")
        try:
            self.usage.add(agent_runs=1)
            self._bump_user("agent")
            result = self._run_locked(payload, max_duration, on_event, should_cancel)
        finally:
            _agent_slots.release()
        self.usage.add(agent_steps=result.steps, errors=1 if (result.error_code or result.status == "FAILED") else 0)
        return result

    def _run_locked(
        self,
        payload: dict[str, Any],
        max_duration: int,
        on_event: Callable[[AgentEvent], None] | None,
        should_cancel: Callable[[], bool] | None,
    ) -> AgentRunResult:
        started = _clock()
        try:
            state = self._run_sse(payload, max_duration, started, on_event, should_cancel)
        except _SseUnavailable:
            logger.info("tinyfish agent: streaming endpoint unavailable, using the synchronous one")
            return self._run_sync(payload, max_duration)
        if state.result is not None:
            return state.result
        if not state.run_id:
            return AgentRunResult(
                None, "FAILED", state.progress, error_code="STREAM_ENDED", error_message="The TinyFish stream ended before the run started."
            )
        return self._poll_run(state, started + max_duration + 30, should_cancel)

    def _run_sse(
        self,
        payload: dict[str, Any],
        max_duration: int,
        started: float,
        on_event: Callable[[AgentEvent], None] | None,
        should_cancel: Callable[[], bool] | None,
    ) -> _RunState:
        url = settings.TINYFISH_AGENT_URL.rstrip("/") + "/v1/automation/run-sse"
        headers = {**self._headers(), "Accept": "text/event-stream"}
        timeout = httpx.Timeout(connect=15.0, read=AGENT_STREAM_READ_TIMEOUT_SECONDS, write=30.0, pool=15.0)
        attempt = 0
        while True:
            try:
                with self._http.stream("POST", url, headers=headers, json=payload, timeout=timeout) as response:
                    if response.is_success:
                        return self._consume(response, started + max_duration + 60, on_event, should_cancel)
                    response.read()
                    if response.status_code in (404, 405):
                        raise _SseUnavailable
                    delay = self._retry_delay(response, attempt)
                    if delay is None:
                        self.usage.add(errors=1)
                        raise self._map_error(response, "agent")
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                raise _SseUnavailable from exc
            except httpx.TransportError as exc:
                self.usage.add(errors=1)
                raise TinyFishUnavailable(f"TinyFish agent is unreachable ({type(exc).__name__})") from exc
            logger.info("tinyfish agent HTTP %s: retrying in %.0fs", response.status_code, delay)
            _sleep(delay)
            attempt += 1

    def _consume(
        self,
        response: httpx.Response,
        deadline: float,
        on_event: Callable[[AgentEvent], None] | None,
        should_cancel: Callable[[], bool] | None,
    ) -> _RunState:
        state = _RunState()
        try:
            for line in response.iter_lines():
                data = _sse_data(line)
                if data is None:
                    continue
                event = _event_from(data)
                state.run_id = event.run_id or state.run_id
                state.streaming_url = event.streaming_url or state.streaming_url
                if event.type == "PROGRESS":
                    state.progress += 1
                if on_event:
                    try:
                        on_event(event)
                    except Exception:  # a UI callback must not kill the run
                        logger.debug("tinyfish on_event callback failed", exc_info=True)
                if event.type == "COMPLETE":
                    state.result = _run_from_payload(data, state.run_id, state.streaming_url, state.progress)
                    return state
                if should_cancel and should_cancel():
                    state.result = self._cancelled(state)
                    return state
                if _clock() > deadline:
                    break
        except httpx.TransportError as exc:  # stream cut mid-run: the caller recovers through GET /v1/runs/{id}
            logger.info("tinyfish agent stream interrupted (%s)", type(exc).__name__)
        return state

    def _cancelled(self, state: _RunState) -> AgentRunResult:
        if state.run_id:
            try:
                self.cancel_run(state.run_id)
            except TinyFishError as exc:
                logger.warning("tinyfish could not cancel run %s: %s", state.run_id, exc)
        return AgentRunResult(state.run_id, "CANCELLED", state.progress, streaming_url=state.streaming_url)

    def _poll_run(self, state: _RunState, deadline: float, should_cancel: Callable[[], bool] | None) -> AgentRunResult:
        """The stream ended without a COMPLETE event: ask the run itself until it is finished."""
        assert state.run_id
        while True:
            try:
                run = self._get_run(state.run_id, guard=False)
                if run.status in TERMINAL_RUN_STATUSES:
                    run.streaming_url = run.streaming_url or state.streaming_url
                    run.steps = max(run.steps, state.progress)
                    return run
            except (TinyFishRateLimited, TinyFishUnavailable):
                logger.info("tinyfish run %s: status check failed, will retry", state.run_id)
            if should_cancel and should_cancel():
                return self._cancelled(state)
            if _clock() > deadline:
                try:
                    self.cancel_run(state.run_id)
                except TinyFishError:
                    logger.debug("tinyfish could not cancel timed-out run", exc_info=True)
                return AgentRunResult(
                    state.run_id, "TIMEOUT", state.progress, error_code="TIMEOUT",
                    error_message="The TinyFish run didn't finish in time.", streaming_url=state.streaming_url,
                )
            _sleep(AGENT_POLL_SECONDS)

    def _run_sync(self, payload: dict[str, Any], max_duration: int) -> AgentRunResult:
        timeout = httpx.Timeout(connect=15.0, read=float(max_duration + 60), write=30.0, pool=15.0)
        response = self._request(
            "agent", "POST", settings.TINYFISH_AGENT_URL.rstrip("/") + "/v1/automation/run",
            body=payload, timeout=timeout, guard=False, retry_transport=False,  # never re-send: a second run costs credits
        )
        try:
            data = response.json()
        except ValueError:
            data = None
        if not isinstance(data, dict):
            return AgentRunResult(None, "FAILED", error_code="BAD_RESPONSE", error_message="TinyFish returned an unreadable run result.")
        return _run_from_payload(data)

    def _get_run(self, run_id: str, *, guard: bool) -> AgentRunResult:
        response = self._request(
            "agent", "GET", settings.TINYFISH_AGENT_URL.rstrip("/") + f"/v1/runs/{quote(run_id, safe='')}",
            timeout=30.0, guard=guard,
        )
        try:
            data = response.json()
        except ValueError:
            data = None
        if not isinstance(data, dict):
            raise TinyFishUnavailable("TinyFish returned an unreadable run status.")
        return _run_from_payload(data, run_id)

    def get_run(self, run_id: str) -> AgentRunResult:
        return self._get_run(run_id, guard=True)

    def cancel_run(self, run_id: str) -> None:
        self._request(
            "agent", "POST", settings.TINYFISH_AGENT_URL.rstrip("/") + f"/v1/runs/{quote(run_id, safe='')}/cancel",
            timeout=30.0, guard=False,
        )

    # ------------------------------------------------------------------ health check
    def ping(self) -> dict[str, Any]:
        """One tiny Search to prove the key works (used by Settings -> "Test TinyFish")."""
        if not self.configured:
            return {"ok": False, "latency_ms": 0, "results": 0, "error": "TINYFISH_API_KEY is not set", "hint": NOT_CONFIGURED_HINT}
        started = _clock()
        try:
            hits = self.search("software engineer careers", purpose="Connection test")
        except TinyFishError as exc:
            hint = None
            if isinstance(exc, TinyFishAuthError):
                hint = "Check TINYFISH_API_KEY: create a new key at https://agent.tinyfish.ai/api-keys."
            elif isinstance(exc, TinyFishQuotaError):
                hint = "Your TinyFish credits or daily allowance are used up: top up at https://agent.tinyfish.ai."
            return {"ok": False, "latency_ms": int((_clock() - started) * 1000), "results": 0, "error": self._redact(str(exc)), "hint": hint}
        return {"ok": True, "latency_ms": int((_clock() - started) * 1000), "results": len(hits), "error": None, "hint": None}


# ---------------------------------------------------------------------------------------------- module API
def tinyfish_available() -> bool:
    return settings.tinyfish_enabled


def get_client(user_id: str | None = None) -> TinyFishClient:
    """A client for this process's key, metering ``user_id``'s daily usage. Raises ``TinyFishNotConfigured`` when TinyFish is off."""
    if not settings.tinyfish_enabled:
        if (settings.TINYFISH_API_KEY or "").strip():
            raise TinyFishNotConfigured("TinyFish is switched off (TINYFISH_ENABLED=false).")
        raise TinyFishNotConfigured(NOT_CONFIGURED_HINT)
    return TinyFishClient(user_id=user_id)


def usage_today(user_id: str) -> dict[str, int]:
    """This user's TinyFish calls so far today (UTC), from the rate limiter's counters."""
    day = datetime.now(UTC).strftime("%Y%m%d")

    def count(endpoint: str) -> int:
        return int(rate_limiter.get_counter(f"tf:use:{user_id}:{day}:{endpoint}"))

    return {"search_calls": count("search"), "fetch_urls": count("fetch"), "agent_runs": count("agent")}
