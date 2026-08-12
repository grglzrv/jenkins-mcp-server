"""Regression tests for path-traversal, log pagination, and CSRF crumb handling."""

import asyncio
import logging

import httpx
import pytest

from jenkins_mcp_server.audit import AuditLogger
from jenkins_mcp_server.client import JenkinsClient, JenkinsError, _job_path
from jenkins_mcp_server.config import Settings
from jenkins_mcp_server.diagnostics import JenkinsContact
from jenkins_mcp_server.security import Policy, PolicyError


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "JENKINS_URL": "https://jenkins.test",
        "JENKINS_USERNAME": "admin",
        "JENKINS_TOKEN": "token",
        "JENKINS_MAX_RETRIES": 0,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def policy(**overrides: object) -> Policy:
    values: dict[str, object] = {
        "read_only": False,
        "allow_job_write": True,
        "allow_build_write": True,
        "allow_node_write": True,
        "allow_admin_request": True,
        "job_patterns": ["*"],
    }
    values.update(overrides)
    return Policy(**values)  # type: ignore[arg-type]


def client(
    handler,
    *,
    contact: JenkinsContact | None = None,
    **setting_overrides: object,
) -> JenkinsClient:
    return JenkinsClient(
        settings(**setting_overrides),
        policy(),
        AuditLogger(None),
        transport=httpx.MockTransport(handler),
        contact=contact,
    )


def client_with_policy(handler, **policy_overrides: object) -> JenkinsClient:
    """A client whose Policy differs from the default. Policy is frozen."""
    return JenkinsClient(
        settings(JENKINS_MAX_RETRIES=0),
        policy(**policy_overrides),
        AuditLogger(None),
        transport=httpx.MockTransport(handler),
    )


# --- path traversal -------------------------------------------------------


@pytest.mark.parametrize(
    "job_name",
    ["..", ".", "AI/../Production/secret", "AI/./build", "../../scriptText"],
)
def test_job_path_rejects_traversal_segments(job_name: str) -> None:
    with pytest.raises(ValueError, match="path segments"):
        _job_path(job_name)


@pytest.mark.parametrize("job_name", ["/AI/build", "AI/build/", "AI//build"])
def test_job_path_rejects_ambiguous_separators(job_name: str) -> None:
    """Invalid separators must not silently select a different Jenkins job."""
    with pytest.raises(ValueError, match="leading, trailing, or repeated"):
        _job_path(job_name)


def test_job_path_still_accepts_normal_folder_nesting() -> None:
    assert _job_path("AI/team/build") == "job/AI/job/team/job/build"


def test_dotted_job_names_are_not_over_rejected() -> None:
    # Only the exact '.' and '..' segments are traversal; real jobs may contain dots.
    assert _job_path("AI/release.v2") == "job/AI/job/release.v2"


def test_policy_rejects_traversal_even_when_pattern_would_match() -> None:
    """'AI/../Production/x' matches the glob 'AI/*' but must not be allowed."""
    restricted = policy(job_patterns=["AI/*"])
    with pytest.raises(PolicyError, match="traversal"):
        restricted.check_job("AI/../Production/secret")


def test_policy_rejects_empty_job_name() -> None:
    with pytest.raises(PolicyError, match="must not be empty"):
        policy().check_job("/")


@pytest.mark.parametrize("job_name", ["/AI/build", "AI/build/", "AI//build"])
def test_policy_rejects_ambiguous_job_separators(job_name: str) -> None:
    with pytest.raises(PolicyError, match="leading, trailing, or repeated"):
        policy().check_job(job_name)


def test_policy_allows_legitimate_job_under_pattern() -> None:
    policy(job_patterns=["AI/*"]).check_job("AI/nightly-build")


def test_policy_allows_browsing_only_possible_allowlist_ancestors() -> None:
    restricted = policy(job_patterns=["AI/team/*"])
    assert restricted.allows_job_or_descendant("AI") is True
    assert restricted.allows_job_or_descendant("AI/team") is True
    assert restricted.allows_job_or_descendant("Production") is False


# --- admin request path validation ---------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "//evil.example/steal",
        "/safe/../../etc",
        "/./relative",
        "/safe/%2e%2e/etc",
        "/safe/%2E/etc",
        "/safe/%5c..%5cetc",
        "/job;matrix/Secret/doDelete",
        "/safe/%00/control",
    ],
)
async def test_admin_request_rejects_unsafe_paths(path: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("request must never be issued")

    jc = client(handler)
    with pytest.raises(ValueError):
        await jc.admin_request("GET", path)
    await jc.close()


# --- console log pagination ----------------------------------------------


@pytest.mark.asyncio
async def test_console_truncation_resumes_from_delivered_bytes() -> None:
    """A clipped page must not advance next_start past the bytes we returned."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"x" * 500,
            headers={"X-Text-Size": "500", "X-More-Data": "false"},
        )

    jc = client(handler, MCP_MAX_LOG_BYTES=100)
    result = await jc.console("AI/build", 7, start=0)
    assert len(result["text"]) == 100
    assert result["truncated"] is True
    assert result["next_start"] == 100, "must resume where the caller stopped reading"
    assert result["more_data"] is True
    await jc.close()


@pytest.mark.asyncio
async def test_console_untruncated_uses_jenkins_offset() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"hello",
            headers={"X-Text-Size": "1234", "X-More-Data": "true"},
        )

    jc = client(handler)
    result = await jc.console("AI/build", 7, start=0)
    assert result["truncated"] is False
    assert result["next_start"] == 1234
    assert result["more_data"] is True
    await jc.close()


@pytest.mark.asyncio
async def test_api_response_is_streamed_and_rejected_at_global_limit() -> None:
    """Large Jenkins JSON must not be buffered without a bound."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'{"jobs":[' + b" " * 2048)

    jc = client(handler, MCP_MAX_RESPONSE_BYTES=1024)
    with pytest.raises(JenkinsError, match=r"MCP_MAX_RESPONSE_BYTES \(1024 bytes\)"):
        await jc.list_jobs()
    await jc.close()


@pytest.mark.asyncio
async def test_malformed_api_json_is_wrapped_as_jenkins_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not-json")

    jc = client(handler)
    with pytest.raises(JenkinsError, match="malformed JSON for /api/json"):
        await jc.list_jobs()
    await jc.close()


@pytest.mark.asyncio
async def test_console_keeps_its_smaller_truncating_limit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 4096)

    jc = client(
        handler,
        MCP_MAX_LOG_BYTES=1024,
        MCP_MAX_RESPONSE_BYTES=2048,
    )
    result = await jc.console("AI/build", 7)
    assert len(result["text"]) == 1024
    assert result["truncated"] is True
    await jc.close()


@pytest.mark.asyncio
async def test_redirect_error_explains_context_path_and_proxy_causes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "/login"})

    jc = client(handler)
    with pytest.raises(JenkinsError, match="Unexpected redirect.*context path"):
        await jc.list_jobs()
    await jc.close()


@pytest.mark.asyncio
async def test_read_permission_403_does_not_suggest_crumb_troubleshooting() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="user is missing the Job/Read permission")

    jc = client(handler)
    with pytest.raises(JenkinsError) as error:
        await jc.list_jobs()
    message = str(error.value)
    assert "Permission denied" in message
    assert "crumb header" not in message
    await jc.close()


@pytest.mark.asyncio
async def test_crumb_403_keeps_targeted_proxy_troubleshooting() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/crumbIssuer/api/json":
            return httpx.Response(
                200,
                json={"crumbRequestField": "Jenkins-Crumb", "crumb": "stale"},
            )
        return httpx.Response(403, text="No valid crumb was included in the request")

    jc = client(handler)
    with pytest.raises(JenkinsError) as error:
        await jc.request("POST", "/job/AI/job/build/build", action="test")
    message = str(error.value)
    assert "rejected the CSRF crumb" in message
    assert "Strict Crumb Issuer" in message
    await jc.close()


# --- CSRF crumb refresh ---------------------------------------------------


@pytest.mark.asyncio
async def test_stale_crumb_is_reissued_and_request_retried() -> None:
    crumbs = iter(["stale-crumb", "fresh-crumb"])
    sent_crumbs: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/crumbIssuer/api/json":
            return httpx.Response(
                200,
                json={"crumbRequestField": "Jenkins-Crumb", "crumb": next(crumbs)},
            )
        sent_crumbs.append(request.headers["Jenkins-Crumb"])
        if request.headers["Jenkins-Crumb"] == "fresh-crumb":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(403, text="No valid crumb was included in the request")

    # max_retries=0 proves the crumb refresh does not consume a retry budget.
    jc = client(handler, JENKINS_MAX_RETRIES=0)
    response = await jc.request("POST", "/job/AI/job/build/build", action="test")
    assert response.status_code == 200
    assert sent_crumbs == ["stale-crumb", "fresh-crumb"]
    await jc.close()


@pytest.mark.asyncio
async def test_crumb_is_only_refreshed_once_before_failing() -> None:
    attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/crumbIssuer/api/json":
            return httpx.Response(
                200,
                json={"crumbRequestField": "Jenkins-Crumb", "crumb": "always-stale"},
            )
        attempts.append(request.url.path)
        return httpx.Response(403, text="No valid crumb was included in the request")

    jc = client(handler)
    with pytest.raises(Exception, match="403"):
        await jc.request("POST", "/job/AI/job/build/build", action="test")
    assert len(attempts) == 2, "one original attempt plus exactly one crumb retry"
    await jc.close()


@pytest.mark.asyncio
async def test_unrelated_403_is_not_treated_as_a_crumb_problem() -> None:
    attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/crumbIssuer/api/json":
            return httpx.Response(
                200,
                json={"crumbRequestField": "Jenkins-Crumb", "crumb": "c"},
            )
        attempts.append(request.url.path)
        return httpx.Response(403, text="user is missing the Job/Build permission")

    jc = client(handler)
    with pytest.raises(Exception, match="403"):
        await jc.request("POST", "/job/AI/job/build/build", action="test")
    assert len(attempts) == 1, "permission errors must not trigger a crumb retry"
    await jc.close()


@pytest.mark.asyncio
async def test_403_that_only_mentions_crumb_configuration_is_not_replayed() -> None:
    """The noun alone is not proof that Jenkins rejected the request's crumb."""
    issuer_calls = 0
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal issuer_calls, attempts
        if request.url.path == "/crumbIssuer/api/json":
            issuer_calls += 1
            return httpx.Response(
                200, json={"crumbRequestField": "Jenkins-Crumb", "crumb": "c"}
            )
        attempts += 1
        return httpx.Response(403, text="Not permitted to configure the crumb issuer")

    jc = client(handler, JENKINS_MAX_RETRIES=0)
    with pytest.raises(JenkinsError) as caught:
        await jc.build("demo")
    assert issuer_calls == 1
    assert attempts == 1
    assert "rejected the CSRF crumb" not in str(caught.value)
    await jc.close()


# --- TLS trust settings ---------------------------------------------------


def test_public_certificate_needs_no_ca_bundle() -> None:
    """The common case: Let's Encrypt or Tailscale, verified by system roots."""
    assert settings(JENKINS_VERIFY_TLS=True).verify is True


def test_ca_bundle_pins_trust_to_that_issuer() -> None:
    assert (
        settings(JENKINS_VERIFY_TLS=True, JENKINS_CA_BUNDLE="/certs/ca.crt").verify
        == "/certs/ca.crt"
    )


def test_verification_can_be_disabled_without_a_bundle() -> None:
    assert settings(JENKINS_VERIFY_TLS=False).verify is False


def test_ca_bundle_with_verification_disabled_is_rejected() -> None:
    """This combination silently re-enabled verification."""
    with pytest.raises(ValueError, match="JENKINS_VERIFY_TLS is false"):
        settings(JENKINS_VERIFY_TLS=False, JENKINS_CA_BUNDLE="/certs/ca.crt")


# --- controllers with CSRF protection disabled ---------------------------


@pytest.mark.asyncio
async def test_missing_crumb_issuer_is_probed_once_not_per_write() -> None:
    """A controller with CSRF off must not cost a 404 on every write."""
    probes: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/crumbIssuer/api/json":
            probes.append(request.url.path)
            return httpx.Response(404, text="not found")
        return httpx.Response(200, json={"ok": True})

    jc = client(handler)
    for _ in range(4):
        await jc.request("POST", "/job/AI/job/x/build", action="test")
    assert len(probes) == 1, f"crumb issuer probed {len(probes)} times"
    await jc.close()


@pytest.mark.asyncio
async def test_writes_still_succeed_without_a_crumb_issuer() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/crumbIssuer/api/json":
            return httpx.Response(404)
        assert "Jenkins-Crumb" not in request.headers
        return httpx.Response(200, json={"ok": True})

    jc = client(handler)
    response = await jc.request("POST", "/job/AI/job/x/build", action="test")
    assert response.status_code == 200
    await jc.close()


# --- parameterised builds -------------------------------------------------


@pytest.mark.asyncio
async def test_empty_parameters_still_use_buildWithParameters() -> None:
    """A parameterised job rejects /build, so an empty dict must not fall back."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/crumbIssuer/api/json":
            return httpx.Response(
                200, json={"crumbRequestField": "Jenkins-Crumb", "crumb": "c"}
            )
        calls.append(request.url.path)
        return httpx.Response(201, headers={"Location": "q"})

    jc = client(handler)
    await jc.build("AI/x", {})
    assert calls[-1].endswith("/buildWithParameters")

    await jc.build("AI/x", {"BRANCH": "main"})
    assert calls[-1].endswith("/buildWithParameters")

    await jc.build("AI/x")
    assert calls[-1].endswith("/build")
    await jc.close()


def test_context_path_is_preserved_in_urls() -> None:
    """Jenkins behind a prefix such as https://ci.corp/jenkins must still work."""
    c = httpx.Client(base_url="https://ci.corp/jenkins", trust_env=False)
    assert (
        str(c.build_request("GET", "/job/AI/job/x/api/json").url)
        == "https://ci.corp/jenkins/job/AI/job/x/api/json"
    )


# --- admin_request hardening ----------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "https://evil.test/x",
        "HtTpS://evil.test/x",  # scheme case is not a prefix match
        " https://evil.test/x",  # leading whitespace defeats startswith
        "//evil.test/x",  # protocol-relative
        "relative/path",  # not absolute
        "/a/../../etc/passwd",  # traversal
    ],
)
async def test_admin_request_rejects_non_jenkins_targets(path: str) -> None:
    """The path is caller-controlled and reaches an HTTP request."""
    jc = client(lambda request: httpx.Response(200, text="ok"), allow_admin=True)
    with pytest.raises(ValueError):
        await jc.admin_request("GET", path)
    await jc.close()


@pytest.mark.asyncio
async def test_admin_request_withholds_session_and_csrf_headers() -> None:
    """Jenkins issues the session to this server, not to the MCP client.

    Returning Set-Cookie or the crumb hands a caller a usable session.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/crumbIssuer/api/json":
            return httpx.Response(
                200, json={"crumbRequestField": "Jenkins-Crumb", "crumb": "c"}
            )
        return httpx.Response(
            200,
            text="ok",
            headers={
                "Set-Cookie": "JSESSIONID=secret; Path=/",
                "X-Jenkins-Crumb": "crumb-value",
                "X-Jenkins": "2.555.1",
            },
        )

    jc = client(handler, allow_admin=True)
    result = await jc.admin_request("GET", "/api/json")
    names = {name.lower() for name in result["headers"]}
    assert "set-cookie" not in names
    assert "x-jenkins-crumb" not in names
    # Harmless headers still pass through, so the tool stays useful.
    assert "x-jenkins" in names
    await jc.close()


@pytest.mark.asyncio
async def test_admin_patch_receives_a_csrf_crumb() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/crumbIssuer/api/json":
            return httpx.Response(
                200,
                json={"crumbRequestField": "Jenkins-Crumb", "crumb": "c"},
            )
        assert request.method == "PATCH"
        assert request.headers["Jenkins-Crumb"] == "c"
        return httpx.Response(200, text="ok")

    jc = client(handler, allow_admin=True)
    result = await jc.admin_request("PATCH", "/plugin/endpoint", "{}")
    assert result["status"] == 200
    await jc.close()


# --- retry safety ----------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [502, 503, 504])
async def test_write_is_not_replayed_on_a_gateway_error(status: int) -> None:
    """A 502 does not say whether Jenkins acted on the request.

    Replaying a build trigger queues a second build, and the tool then reports
    success, so the duplicate is invisible to the caller.
    """
    attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("crumbIssuer/api/json"):
            return httpx.Response(200, json={"crumbRequestField": "C", "crumb": "c"})
        attempts.append(request.url.path)
        return httpx.Response(status, text="gateway")

    jc = client(handler, JENKINS_MAX_RETRIES=3)
    with pytest.raises(JenkinsError):
        await jc.build("demo")
    assert len(attempts) == 1, f"build was replayed {len(attempts)} times"
    await jc.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_type",
    [
        httpx.ReadTimeout,
        httpx.WriteTimeout,
        httpx.ReadError,
        httpx.WriteError,
        httpx.RemoteProtocolError,
    ],
)
async def test_write_is_not_replayed_after_an_ambiguous_transport_error(
    error_type: type[httpx.TransportError],
) -> None:
    """These failures can arrive after Jenkins accepted the request."""
    attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("crumbIssuer/api/json"):
            return httpx.Response(200, json={"crumbRequestField": "C", "crumb": "c"})
        attempts.append(request.url.path)
        raise error_type("ambiguous failure", request=request)

    jc = client(handler, JENKINS_MAX_RETRIES=3)
    with pytest.raises(JenkinsError):
        await jc.build("demo")
    assert len(attempts) == 1
    await jc.close()


@pytest.mark.asyncio
async def test_write_is_not_replayed_on_429() -> None:
    """429 is a rate-limit signal, not proof that no side effect occurred."""
    attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("crumbIssuer/api/json"):
            return httpx.Response(200, json={"crumbRequestField": "C", "crumb": "c"})
        attempts.append(request.url.path)
        return httpx.Response(429)

    jc = client(handler, JENKINS_MAX_RETRIES=3)
    with pytest.raises(JenkinsError, match="429"):
        await jc.build("demo")
    assert len(attempts) == 1
    await jc.close()


@pytest.mark.asyncio
async def test_reads_still_retry_on_transient_failures() -> None:
    """The fix must not remove retries where replaying is harmless."""
    attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request.url.path)
        if len(attempts) < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"jobs": []})

    jc = client(handler, JENKINS_MAX_RETRIES=3)
    await jc.list_jobs()
    assert len(attempts) == 3
    await jc.close()


@pytest.mark.asyncio
async def test_safe_retry_honours_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return httpx.Response(200, json={"jobs": []})

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("jenkins_mcp_server.client.asyncio.sleep", record_sleep)
    jc = client(handler, JENKINS_MAX_RETRIES=1)
    await jc.list_jobs()
    assert delays == [7]
    await jc.close()


@pytest.mark.asyncio
async def test_read_retries_after_read_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadTimeout("late response", request=request)
        return httpx.Response(200, json={"jobs": []})

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("jenkins_mcp_server.client.asyncio.sleep", no_sleep)
    jc = client(handler, JENKINS_MAX_RETRIES=1)
    await jc.list_jobs()
    assert attempts == 2
    await jc.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_type",
    [httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout],
)
async def test_write_retries_only_before_sending(
    error_type: type[httpx.TransportError],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        if request.url.path.endswith("crumbIssuer/api/json"):
            return httpx.Response(200, json={"crumbRequestField": "C", "crumb": "c"})
        attempts += 1
        if attempts == 1:
            raise error_type("not sent", request=request)
        return httpx.Response(201, headers={"Location": "/queue/1"})

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("jenkins_mcp_server.client.asyncio.sleep", no_sleep)
    jc = client(handler, JENKINS_MAX_RETRIES=1)
    assert (await jc.build("demo"))["queued"] is True
    assert attempts == 2
    await jc.close()


@pytest.mark.asyncio
async def test_concurrent_writes_share_one_crumb_request() -> None:
    """Eight parallel writes should not each fetch their own crumb."""
    crumb_requests: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("crumbIssuer/api/json"):
            crumb_requests.append(request.url.path)
            await asyncio.sleep(0.02)
            return httpx.Response(200, json={"crumbRequestField": "C", "crumb": "c"})
        return httpx.Response(201, headers={"Location": "/queue/1"})

    jc = client(handler, JENKINS_MAX_RETRIES=0)
    await asyncio.gather(*(jc.build(f"job{i}") for i in range(8)))
    assert len(crumb_requests) == 1, f"{len(crumb_requests)} crumb requests"
    await jc.close()


@pytest.mark.asyncio
async def test_crumb_issuer_errors_surface_as_jenkins_errors() -> None:
    """Tools document JenkinsError; httpx types should not leak through."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("crumbIssuer/api/json"):
            return httpx.Response(401, text="unauthorized")
        return httpx.Response(201)

    jc = client(handler, JENKINS_MAX_RETRIES=0)
    with pytest.raises(JenkinsError, match="crumb issuer returned 401"):
        await jc.build("demo")
    await jc.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not-json"),
        httpx.Response(200, json={"crumbRequestField": "C"}),
        httpx.Response(200, json={"crumbRequestField": "C", "crumb": 7}),
    ],
)
async def test_malformed_crumb_response_is_wrapped(response: httpx.Response) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return response

    jc = client(handler, JENKINS_MAX_RETRIES=0)
    with pytest.raises(JenkinsError, match="malformed JSON"):
        await jc.build("demo")
    await jc.close()


@pytest.mark.asyncio
async def test_crumb_response_is_bounded() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"{" + b"x" * 2048)

    jc = client(handler, JENKINS_MAX_RETRIES=0, MCP_MAX_RESPONSE_BYTES=1024)
    with pytest.raises(JenkinsError, match=r"crumb issuer response exceeded.*1024 bytes"):
        await jc.build("demo")
    await jc.close()


@pytest.mark.asyncio
async def test_failed_crumb_fetch_releases_single_flight_lock() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    jc = client(handler, JENKINS_MAX_RETRIES=0)
    results = await asyncio.wait_for(
        asyncio.gather(jc.build("one"), jc.build("two"), return_exceptions=True),
        timeout=1,
    )
    assert all(isinstance(result, JenkinsError) for result in results)
    assert calls == 2
    await jc.close()


@pytest.mark.asyncio
async def test_audit_write_failure_does_not_fail_a_completed_action(tmp_path, caplog) -> None:
    """emit() runs after Jenkins acted, so raising misreports a success.

    The caller would see a failure for a build that was queued, and would
    reasonably trigger it again.
    """
    unwritable = tmp_path / "file" / "nested" / "audit.log"
    unwritable.parent.parent.write_text("not a directory", encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("crumbIssuer/api/json"):
            return httpx.Response(200, json={"crumbRequestField": "C", "crumb": "c"})
        return httpx.Response(201, headers={"Location": "/queue/1"})

    jc = JenkinsClient(
        settings(),
        policy(),
        AuditLogger(unwritable),
        transport=httpx.MockTransport(handler),
    )
    with caplog.at_level(logging.ERROR):
        result = await jc.build("demo")
    assert result["queued"] is True
    assert "not writable" in caplog.text
    await jc.close()


def test_audit_reports_an_unwritable_path_once(tmp_path, caplog) -> None:
    """A per-action error log would drown the records it is meant to protect."""
    unwritable = tmp_path / "file" / "nested" / "audit.log"
    unwritable.parent.parent.write_text("not a directory", encoding="utf-8")

    audit = AuditLogger(unwritable)
    with caplog.at_level(logging.ERROR):
        for _ in range(5):
            audit.emit("build.trigger", "success", status=201)
    assert caplog.text.count("not writable") == 1


@pytest.mark.asyncio
async def test_a_transient_crumb_404_does_not_disable_csrf_forever() -> None:
    """A 404 during a restart made the wrong conclusion permanent.

    Nothing re-probed the issuer and readiness does not test it, so every write
    failed until the process restarted. Jenkins asking for a crumb disproves the
    conclusion, so the next write must recover on its own.
    """
    state = {"issuer_up": False, "probes": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/crumbIssuer/api/json":
            state["probes"] += 1
            if not state["issuer_up"]:
                return httpx.Response(404, text="not found")
            return httpx.Response(
                200, json={"crumbRequestField": "Jenkins-Crumb", "crumb": "c"}
            )
        if "Jenkins-Crumb" not in request.headers:
            return httpx.Response(403, text="No valid crumb was included")
        return httpx.Response(201, headers={"Location": "/queue/1"})

    jc = client(handler, JENKINS_MAX_RETRIES=0)
    with pytest.raises(JenkinsError):
        await jc.build("demo")

    state["issuer_up"] = True
    assert (await jc.build("demo"))["queued"] is True
    await jc.close()


@pytest.mark.asyncio
async def test_concurrent_writes_share_one_transient_crumb_recovery() -> None:
    """Every write waiting on the disproved negative cache must recover."""
    state = {"issuer_up": False, "probes": 0, "waiting": 0}
    both_waiting = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/crumbIssuer/api/json":
            state["probes"] += 1
            if not state["issuer_up"]:
                return httpx.Response(404)
            return httpx.Response(200, json={"crumbRequestField": "Jenkins-Crumb", "crumb": "c"})
        if "Jenkins-Crumb" not in request.headers:
            if state["issuer_up"]:
                state["waiting"] += 1
                if state["waiting"] == 2:
                    both_waiting.set()
                await asyncio.wait_for(both_waiting.wait(), timeout=1)
            return httpx.Response(403, text="No valid crumb was included")
        return httpx.Response(201, headers={"Location": "/queue/1"})

    jc = client(handler, JENKINS_MAX_RETRIES=0)
    with pytest.raises(JenkinsError):
        await jc.build("initial")

    state["issuer_up"] = True
    state["probes"] = 0
    results = await asyncio.gather(jc.build("one"), jc.build("two"))
    assert all(result["queued"] is True for result in results)
    assert state["probes"] == 1, "concurrent recovery must share one issuer probe"
    await jc.close()


@pytest.mark.asyncio
async def test_crumb_issuer_transport_failure_is_reported() -> None:
    """The preflight can fail before request() sends the write itself."""
    contact = JenkinsContact()

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    jc = client(handler, contact=contact, JENKINS_MAX_RETRIES=0)
    with pytest.raises(JenkinsError, match="Could not reach the Jenkins crumb issuer"):
        await jc.build("demo")
    assert contact.snapshot() == {
        "last_contact_age_seconds": None,
        "last_transport_error": "ConnectError",
    }
    await jc.close()


@pytest.mark.asyncio
async def test_default_transport_diagnostics_are_isolated_per_client() -> None:
    """Library clients must not leak failures through process-global state."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request)

    first = client(handler)
    second = client(handler)
    first.contact.record_failure(httpx.ConnectError("refused"))

    assert first.contact.snapshot()["last_transport_error"] == "ConnectError"
    assert second.contact.snapshot() == {
        "last_contact_age_seconds": None,
        "last_transport_error": None,
    }
    await first.close()
    await second.close()


@pytest.mark.asyncio
async def test_csrf_disabled_controllers_are_still_probed_only_once() -> None:
    """The negative cache is why the flag exists; recovery must not cost it."""
    probes = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/crumbIssuer/api/json":
            probes["count"] += 1
            return httpx.Response(404)
        return httpx.Response(201, headers={"Location": "/queue/1"})

    jc = client(handler, JENKINS_MAX_RETRIES=0)
    for _ in range(5):
        await jc.build("demo")
    assert probes["count"] == 1, f"re-probed {probes['count']} times"
    await jc.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [1, 5, 20])
async def test_in_flight_requests_to_jenkins_are_bounded(limit: int) -> None:
    """An agent can fan out tool calls; Jenkins serves everything from one pool.

    httpx defaults to 100 connections, which is a general-purpose default and a
    poor one for a shared controller. The bound is on requests rather than
    connections so it holds whatever transport is in use.
    """
    inflight = {"now": 0, "peak": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        inflight["now"] += 1
        inflight["peak"] = max(inflight["peak"], inflight["now"])
        await asyncio.sleep(0.01)
        inflight["now"] -= 1
        return httpx.Response(200, json={"jobs": []})

    jc = client(handler, JENKINS_MAX_RETRIES=0, JENKINS_MAX_CONCURRENCY=limit)
    await asyncio.gather(*(jc.list_jobs() for _ in range(60)))
    assert inflight["peak"] <= limit, f"peak {inflight['peak']} exceeded limit {limit}"
    await jc.close()


@pytest.mark.asyncio
async def test_waiting_for_a_concurrency_slot_is_bounded_by_timeout() -> None:
    """Queue time is outside HTTPX, so the application must bound it itself."""
    entered = asyncio.Event()
    release = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        entered.set()
        await release.wait()
        return httpx.Response(200, json={"jobs": []})

    jc = client(
        handler,
        JENKINS_MAX_RETRIES=0,
        JENKINS_MAX_CONCURRENCY=1,
        JENKINS_TIMEOUT_SECONDS=0.02,
    )
    first = asyncio.create_task(jc.list_jobs())
    await asyncio.wait_for(entered.wait(), timeout=1)
    with pytest.raises(JenkinsError, match="Timed out waiting for a Jenkins concurrency slot"):
        await jc.list_jobs()
    release.set()
    await first
    await jc.close()


@pytest.mark.asyncio
async def test_crumb_preflight_does_not_deadlock_against_the_bound() -> None:
    """A write needs a crumb first, and both go through the same semaphore.

    Concurrency of one is the worst case: if the permit were held across the
    preflight, no write could ever complete.
    """

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/crumbIssuer/api/json":
            await asyncio.sleep(0.01)
            return httpx.Response(200, json={"crumbRequestField": "Jenkins-Crumb", "crumb": "c"})
        return httpx.Response(201, headers={"Location": "/queue/1"})

    jc = client(handler, JENKINS_MAX_RETRIES=0, JENKINS_MAX_CONCURRENCY=1)
    results = await asyncio.wait_for(
        asyncio.gather(*(jc.build(f"job{i}") for i in range(8))), timeout=10
    )
    assert len(results) == 8
    await jc.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("node_name", ["", "   ", "../evil", "a/../b"])
async def test_node_names_are_validated_like_job_names(node_name: str) -> None:
    """An empty node name collapsed /computer/<name>/ to the collection.

    set_node_offline("") is destructive: it read every node, found no
    temporarilyOffline field, posted to /computer//toggleOffline and returned
    {"offline": true}. The caller was told a node had been taken offline when
    none had.
    """
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("invalid node name must be rejected before Jenkins")

    jc = client(handler, JENKINS_MAX_RETRIES=0)
    with pytest.raises(ValueError, match="node_name"):
        await jc.node_info(node_name)
    # offline=False is a write rather than a destructive action, so it reaches
    # the name handling under the default policy.
    with pytest.raises(ValueError, match="node_name"):
        await jc.toggle_node(node_name, False)
    await jc.close()


@pytest.mark.asyncio
async def test_valid_node_names_still_work() -> None:
    """Validation must not reject the names Jenkins actually uses."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        # raw_path keeps the encoding; url.path decodes it.
        seen.append(request.url.raw_path.decode())
        if request.url.path.endswith("crumbIssuer/api/json"):
            return httpx.Response(
                200, json={"crumbRequestField": "C", "crumb": "c"}
            )
        return httpx.Response(200, json={"temporarilyOffline": True})

    jc = client(handler, JENKINS_MAX_RETRIES=0)
    # Spaces are legal in Jenkins node names and must survive encoding.
    await jc.toggle_node("build agent 1", False)
    assert "/computer/build%20agent%201/api/json?depth=2" in seen
    assert any(
        path.startswith("/computer/build%20agent%201/toggleOffline?") for path in seen
    )
    await jc.close()


# --- admin_request inherits the limits it used to walk around ---------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/job/Secret/job/x/doDelete",
        "/%6aob/Secret/job/x/doDelete",
        "/view/All/job/Secret/job/x/doDelete",
        "/view/All/%6aob/Secret/job/x/doDelete",
    ],
)
async def test_admin_request_still_honours_the_job_allowlist(path: str) -> None:
    """The escape hatch inherited none of the limits of the tools it replaces.

    With MCP_ALLOWED_JOBS=AI/*, POST /job/Secret/job/x/doDelete deleted a job
    outside the allowlist, so enabling this tool voided the boundary entirely.
    """
    jc = client_with_policy(
        lambda request: httpx.Response(200, text="ok"),
        job_patterns=["AI/*"],
    )
    with pytest.raises(PolicyError, match="not allowed"):
        await jc.admin_request("POST", path)
    await jc.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/job/AI/job/nightly/doDelete",
        "/job/AI/job/nightly/api/json",
        "/view/All/job/AI/job/nightly/api/json",
    ],
)
async def test_admin_request_permits_jobs_inside_the_allowlist(path: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("crumbIssuer/api/json"):
            return httpx.Response(200, json={"crumbRequestField": "C", "crumb": "c"})
        return httpx.Response(200, text="ok")

    jc = client_with_policy(handler, job_patterns=["AI/*"])
    assert (await jc.admin_request("POST", path))["status"] == 200
    await jc.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/script",
        "/scriptText",
        "/SCRIPTTEXT",
        "/%73criptText",
        "/script%54ext",
    ],
)
async def test_admin_request_refuses_the_script_console_by_default(path: str) -> None:
    """Minibridge's sensitive-path guardrail refuses it, but that layer is optional.

    The layer documented as always enforced must not be the weaker of the two:
    the console runs arbitrary code on the controller.
    """
    jc = client(lambda request: httpx.Response(200, text="ok"), JENKINS_MAX_RETRIES=0)
    with pytest.raises(PolicyError, match="script console"):
        await jc.admin_request("POST", path)
    await jc.close()


@pytest.mark.asyncio
async def test_script_console_can_be_enabled_explicitly() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("crumbIssuer/api/json"):
            return httpx.Response(200, json={"crumbRequestField": "C", "crumb": "c"})
        return httpx.Response(200, text="ok")

    jc = client_with_policy(handler, allow_script_console=True)
    assert (await jc.admin_request("POST", "/scriptText"))["status"] == 200
    await jc.close()


@pytest.mark.asyncio
async def test_admin_request_still_reaches_non_job_endpoints() -> None:
    """The tool must stay an escape hatch for everything else."""
    jc = client_with_policy(
        lambda request: httpx.Response(200, text="ok"), job_patterns=["AI/*"]
    )
    assert (await jc.admin_request("GET", "/api/json"))["status"] == 200
    await jc.close()


# --- refusals are recorded -------------------------------------------------


def _audit_records(path) -> list[dict]:
    import json

    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


@pytest.mark.asyncio
async def test_policy_refusals_are_audited(tmp_path) -> None:
    """The log recorded what an agent was allowed to do and nothing it tried.

    For a server whose purpose is bounding an agent, the refusals are the
    interesting events: a job probed outside the allowlist, a blocked delete, a
    reach for the script console. None of it was visible before.
    """
    log = tmp_path / "audit.jsonl"
    original_policy = policy(job_patterns=["AI/*"])
    jc = JenkinsClient(
        settings(JENKINS_MAX_RETRIES=0),
        original_policy,
        AuditLogger(log),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"jobs": []})
        ),
    )
    with pytest.raises(PolicyError):
        await jc.get_job("Secret/x")
    await jc.close()

    assert jc.policy is original_policy
    denials = [r for r in _audit_records(log) if r["outcome"] == "denied"]
    assert denials, "a refused call left no audit record"
    assert denials[0]["check"] == "check_job"
    assert denials[0]["target"] == "Secret/x"
    assert "not allowed" in denials[0]["reason"]


@pytest.mark.asyncio
async def test_script_console_refusals_are_audited(tmp_path) -> None:
    """That check raises outside the Policy object, so it needs its own hook."""
    log = tmp_path / "audit.jsonl"
    jc = JenkinsClient(
        settings(JENKINS_MAX_RETRIES=0),
        policy(),
        AuditLogger(log),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text="ok")
        ),
    )
    for path in ("/scriptText", "/%73criptText"):
        with pytest.raises(PolicyError):
            await jc.admin_request("POST", path)
    await jc.close()

    checks = [r["check"] for r in _audit_records(log) if r["outcome"] == "denied"]
    assert checks.count("script_console") == 2, (
        "an encoded attempt should be recorded like a plain one"
    )


@pytest.mark.asyncio
async def test_allowed_calls_are_still_audited_as_before(tmp_path) -> None:
    """Recording refusals must not disturb the existing success records."""
    log = tmp_path / "audit.jsonl"
    jc = JenkinsClient(
        settings(JENKINS_MAX_RETRIES=0),
        policy(job_patterns=["AI/*"]),
        AuditLogger(log),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"name": "nightly"})
        ),
    )
    await jc.get_job("AI/nightly")
    await jc.close()

    outcomes = [r["outcome"] for r in _audit_records(log)]
    assert "success" in outcomes
    assert "denied" not in outcomes


@pytest.mark.asyncio
async def test_destructive_denial_records_action_and_job(tmp_path) -> None:
    """A generic positional proxy used to lose the job behind the action."""
    log = tmp_path / "audit.jsonl"
    jc = JenkinsClient(
        settings(JENKINS_MAX_RETRIES=0),
        policy(
            job_patterns=["AI/*"],
            allow_destructive=True,
            allow_job_delete=False,
        ),
        AuditLogger(log),
        transport=httpx.MockTransport(
            lambda request: pytest.fail(f"denied call reached Jenkins: {request.url}")
        ),
    )

    with pytest.raises(PolicyError, match="job.delete"):
        await jc.delete_job("AI/nightly")
    await jc.close()

    [denial] = [r for r in _audit_records(log) if r["outcome"] == "denied"]
    assert denial["check"] == "require_destructive"
    assert denial["policy_action"] == "job.delete"
    assert denial["target"] == "AI/nightly"
    assert denial["job"] == "AI/nightly"


@pytest.mark.asyncio
async def test_folder_scope_refusal_is_audited(tmp_path) -> None:
    """list_jobs used a boolean policy query, so a proxy saw no exception."""
    log = tmp_path / "audit.jsonl"
    jc = JenkinsClient(
        settings(JENKINS_MAX_RETRIES=0),
        policy(job_patterns=["AI/*"]),
        AuditLogger(log),
        transport=httpx.MockTransport(
            lambda request: pytest.fail(f"denied call reached Jenkins: {request.url}")
        ),
    )

    with pytest.raises(PolicyError, match="not allowed"):
        await jc.list_jobs("Production")
    await jc.close()

    [denial] = [r for r in _audit_records(log) if r["outcome"] == "denied"]
    assert denial["check"] == "allows_job_or_descendant"
    assert denial["target"] == "Production"


@pytest.mark.asyncio
async def test_denial_audit_completes_before_error_is_returned() -> None:
    """Normal shutdown must not race a detached denial-write task."""
    started = asyncio.Event()
    release = asyncio.Event()

    class DelayedAudit(AuditLogger):
        async def emit_async(self, action, outcome, **fields) -> None:
            if action == "policy.denied":
                started.set()
                await release.wait()
            await super().emit_async(action, outcome, **fields)

    jc = JenkinsClient(
        settings(JENKINS_MAX_RETRIES=0),
        policy(job_patterns=["AI/*"]),
        DelayedAudit(),
        transport=httpx.MockTransport(
            lambda request: pytest.fail(f"denied call reached Jenkins: {request.url}")
        ),
    )

    call = asyncio.create_task(jc.get_job("Secret/x"))
    await asyncio.wait_for(started.wait(), timeout=1)
    assert not call.done(), "the policy error escaped before its audit record"
    release.set()
    with pytest.raises(PolicyError):
        await call
    await jc.close()


@pytest.mark.asyncio
async def test_admin_category_denial_does_not_audit_query_secrets(tmp_path) -> None:
    log = tmp_path / "audit.jsonl"
    jc = JenkinsClient(
        settings(JENKINS_MAX_RETRIES=0),
        policy(allow_admin_request=False),
        AuditLogger(log),
        transport=httpx.MockTransport(
            lambda request: pytest.fail(f"denied call reached Jenkins: {request.url}")
        ),
    )

    with pytest.raises(PolicyError, match="admin"):
        await jc.admin_request("GET", "/api/json?access_token=do-not-log")
    await jc.close()

    text = log.read_text()
    assert "do-not-log" not in text
    [denial] = [r for r in _audit_records(log) if r["outcome"] == "denied"]
    assert denial["check"] == "require_write"
    assert denial["category"] == "admin"
    assert denial["target"] == "admin"


@pytest.mark.asyncio
async def test_admin_request_query_is_sent_but_not_audited(tmp_path) -> None:
    """Redaction must protect records without changing the Jenkins request."""
    marker = "ADMIN-QUERY-SECRET"
    log = tmp_path / "audit.jsonl"

    def handler(request: httpx.Request) -> httpx.Response:
        assert marker in request.url.query.decode()
        return httpx.Response(200, json={"ok": True})

    jc = JenkinsClient(
        settings(JENKINS_MAX_RETRIES=0),
        policy(),
        AuditLogger(log),
        transport=httpx.MockTransport(handler),
    )
    result = await jc.admin_request("GET", f"/api/json?%74oken={marker}&tree=jobs")
    await jc.close()

    assert result["status"] == 200
    text = log.read_text()
    assert marker not in text
    [record] = [r for r in _audit_records(log) if r["action"] == "admin.request"]
    assert record["path"] == "/api/json?[redacted]"


@pytest.mark.asyncio
async def test_transport_error_does_not_return_query_secret() -> None:
    marker = "TRANSPORT-QUERY-SECRET"

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"failed to reach {request.url}", request=request)

    jc = client(handler, JENKINS_MAX_RETRIES=0)
    with pytest.raises(JenkinsError) as caught:
        await jc.admin_request("GET", f"/api/json?token={marker}")
    await jc.close()

    assert marker not in str(caught.value)
    assert "?[redacted]" in str(caught.value)


# --- request bodies are bounded --------------------------------------------


@pytest.mark.asyncio
async def test_oversized_request_bodies_are_refused() -> None:
    """Responses were capped; the request direction was not.

    A tool call carries whatever config.xml the model produced, and an
    oversized POST is buffered here and then pushed at a controller shared with
    every other Jenkins client.
    """
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.url.path)
        if request.url.path.endswith("crumbIssuer/api/json"):
            return httpx.Response(200, json={"crumbRequestField": "C", "crumb": "c"})
        return httpx.Response(200, text="ok")

    jc = client(handler, JENKINS_MAX_RETRIES=0, MCP_MAX_REQUEST_BYTES=4096)
    with pytest.raises(ValueError, match="MCP_MAX_REQUEST_BYTES"):
        await jc.create_job("job", "<x>" + "A" * 8192 + "</x>")
    assert not sent, "an oversized body triggered a Jenkins or crumb request"
    await jc.close()


@pytest.mark.asyncio
async def test_form_parameters_count_towards_the_request_cap() -> None:
    """Build parameters are a mapping, not a string, and were not measured."""
    jc = client(
        lambda request: httpx.Response(201, headers={"Location": "/queue/1"}),
        JENKINS_MAX_RETRIES=0,
        MCP_MAX_REQUEST_BYTES=4096,
    )
    with pytest.raises(ValueError, match="MCP_MAX_REQUEST_BYTES"):
        await jc.build("job", {"BIG": "A" * 8192})
    await jc.close()


@pytest.mark.asyncio
async def test_form_limit_measures_the_encoded_wire_body() -> None:
    """Reserved form characters expand to three bytes and must not bypass."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(201, headers={"Location": "/queue/1"})

    jc = client(handler, JENKINS_MAX_RETRIES=0, MCP_MAX_REQUEST_BYTES=4096)
    # The input is about 2 KB, but URL encoding makes the body 6004 bytes.
    with pytest.raises(ValueError, match="6004 bytes"):
        await jc.build("job", {"BIG": "&" * 2000})
    assert not requests
    await jc.close()


@pytest.mark.asyncio
async def test_preencoded_form_body_is_not_encoded_twice() -> None:
    """Measure once and reuse those bytes without changing Jenkins semantics."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("crumbIssuer/api/json"):
            return httpx.Response(404)
        assert request.content == b"A=x+y%26z"
        assert request.headers["Content-Type"] == "application/x-www-form-urlencoded"
        return httpx.Response(201, headers={"Location": "/queue/1"})

    jc = client(handler, JENKINS_MAX_RETRIES=0, MCP_MAX_REQUEST_BYTES=4096)
    assert (await jc.build("job", {"A": "x y&z"}))["queued"] is True
    await jc.close()


@pytest.mark.asyncio
async def test_oversized_request_is_audited_without_recording_the_body(tmp_path) -> None:
    log = tmp_path / "audit.jsonl"
    marker = "OVERSIZED-BODY-MARKER"
    jc = JenkinsClient(
        settings(JENKINS_MAX_RETRIES=0, MCP_MAX_REQUEST_BYTES=4096),
        policy(),
        AuditLogger(log),
        transport=httpx.MockTransport(
            lambda request: pytest.fail(f"oversized call reached Jenkins: {request.url}")
        ),
    )

    with pytest.raises(ValueError, match="MCP_MAX_REQUEST_BYTES"):
        await jc.admin_request("POST", "/api/json", marker * 1024)
    await jc.close()

    text = log.read_text()
    assert marker not in text
    [record] = [r for r in _audit_records(log) if r["action"] == "admin.request"]
    assert record["outcome"] == "failure"
    assert record["status"] == "request_too_large"
    assert record["request_bytes"] > record["request_limit_bytes"] == 4096


@pytest.mark.asyncio
async def test_normal_bodies_are_unaffected() -> None:
    """A real job definition is far below the default limit."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("crumbIssuer/api/json"):
            return httpx.Response(200, json={"crumbRequestField": "C", "crumb": "c"})
        return httpx.Response(200, text="ok")

    jc = client(handler, JENKINS_MAX_RETRIES=0)
    await jc.create_job("job", "<project><description>real</description></project>")
    await jc.close()
