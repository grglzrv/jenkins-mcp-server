from __future__ import annotations

import asyncio
import random
from collections.abc import Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote, unquote, urlsplit

import httpx

from . import __version__
from .audit import AuditLogger, redact_query
from .config import Settings
from .diagnostics import JenkinsContact
from .security import Policy, PolicyError


class JenkinsError(RuntimeError):
    pass


TRAVERSAL_SEGMENTS = {".", ".."}

# Methods that are safe to replay against Jenkins. Although HTTP defines PUT
# and DELETE as idempotent, the generic administrator tool can route them to
# plugin endpoints whose side effects do not follow that contract.
REPLAY_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
RETRYABLE_STATUSES = frozenset({429, 502, 503, 504})
MAX_RETRY_DELAY_SECONDS = 30.0
CRUMB_REJECTION_MARKERS = (
    "no valid crumb",
    "invalid crumb",
    "csrf crumb",
)

BUILD_ALIASES = frozenset(
    {
        "lastBuild",
        "lastCompletedBuild",
        "lastFailedBuild",
        "lastStableBuild",
        "lastSuccessfulBuild",
        "lastUnstableBuild",
        "lastUnsuccessfulBuild",
    }
)

# Response headers withheld from admin_request callers. The MCP client is not
# the authenticated party: Jenkins issues the session to this server, and
# forwarding those values hands a caller a usable session and CSRF token.
SENSITIVE_RESPONSE_HEADERS = frozenset(
    {
        "set-cookie",
        "set-cookie2",
        "authorization",
        "proxy-authenticate",
        "www-authenticate",
        "jenkins-crumb",
        "x-jenkins-crumb",
        "x-csrf-token",
    }
)


def _node_path(node_name: str) -> str:
    """Encode a node name for a /computer/<name> URL.

    Jobs go through _job_path, which rejects an empty name. Nodes had no
    equivalent, so an empty name collapsed /computer/<name>/ to the collection
    endpoint: a read returned every node, and a toggle posted to /computer//
    and reported success for a node it never touched.
    """
    if not node_name or not node_name.strip():
        raise ValueError("node_name must not be empty")
    if any(part in TRAVERSAL_SEGMENTS for part in node_name.split("/")):
        raise ValueError("node_name must not contain '.' or '..' segments")
    return quote(node_name, safe="")


def _job_path(full_name: str) -> str:
    if not full_name.strip("/").strip():
        raise ValueError("job_name must not be empty")
    if full_name != full_name.strip("/") or "//" in full_name:
        raise ValueError(
            "job_name must not have leading, trailing, or repeated '/' separators"
        )
    parts = full_name.split("/")
    if any(part in TRAVERSAL_SEGMENTS for part in parts):
        raise ValueError(f"job_name must not contain '.' or '..' path segments: {full_name!r}")
    return "/".join(f"job/{quote(part, safe='')}" for part in parts)


def _build_selector(build_number: int | str) -> str:
    """Validate the path component used to address a Jenkins build."""
    if isinstance(build_number, bool):
        raise ValueError("build_number must be a positive integer or Jenkins alias")
    if isinstance(build_number, int):
        if build_number < 1:
            raise ValueError("build_number must be positive")
        return str(build_number)
    if build_number in BUILD_ALIASES:
        return build_number
    if build_number.isdecimal() and int(build_number) > 0:
        return str(int(build_number))
    raise ValueError("build_number must be a positive integer or a supported Jenkins build alias")


def _new_job_parts(job_name: str) -> tuple[str, str]:
    """Return a canonical parent/name pair for job creation."""
    if job_name != job_name.strip("/") or "//" in job_name:
        raise ValueError(
            "new job names must not have leading, trailing, or repeated '/' separators"
        )
    _job_path(job_name)
    parent, _, leaf = job_name.rpartition("/")
    if not leaf:
        raise ValueError("new job name must not be empty")
    return parent, leaf


def _queue_job_name(item: dict[str, Any]) -> str | None:
    task = item.get("task")
    if not isinstance(task, dict):
        return None
    full_name = task.get("fullName")
    if isinstance(full_name, str) and full_name:
        return full_name
    from_url = _job_name_from_url(task.get("url"))
    if from_url:
        return from_url
    name = task.get("name")
    return name if isinstance(name, str) and name else None


def _job_name_from_url(url: Any) -> str | None:
    if not isinstance(url, str):
        return None
    # Decode exactly once, matching the URI decoding applied before Jenkins'
    # route dispatch. Splitting first and decoding each component afterwards
    # misses encoded separators and can make the policy inspect a different
    # resource from the one Jenkins receives.
    segments = [part for part in unquote(urlsplit(url).path).split("/") if part]
    names = [segments[index + 1] for index, part in enumerate(segments[:-1]) if part == "job"]
    return "/".join(names) or None


def _build_job_name(executable: dict[str, Any]) -> str | None:
    """Extract a nested Jenkins job name from a build URL."""
    return _job_name_from_url(executable.get("url"))


def _is_crumb_rejection(text: str) -> bool:
    """Match Jenkins CSRF rejection phrases, not any mention of a crumb."""
    detail = text[:2000].casefold()
    return any(marker in detail for marker in CRUMB_REJECTION_MARKERS)


class JenkinsClient:
    def __init__(
        self,
        settings: Settings,
        policy: Policy,
        audit: AuditLogger,
        transport: httpx.AsyncBaseTransport | None = None,
        contact: JenkinsContact | None = None,
    ) -> None:
        self.settings = settings
        self.policy = policy
        self.audit = audit
        # Bound in-flight requests, not just connections: the semaphore holds
        # regardless of transport, and the pool is sized to match so a waiting
        # request queues here rather than on a connection it cannot get.
        self._concurrency = asyncio.Semaphore(settings.jenkins_max_concurrency)
        self.http = httpx.AsyncClient(
            base_url=settings.jenkins_url,
            auth=(settings.jenkins_username, settings.jenkins_token),
            verify=settings.verify,
            timeout=settings.jenkins_timeout_seconds,
            limits=httpx.Limits(
                max_connections=settings.jenkins_max_concurrency,
                max_keepalive_connections=min(settings.jenkins_max_concurrency, 20),
                keepalive_expiry=5.0,
            ),
            follow_redirects=False,
            transport=transport,
            headers={"User-Agent": f"jenkins-mcp-server/{__version__}"},
        )
        self._crumb: tuple[str, str] | None = None
        # Set when the controller has no crumb issuer, so the probe runs once.
        self._crumb_disabled = False
        # Concurrent writes would otherwise each fetch their own crumb: eight
        # parallel triggers meant eight crumb requests against a controller
        # that is often the slow part.
        self._crumb_lock = asyncio.Lock()
        # Tests and library callers get isolated diagnostics by default. The
        # application injects one process-level instance shared with /readyz.
        self.contact = contact or JenkinsContact()

    async def close(self) -> None:
        await self.http.aclose()

    async def _record_policy_denial(
        self,
        check: str,
        target: str,
        reason: str,
        **fields: Any,
    ) -> None:
        """Persist a refusal before returning it to the MCP caller.

        Policy checks are synchronous, but file audit is deliberately off-loop.
        Awaiting the write avoids detached tasks that can be lost during normal
        shutdown and bounds denial-write concurrency to active MCP requests.
        """
        await self.audit.emit_async(
            "policy.denied",
            "denied",
            check=check,
            target=target,
            reason=reason,
            **fields,
        )

    async def _deny_policy(
        self,
        check: str,
        target: str,
        reason: str,
        **fields: Any,
    ) -> None:
        await self._record_policy_denial(check, target, reason, **fields)
        raise PolicyError(reason)

    async def _check_job(self, job_name: str) -> None:
        try:
            self.policy.check_job(job_name)
        except PolicyError as exc:
            await self._record_policy_denial(
                "check_job",
                job_name,
                str(exc),
                job=job_name,
            )
            raise

    async def _require_write(
        self,
        category: str,
        job_name: str | None = None,
        *,
        target: str | None = None,
    ) -> None:
        try:
            self.policy.require_write(category, job_name)
        except PolicyError as exc:
            fields: dict[str, Any] = {"category": category}
            if job_name is not None:
                fields["job"] = job_name
            await self._record_policy_denial(
                "require_write",
                target or job_name or category,
                str(exc),
                **fields,
            )
            raise

    async def _require_destructive(
        self,
        action: str,
        job_name: str | None = None,
        *,
        target: str | None = None,
    ) -> None:
        try:
            self.policy.require_destructive(action, job_name)
        except PolicyError as exc:
            fields: dict[str, Any] = {"policy_action": action}
            if job_name is not None:
                fields["job"] = job_name
            await self._record_policy_denial(
                "require_destructive",
                target or job_name or action,
                str(exc),
                **fields,
            )
            raise

    async def _get_crumb(
        self,
        stale: tuple[str, str] | None = None,
        *,
        force: bool = False,
    ) -> tuple[str, str] | None:
        if not force and stale is None and self._crumb is not None:
            return self._crumb
        if not force and stale is None and self._crumb_disabled:
            return None
        async with self._crumb_lock:
            # A second request may have refreshed the stale crumb while this
            # caller waited. Reuse that value instead of rotating it again.
            if stale is not None:
                if self._crumb is not None and self._crumb != stale:
                    return self._crumb
                self._crumb = None
            elif self._crumb is not None:
                return self._crumb
            if force:
                # A crumb-related 403 proves that a previously observed 404
                # was transient. Clear the negative cache under the same lock
                # used for the fetch so concurrent recoveries share one probe.
                self._crumb_disabled = False
            if self._crumb_disabled:
                return None
            try:
                response = await self._send(
                    "GET",
                    "/crumbIssuer/api/json",
                    {},
                    self.settings.max_response_bytes,
                )
            except httpx.TransportError as exc:
                raise JenkinsError(f"Could not reach the Jenkins crumb issuer: {exc}") from exc
            if response.status_code == 404:
                # CSRF protection is off on this controller. Remember it,
                # otherwise every write pays for a 404 round trip first.
                self._crumb_disabled = True
                return None
            if response.status_code >= 400:
                # Surfacing httpx.HTTPStatusError here leaks the transport
                # exception type through tools that only document JenkinsError.
                raise JenkinsError(
                    f"Jenkins crumb issuer returned {response.status_code}. "
                    "Check that the account can authenticate."
                )
            if response.extensions.get("jenkins_mcp_truncated", False):
                raise JenkinsError(
                    "Jenkins crumb issuer response exceeded MCP_MAX_RESPONSE_BYTES "
                    f"({self.settings.max_response_bytes} bytes)"
                )
            try:
                data = response.json()
                field = data["crumbRequestField"]
                value = data["crumb"]
                if (
                    not isinstance(field, str)
                    or not field
                    or not isinstance(value, str)
                    or not value
                ):
                    raise TypeError("crumb fields must be strings")
                self._crumb = (field, value)
            except (KeyError, TypeError, ValueError) as exc:
                raise JenkinsError("Jenkins crumb issuer returned malformed JSON") from exc
            return self._crumb

    def _may_retry(self, method: str, status_code: int) -> bool:
        """Whether replaying this request risks applying it twice.

        Jenkins has no idempotency key, so a replayed POST is a second build,
        a second job deletion, a second node update.
        """
        if status_code not in RETRYABLE_STATUSES:
            return False
        return method.upper() in REPLAY_SAFE_METHODS

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        """Use Retry-After when supplied, with a bounded operational wait."""
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                delay = float(retry_after)
            except ValueError:
                try:
                    when = parsedate_to_datetime(retry_after)
                    if when.tzinfo is None:
                        when = when.replace(tzinfo=UTC)
                    delay = (when - datetime.now(UTC)).total_seconds()
                except (TypeError, ValueError, OverflowError):
                    delay = 0
            if delay > 0:
                return min(delay, MAX_RETRY_DELAY_SECONDS)
        return JenkinsClient._backoff_delay(attempt)

    @staticmethod
    def _backoff_delay(attempt: int) -> float:
        """Full jitter prevents replicas retrying Jenkins in lockstep."""
        return random.uniform(0, min(2**attempt, 5))

    async def _send(
        self,
        method: str,
        path: str,
        request_kwargs: dict[str, Any],
        response_limit: int | None,
    ) -> httpx.Response:
        # HTTPX timeouts start only after entering the transport. Bound the
        # application queue separately; otherwise N saturated batches could
        # make a caller wait N * timeoutSeconds before its request even starts.
        try:
            async with asyncio.timeout(self.settings.jenkins_timeout_seconds):
                await self._concurrency.acquire()
        except TimeoutError as exc:
            raise httpx.PoolTimeout(
                "Timed out waiting for a Jenkins concurrency slot"
            ) from exc
        try:
            return await self._send_unbounded(method, path, request_kwargs, response_limit)
        finally:
            self._concurrency.release()

    async def _send_unbounded(
        self,
        method: str,
        path: str,
        request_kwargs: dict[str, Any],
        response_limit: int | None,
    ) -> httpx.Response:
        try:
            if response_limit is None:
                response = await self.http.request(method, path, **request_kwargs)
            else:
                truncated = False
                content = bytearray()
                async with self.http.stream(method, path, **request_kwargs) as streamed:
                    async for chunk in streamed.aiter_bytes():
                        remaining = response_limit - len(content)
                        if remaining <= 0:
                            truncated = True
                            break
                        content.extend(chunk[:remaining])
                        if len(chunk) > remaining:
                            truncated = True
                            break
                    # aiter_bytes() has already decoded content encodings.
                    # Carrying gzip/br (or the original length) onto the
                    # synthetic response would decode plain bytes twice.
                    response_headers = [
                        (name, value)
                        for name, value in streamed.headers.multi_items()
                        if name.lower()
                        not in {"content-encoding", "content-length", "transfer-encoding"}
                    ]
                    response = httpx.Response(
                        streamed.status_code,
                        headers=response_headers,
                        content=bytes(content),
                        request=streamed.request,
                        extensions={
                            **streamed.extensions,
                            "jenkins_mcp_truncated": truncated,
                        },
                    )
        except httpx.TransportError as exc:
            # Instrument the common transport path so crumb-issuer failures are
            # visible too; request() never runs its own send in that case.
            self.contact.record_failure(exc)
            raise
        self.contact.record_contact()
        return response

    @staticmethod
    def _encode_request_body(
        data: Any,
        json_body: Any,
    ) -> tuple[bytes | None, str | None]:
        """Serialize once, returning the exact body and generated media type.

        Form values must be encoded before measuring. Counting their input
        characters lets reserved and non-ASCII characters expand past the cap
        during application/x-www-form-urlencoded serialization. Reusing the
        resulting bytes prevents a second serializer from drifting or doubling
        peak memory for JSON and form bodies.
        """
        if data is None and json_body is None:
            return None, None
        if isinstance(data, bytearray):
            data = bytes(data)
        if isinstance(data, str | bytes):
            request = httpx.Request(
                "POST", "http://request-size.invalid/", content=data
            )
        elif isinstance(data, Mapping):
            request = httpx.Request(
                "POST", "http://request-size.invalid/", data=data
            )
        elif data is not None:
            # Preserve the generic internal request API while forcing any
            # iterable body to be consumed and bounded before Jenkins contact.
            request = httpx.Request(
                "POST", "http://request-size.invalid/", data=data
            )
            request.read()
        else:
            request = httpx.Request(
                "POST", "http://request-size.invalid/", json=json_body
            )
        return request.content, request.headers.get("Content-Type")

    async def _enforce_target_size(self, action: str, path: str) -> None:
        """Refuse a request line longer than the configured cap.

        The body cap does not cover this. A job name is a URL component, so a
        deeply nested name expands into the request target rather than the
        body: 2000 segments produced a 12 KB URL, above the 8 KB default of
        nginx and of many reverse proxies, so the request is rejected at the
        proxy rather than by Jenkins and the failure is opaque. httpx refuses
        somewhere above 16 KB, which is well past the point where a real
        deployment has already broken.
        """
        limit = self.settings.max_request_target_bytes
        size = len(path.encode("utf-8"))
        if size <= limit:
            return
        await self.audit.emit_async(
            action,
            "failure",
            status="request_target_too_long",
            path=path,
            target_bytes=size,
            target_limit_bytes=limit,
        )
        raise ValueError(
            f"Request target for {action} is {size} bytes, over the "
            f"{limit} byte MCP_MAX_REQUEST_TARGET_BYTES limit. The name or "
            "path is too deeply nested."
        )

    async def _enforce_request_size(
        self,
        action: str,
        path: str,
        body: bytes | None,
    ) -> None:
        """Refuse and audit a body larger than the configured cap."""
        size = len(body) if body is not None else None
        limit = self.settings.max_request_bytes
        if size is None or size <= limit:
            return
        await self.audit.emit_async(
            action,
            "failure",
            status="request_too_large",
            path=path,
            request_bytes=size,
            request_limit_bytes=limit,
        )
        raise ValueError(
            f"Request body for {action} is {size} bytes, over the "
            f"{limit} byte MCP_MAX_REQUEST_BYTES limit. Raise the limit "
            "or send a smaller definition."
        )

    async def request(
        self,
        method: str,
        path: str,
        *,
        action: str,
        params: dict[str, Any] | None = None,
        data: Any = None,
        json: Any = None,
        headers: dict[str, str] | None = None,
        expected: tuple[int, ...] = (200,),
        response_limit: int | None = None,
        allow_truncated: bool = False,
    ) -> httpx.Response:
        method_upper = method.upper()
        encoded_body, generated_content_type = self._encode_request_body(data, json)
        # Checked before the crumb is fetched, so an oversized call costs no
        # round trip and never reaches the controller.
        await self._enforce_target_size(action, path)
        await self._enforce_request_size(action, path, encoded_body)
        request_headers = dict(headers or {})
        if generated_content_type:
            request_headers.setdefault("Content-Type", generated_content_type)
        crumb: tuple[str, str] | None = None
        if method_upper not in REPLAY_SAFE_METHODS:
            try:
                crumb = await self._get_crumb()
            except JenkinsError as exc:
                await self.audit.emit_async(
                    action,
                    "failure",
                    status="crumb",
                    path=path,
                    error=type(exc).__name__,
                )
                raise
            if crumb:
                request_headers[crumb[0]] = crumb[1]

        last_error: Exception | None = None
        crumb_refreshed = False
        attempt = 0
        while attempt <= self.settings.jenkins_max_retries:
            try:
                request_kwargs: dict[str, Any] = {
                    "params": params,
                    "headers": request_headers,
                }
                if encoded_body is not None:
                    request_kwargs["content"] = encoded_body

                response = await self._send(
                    method,
                    path,
                    request_kwargs,
                    response_limit
                    if response_limit is not None
                    else self.settings.max_response_bytes,
                )
                if response.status_code in expected:
                    if (
                        response.extensions.get("jenkins_mcp_truncated", False)
                        and not allow_truncated
                    ):
                        limit = (
                            response_limit
                            if response_limit is not None
                            else self.settings.max_response_bytes
                        )
                        await self.audit.emit_async(
                            action,
                            "failure",
                            status="response_too_large",
                            path=path,
                        )
                        raise JenkinsError(
                            f"Jenkins response exceeded MCP_MAX_RESPONSE_BYTES ({limit} bytes)"
                        )
                    await self.audit.emit_async(
                        action,
                        "success",
                        status=response.status_code,
                        path=path,
                    )
                    return response

                # A cached crumb goes stale when Jenkins rotates the session,
                # and a 404 from the crumb issuer during a restart or a proxy
                # blip makes us conclude CSRF is off. Jenkins asking for a crumb
                # disproves both, so re-issue once rather than surfacing a hard
                # 403. Without this the wrong conclusion is permanent: nothing
                # re-probes the issuer, readiness does not test it, so every
                # write fails until the process restarts.
                if (
                    response.status_code == 403
                    and method_upper not in REPLAY_SAFE_METHODS
                    and not crumb_refreshed
                    and _is_crumb_rejection(response.text)
                ):
                    crumb_refreshed = True
                    try:
                        crumb = await self._get_crumb(stale=crumb, force=True)
                    except JenkinsError as exc:
                        await self.audit.emit_async(
                            action,
                            "failure",
                            status="crumb",
                            path=path,
                            error=type(exc).__name__,
                        )
                        raise
                    if crumb:
                        request_headers[crumb[0]] = crumb[1]
                        continue

                if (
                    self._may_retry(method, response.status_code)
                    and attempt < self.settings.jenkins_max_retries
                ):
                    await asyncio.sleep(self._retry_delay(response, attempt))
                    attempt += 1
                    continue

                body = response.text[:2000]
                await self.audit.emit_async(
                    action,
                    "failure",
                    status=response.status_code,
                    path=path,
                )
                if 300 <= response.status_code < 400:
                    hint = (
                        " Unexpected redirect; check the JENKINS_URL context path, "
                        "reverse-proxy authentication, and SSO bypass for API tokens."
                    )
                elif response.status_code == 401:
                    hint = " Authentication failed; verify JENKINS_USERNAME and its API token."
                elif response.status_code == 403:
                    hint = " Permission denied; check the Jenkins account's permissions."
                    # A 403 is not inherently a CSRF failure. Read requests do
                    # not use crumbs, and Jenkins also returns 403 for missing
                    # Job/* or Overall/* permissions. Mention proxy/crumb
                    # troubleshooting only when Jenkins actually identifies a
                    # crumb problem; otherwise the old advice sent operators
                    # toward an unrelated proxy setting.
                    if _is_crumb_rejection(body):
                        hint += (
                            " Jenkins also rejected the CSRF crumb; check that the proxy "
                            "preserves its header and, when source NAT is involved, review "
                            "Strict Crumb Issuer's client-IP check."
                        )
                else:
                    hint = ""
                detail = f": {body}" if body else ""
                raise JenkinsError(
                    f"Jenkins returned {response.status_code}{detail}.{hint}".rstrip()
                )
            except httpx.TransportError as exc:
                last_error = exc
                # Connection establishment and pool acquisition fail before a
                # request is sent. Read/write/protocol failures can arrive after
                # Jenkins accepted it, so only replay safe methods then.
                safe_to_replay = method_upper in REPLAY_SAFE_METHODS or isinstance(
                    exc,
                    httpx.ConnectError | httpx.ConnectTimeout | httpx.PoolTimeout,
                )
                if not safe_to_replay or attempt >= self.settings.jenkins_max_retries:
                    break
                await asyncio.sleep(self._backoff_delay(attempt))
                attempt += 1

        await self.audit.emit_async(
            action,
            "failure",
            status="network",
            path=path,
            error=type(last_error).__name__ if last_error else "unknown",
        )
        # Transport exceptions can include the request URL. Do not return a
        # caller-supplied query credential through the MCP error channel.
        error_detail = redact_query(str(last_error)) if last_error else "unknown"
        raise JenkinsError(f"Jenkins request failed: {error_detail}")

    async def api(
        self,
        path: str,
        depth: int = 1,
        tree: str | None = None,
    ) -> Any:
        params: dict[str, Any] = {"depth": depth}
        if tree:
            params["tree"] = tree
        normalized = path.strip("/")
        endpoint = f"/{normalized}/api/json" if normalized else "/api/json"
        response = await self.request(
            "GET",
            endpoint,
            action="api.get",
            params=params,
        )
        try:
            return response.json()
        except ValueError as exc:
            raise JenkinsError(f"Jenkins returned malformed JSON for {endpoint}") from exc

    async def list_jobs(self, folder: str | None = None) -> Any:
        if folder:
            try:
                folder_allowed = self.policy.allows_job_or_descendant(folder)
            except PolicyError as exc:
                await self._record_policy_denial(
                    "allows_job_or_descendant",
                    folder,
                    str(exc),
                    job=folder,
                )
                raise
            if not folder_allowed:
                await self._deny_policy(
                    "allows_job_or_descendant",
                    folder,
                    f"Job folder '{folder}' is not allowed by MCP_ALLOWED_JOBS",
                    job=folder,
                )
        path = _job_path(folder) if folder else ""
        result = await self.api(path, tree="jobs[name,fullName,url,color,_class]")
        if not isinstance(result, dict) or not isinstance(result.get("jobs"), list):
            return result
        visible: list[Any] = []
        for entry in result["jobs"]:
            if not isinstance(entry, dict):
                continue
            name = entry.get("fullName") or entry.get("name")
            if not isinstance(name, str) or not name:
                continue
            if folder and "/" not in name:
                name = f"{folder.strip('/')}/{name}"
            try:
                if self.policy.allows_job_or_descendant(name):
                    visible.append(entry)
            except PolicyError:
                continue
        return {**result, "jobs": visible}

    async def get_job(self, job_name: str) -> Any:
        await self._check_job(job_name)
        return await self.api(_job_path(job_name), depth=2)

    async def get_job_config(self, job_name: str) -> str:
        await self._check_job(job_name)
        response = await self.request(
            "GET",
            f"/{_job_path(job_name)}/config.xml",
            action="job.config.get",
        )
        return response.text

    async def create_job(
        self,
        job_name: str,
        config_xml: str,
    ) -> dict[str, Any]:
        parent, leaf = _new_job_parts(job_name)
        await self._require_write("job", job_name)
        path = f"/{_job_path(parent)}/createItem" if parent else "/createItem"
        await self.request(
            "POST",
            path,
            action="job.create",
            params={"name": leaf},
            data=config_xml.encode(),
            headers={"Content-Type": "application/xml"},
            expected=(200,),
        )
        return {"created": job_name}

    async def update_job(
        self,
        job_name: str,
        config_xml: str,
    ) -> dict[str, Any]:
        await self._require_destructive("job.update", job_name)
        await self.request(
            "POST",
            f"/{_job_path(job_name)}/config.xml",
            action="job.update",
            data=config_xml.encode(),
            headers={"Content-Type": "application/xml"},
            expected=(200,),
        )
        return {"updated": job_name}

    async def delete_job(self, job_name: str) -> dict[str, Any]:
        await self._require_destructive("job.delete", job_name)
        await self.request(
            "POST",
            f"/{_job_path(job_name)}/doDelete",
            action="job.delete",
            expected=(200, 302),
        )
        return {"deleted": job_name}

    async def enable_job(
        self,
        job_name: str,
        enabled: bool,
    ) -> dict[str, Any]:
        await self._require_write("job", job_name)
        operation = "enable" if enabled else "disable"
        await self.request(
            "POST",
            f"/{_job_path(job_name)}/{operation}",
            action=f"job.{operation}",
            expected=(200, 302),
        )
        return {"job": job_name, "enabled": enabled}

    async def copy_job(self, source: str, target: str) -> dict[str, Any]:
        parent, leaf = _new_job_parts(target)
        await self._check_job(source)
        await self._require_write("job", target)
        path = f"/{_job_path(parent)}/createItem" if parent else "/createItem"
        await self.request(
            "POST",
            path,
            action="job.copy",
            params={"name": leaf, "mode": "copy", "from": source},
            expected=(200, 302),
        )
        return {"source": source, "target": target}

    async def build(
        self,
        job_name: str,
        parameters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        await self._require_write("build", job_name)
        # `is not None` rather than truthiness: an explicitly empty dict means
        # "this job is parameterised, use the defaults". Falling back to /build
        # there makes Jenkins reject the trigger on a parameterised job.
        endpoint = "buildWithParameters" if parameters is not None else "build"
        response = await self.request(
            "POST",
            f"/{_job_path(job_name)}/{endpoint}",
            action="build.trigger",
            data=parameters,
            expected=(201, 302),
        )
        return {"queued": True, "queue_url": response.headers.get("Location")}

    async def stop_build(
        self,
        job_name: str,
        build_number: int,
        mode: str = "stop",
    ) -> dict[str, Any]:
        await self._require_destructive("build.stop", job_name)
        if isinstance(build_number, bool) or build_number < 1:
            raise ValueError("build_number must be positive")
        if mode not in {"stop", "term", "kill"}:
            raise ValueError("mode must be stop, term, or kill")
        await self.request(
            "POST",
            f"/{_job_path(job_name)}/{build_number}/{mode}",
            action=f"build.{mode}",
            expected=(200, 302),
        )
        return {
            "job": job_name,
            "build_number": build_number,
            "action": mode,
        }

    async def build_info(
        self,
        job_name: str,
        build_number: int | str = "lastBuild",
    ) -> Any:
        await self._check_job(job_name)
        selector = _build_selector(build_number)
        return await self.api(f"{_job_path(job_name)}/{selector}", depth=2)

    async def console(
        self,
        job_name: str,
        build_number: int | str = "lastBuild",
        start: int = 0,
    ) -> dict[str, Any]:
        await self._check_job(job_name)
        selector = _build_selector(build_number)
        if start < 0:
            raise ValueError("start must not be negative")
        response = await self.request(
            "GET",
            f"/{_job_path(job_name)}/{selector}/logText/progressiveText",
            action="build.console",
            params={"start": start},
            response_limit=self.settings.max_log_bytes,
            allow_truncated=True,
        )
        raw = response.content
        truncated = bool(response.extensions.get("jenkins_mcp_truncated", False))
        if truncated:
            # Jenkins' X-Text-Size counts everything it sent. Honouring it after
            # we clipped the body would make the next page skip the bytes we
            # dropped, so resume from what the caller actually received.
            next_start = start + len(raw)
            more_data = True
        else:
            next_start = int(response.headers.get("X-Text-Size", start + len(raw)))
            more_data = response.headers.get("X-More-Data", "false").lower() == "true"
        return {
            "text": raw.decode(response.encoding or "utf-8", errors="replace"),
            "next_start": next_start,
            "more_data": more_data,
            "truncated": truncated,
        }

    async def queue(self) -> Any:
        result = await self.api("queue", depth=2)
        if not isinstance(result, dict) or not isinstance(result.get("items"), list):
            return result
        visible: list[Any] = []
        for item in result["items"]:
            if not isinstance(item, dict):
                continue
            job_name = _queue_job_name(item)
            if job_name and self.policy.allows_job(job_name):
                visible.append(item)
        return {**result, "items": visible}

    async def cancel_queue(self, item_id: int) -> dict[str, Any]:
        if isinstance(item_id, bool) or item_id < 1:
            raise ValueError("item_id must be positive")
        await self._require_destructive(
            "queue.cancel",
            target=str(item_id),
        )
        item = await self.api(f"queue/item/{item_id}", depth=1)
        job_name = _queue_job_name(item) if isinstance(item, dict) else None
        if not job_name:
            await self._deny_policy(
                "queue_item_job",
                str(item_id),
                "Queue item does not identify a Jenkins job",
                queue_item=item_id,
            )
        assert job_name is not None
        await self._check_job(job_name)
        await self.request(
            "POST",
            "/queue/cancelItem",
            action="queue.cancel",
            params={"id": item_id},
            expected=(200, 302),
        )
        return {"cancelled": item_id}

    async def running_builds(self) -> list[dict[str, Any]]:
        data = await self.api("computer", depth=2)
        running: list[dict[str, Any]] = []
        for computer in data.get("computer", []):
            executors = [
                *computer.get("executors", []),
                *computer.get("oneOffExecutors", []),
            ]
            for executor in executors:
                current = executor.get("currentExecutable")
                if current:
                    job_name = _build_job_name(current)
                    if not job_name or not self.policy.allows_job(job_name):
                        continue
                    running.append({"node": computer.get("displayName"), **current})
        return running

    async def nodes(self) -> Any:
        return await self.api("computer", depth=2)

    async def node_info(self, node_name: str) -> Any:
        return await self.api(f"computer/{_node_path(node_name)}", depth=2)

    async def toggle_node(
        self,
        node_name: str,
        offline: bool,
        message: str = "Managed by MCP",
    ) -> dict[str, Any]:
        if offline:
            await self._require_destructive(
                "node.offline",
                target=node_name,
            )
        else:
            await self._require_write("node", target=node_name)
        encoded_node = _node_path(node_name)
        current = await self.node_info(node_name)
        if bool(current.get("temporarilyOffline")) != offline:
            await self.request(
                "POST",
                f"/computer/{encoded_node}/toggleOffline",
                action="node.toggle",
                params={"offlineMessage": message},
                expected=(200, 302),
            )
        return {"node": node_name, "offline": offline}

    async def scan_multibranch(self, job_name: str) -> dict[str, Any]:
        await self._require_write("job", job_name)
        await self.request(
            "POST",
            f"/{_job_path(job_name)}/build",
            action="multibranch.scan",
            expected=(201, 302),
        )
        return {"scan_triggered": job_name}

    async def _check_admin_path(self, path: str) -> None:
        """Apply the policy an arbitrary path would otherwise walk around.

        jenkins_admin_request exists to reach endpoints no other tool covers,
        but it inherited none of their limits. A path under /job/ addresses a
        job, so MCP_ALLOWED_JOBS has to apply, or an allowlist of AI/* is no
        boundary at all once this tool is enabled. The Groovy console is a
        second decision again: it runs arbitrary code on the controller, and
        Minibridge's sensitive-pattern guardrail refuses it when enabled, so
        the layer that always applies should not be the weaker one.
        """
        # httpx and Jenkins decode URI escapes before exposing/routing the
        # request path. Apply policy to that same single-decoded form so
        # /%6aob/... and /%73criptText cannot evade literal string checks.
        decoded_path = unquote(path)
        if "\\" in decoded_path:
            raise ValueError("path must not contain backslash separators")
        if ";" in decoded_path:
            raise ValueError("path must not contain semicolon path parameters")
        if any(ord(character) < 32 or ord(character) == 127 for character in decoded_path):
            raise ValueError("path must not contain control characters")
        if any(part in TRAVERSAL_SEGMENTS for part in decoded_path.split("/")):
            raise ValueError("path must not contain '.' or '..' segments")

        # Compare the decoded route name rather than only an exact raw spelling.
        first_segment = next(
            (part.casefold() for part in decoded_path.split("/") if part),
            "",
        )
        if not self.policy.allow_script_console and first_segment in {
            "script",
            "scripttext",
        }:
            reason = (
                f"Path '{path}' is the Jenkins script console, which runs "
                "arbitrary code on the controller. Enable "
                "MCP_ALLOW_SCRIPT_CONSOLE to permit it."
            )
            await self._deny_policy("script_console", path, reason, path=path)

        # Jenkins also exposes jobs through view URLs such as
        # /view/All/job/name. Reuse the same parser as queue/running-build URL
        # filtering so aliases cannot drift into a second allowlist bypass.
        job_name = _job_name_from_url(path)
        if job_name:
            await self._check_job(job_name)

    async def admin_request(
        self,
        method: str,
        path: str,
        body: str | None = None,
        content_type: str = "application/json",
    ) -> dict[str, Any]:
        # Do not include the caller's arbitrary path in this category denial:
        # it may contain a query string. Finer-grained path/job denials below
        # use only the parsed path or canonical job name.
        await self._require_write("admin")
        # Parse rather than pattern-match the caller's path. A structural check
        # rejects any scheme or authority outright, including forms that string
        # prefixes miss such as '//host/x', ' https://host' and 'HtTpS://host'.
        parsed = urlsplit(path)
        if parsed.scheme or parsed.netloc:
            raise ValueError("path must be a Jenkins-relative absolute path")
        if not parsed.path.startswith("/"):
            raise ValueError("path must be a Jenkins-relative absolute path")
        # _check_admin_path validates both policy and the decoded path shape.
        # urlsplit separates components but deliberately does not normalise or
        # percent-decode the path.
        await self._check_admin_path(parsed.path)
        # Rebuild from the parsed components so only what was validated is sent.
        path = parsed.path
        if parsed.query:
            path = f"{path}?{parsed.query}"
        response = await self.request(
            method.upper(),
            path,
            action="admin.request",
            data=body,
            headers={"Content-Type": content_type},
            expected=tuple(range(200, 400)),
            response_limit=self.settings.max_log_bytes,
            allow_truncated=True,
        )
        return {
            "status": response.status_code,
            "headers": {
                name: value
                for name, value in response.headers.items()
                if name.lower() not in SENSITIVE_RESPONSE_HEADERS
            },
            "body": response.text,
            "truncated": bool(response.extensions.get("jenkins_mcp_truncated", False)),
        }
