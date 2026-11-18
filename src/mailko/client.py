"""The HTTP client of Mailko's API ``/v1``.

* **One key, one project.** ``mk_live_…`` sends, ``mk_test_…`` validates and
  never delivers; a partner app's key (``mkp_…``) only opens ``client.partner``.
* **Idempotency.** Every write carries an ``Idempotency-Key`` (a uuid4 unless
  you pass one) and the SAME key on every retry: a retried send never sends
  the email twice.
* **Retries**, only when retrying is safe and useful: no answer, ``409
  idempotency_key_in_progress``, ``429`` (honouring ``Retry-After`` up to
  ``max_retry_after``; never the month's quota) and ``5xx``. Never another 4xx.
* Answers are plain ``dict``s, exactly the JSON Mailko returns.
"""

from __future__ import annotations

import datetime
import json as jsonlib
import platform
import random
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from typing import Any
from urllib.parse import urlsplit

import requests

from mailko import resources
from mailko._version import __version__
from mailko.constants import (
    API_PREFIX,
    API_VERSION,
    DEFAULT_BASE_URL,
    IDEMPOTENCY_HEADER,
    PARTNER_KEY_PREFIXES,
    PROJECT_KEY_PREFIXES,
    VERSION_HEADER,
)
from mailko.errors import (
    APIConnectionError,
    ConfigurationError,
    IdempotencyError,
    RateLimitError,
    ServerError,
    error_from_response,
)

#: Network failures after which a request may be retried.
RETRYABLE_EXCEPTIONS = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
)
#: A 429 that no wait inside one call can fix.
NOT_RETRYABLE_429 = frozenset({"monthly_quota_exceeded"})
WRITE_METHODS = frozenset({"POST", "PATCH", "PUT", "DELETE"})


def _check_key(api_key: str) -> str:
    if not isinstance(api_key, str) or not api_key.strip():
        raise ConfigurationError("Falta la llave de Mailko (mk_live_… o mk_test_…).", code="no_key")
    api_key = api_key.strip()
    if not api_key.startswith(PROJECT_KEY_PREFIXES + PARTNER_KEY_PREFIXES):
        raise ConfigurationError(
            "La llave de Mailko debe empezar por mk_live_ o mk_test_ (o mkp_ si es de una app socia). "
            "Créala en Configuración → Desarrolladores.",
            code="invalid_key",
        )
    return api_key


def _check_base_url(base_url: str) -> str:
    """https always, except a development host: the key travels in every request."""
    parts = urlsplit(str(base_url).strip())
    host = (parts.hostname or "").lower()
    local = host in ("localhost", "127.0.0.1", "::1") or host.endswith((".localhost", ".test"))
    if not host or parts.scheme not in ("https", "http") or (parts.scheme == "http" and not local):
        raise ConfigurationError(
            f"base_url debe empezar por https:// (por ejemplo {DEFAULT_BASE_URL}).", code="invalid_base_url"
        )
    return str(base_url).strip().rstrip("/")


def _query_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime.date | datetime.datetime):
        return value.isoformat()
    return str(value)


def _query(params: Mapping[str, Any] | None) -> dict[str, str]:
    """Query parameters as Mailko reads them: booleans as ``true``/``false``,
    dates in ISO 8601, a list as one comma-separated value (``status=a,b``),
    ``None`` left out."""
    clean: dict[str, str] = {}
    for key, value in (params or {}).items():
        if value is None:
            continue
        if isinstance(value, list | tuple | set | frozenset):
            clean[key] = ",".join(_query_value(item) for item in value)
        else:
            clean[key] = _query_value(value)
    return clean


class Mailko:
    """``Mailko("mk_live_…")``: one client per key, thread-safe for requests.

    ``client.emails.send(...)``, ``client.domains.list()``… — every resource
    of ``/v1`` hangs from the client."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float | tuple[float, float] = (5.0, 30.0),
        max_retries: int = 2,
        max_retry_after: float = 10.0,
        session: requests.Session | None = None,
        user_agent: str | None = None,
    ) -> None:
        self.api_key = _check_key(api_key)
        self.livemode = not self.api_key.startswith(("mk_test_", "mkp_test_"))
        self.is_partner = self.api_key.startswith(PARTNER_KEY_PREFIXES)
        self.base_url = _check_base_url(base_url or DEFAULT_BASE_URL)
        if self.base_url.endswith(API_PREFIX):
            self.base_url = self.base_url[: -len(API_PREFIX)]
        self.timeout = timeout
        self.max_retries = max(0, int(max_retries))
        #: A ``Retry-After`` longer than this is not waited for: the error is raised.
        self.max_retry_after = max(0.0, float(max_retry_after))
        self.session = session or requests.Session()
        self.user_agent = user_agent
        self._sleep: Callable[[float], None] = time.sleep

        self.me = resources.Me(self)
        self.emails = resources.Emails(self)
        self.domains = resources.Domains(self)
        self.suppressions = resources.Suppressions(self)
        self.templates = resources.Templates(self)
        self.usage = resources.Usage(self)
        self.webhook_endpoints = resources.WebhookEndpoints(self)
        self.webhook_events = resources.WebhookEvents(self)
        self.contacts = resources.Contacts(self)
        self.lists = resources.Lists(self)
        self.segments = resources.Segments(self)
        self.campaigns = resources.Campaigns(self)
        self.imports = resources.Imports(self)
        self.partner = resources.Partner(self)

    def __repr__(self) -> str:
        mode = "live" if self.livemode else "test"
        return f"Mailko(mode={mode!r}, base_url={self.base_url!r})"

    # -- requests -------------------------------------------------------------

    def _headers(self, idempotency_key: str | None, *, has_body: bool) -> dict[str, str]:
        agent = f"mailko-python/{__version__} (Python {platform.python_version()})"
        if self.user_agent:
            agent = f"{self.user_agent} {agent}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            VERSION_HEADER: API_VERSION,
            "User-Agent": agent,
        }
        if has_body:
            headers["Content-Type"] = "application/json"
        if idempotency_key:
            headers[IDEMPOTENCY_HEADER] = idempotency_key
        return headers

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(0.5 * (2**attempt), 8.0) * random.uniform(0.5, 1.0)

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
        timeout: float | tuple[float, float] | None = None,
        max_retries: int | None = None,
    ) -> Any:
        """One call to ``/v1{path}``, retried when safe. Answers the JSON
        (``None`` for a 204)."""
        method = method.upper()
        idempotency_key = str(idempotency_key or uuid.uuid4()) if method in WRITE_METHODS else None
        url = f"{self.base_url}{API_PREFIX}{path}"
        data = None
        if body is not None:
            data = jsonlib.dumps(dict(body), separators=(",", ":"), ensure_ascii=False, default=str).encode()
        headers = self._headers(idempotency_key, has_body=data is not None)
        retries = self.max_retries if max_retries is None else max(0, int(max_retries))
        wait = self.timeout if timeout is None else timeout

        attempt = 0
        while True:
            try:
                # Never follow a redirect: it would turn a POST into a GET
                # (and carry the key wherever it points).
                response = self.session.request(
                    method,
                    url,
                    params=_query(params),
                    data=data,
                    headers=headers,
                    timeout=wait,
                    allow_redirects=False,
                )
            except requests.exceptions.SSLError as exc:
                # A bad certificate stays bad: no retry.
                raise APIConnectionError(
                    "No se pudo establecer una conexión segura con Mailko (certificado TLS).", code="tls_error"
                ) from exc
            except RETRYABLE_EXCEPTIONS as exc:
                if attempt < retries:
                    self._sleep(self._backoff(attempt))
                    attempt += 1
                    continue
                raise APIConnectionError(
                    "Mailko no respondió. Inténtalo de nuevo con la misma idempotency_key.", code="unreachable"
                ) from exc
            except requests.exceptions.RequestException as exc:
                raise APIConnectionError(f"No se pudo llamar a Mailko: {type(exc).__name__}.") from exc

            if 300 <= response.status_code < 400:
                raise APIConnectionError(
                    f"Mailko respondió con una redirección ({response.status_code}). Revisa base_url: "
                    f"debe ser la dirección final y con https ({DEFAULT_BASE_URL}).",
                    code="redirected",
                )
            if response.status_code < 300:
                if response.status_code == 204 or not response.content:
                    return None
                try:
                    return response.json()
                except ValueError as exc:
                    raise ServerError(
                        "Mailko respondió algo que no es JSON.", status=response.status_code, body=response.text
                    ) from exc

            error = error_from_response(response)
            delay = self._retry_delay(error, attempt)
            if attempt < retries and delay is not None:
                self._sleep(delay)
                attempt += 1
                continue
            raise error

    def _retry_delay(self, error: Exception, attempt: int) -> float | None:
        """Seconds to wait before retrying ``error``, or ``None`` when it
        must be raised now."""
        if isinstance(error, ServerError):
            return self._backoff(attempt)
        if isinstance(error, IdempotencyError) and error.code == "idempotency_key_in_progress":
            return self._backoff(attempt)
        if isinstance(error, RateLimitError) and error.code not in NOT_RETRYABLE_429:
            if error.retry_after is None:
                return self._backoff(attempt)
            if error.retry_after <= self.max_retry_after:
                return error.retry_after
        return None

    def paginate(self, path: str, params: Mapping[str, Any] | None = None) -> Iterator[dict[str, Any]]:
        """Every item of a paginated list, page after page."""
        query = dict(params or {})
        page = int(query.pop("page", 1) or 1)
        while True:
            answer = self.request("GET", path, params={**query, "page": page}) or {}
            yield from answer.get("data") or []
            pagination = answer.get("pagination") or {}
            if not pagination.get("has_more"):
                return
            page += 1
