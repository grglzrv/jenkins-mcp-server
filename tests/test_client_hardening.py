"""Regression tests for path-traversal, log pagination, and CSRF crumb handling."""

import asyncio
import logging

import httpx
import pytest

from jenkins_mcp_server.audit import AuditLogger
from jenkins_mcp_server.client import JenkinsClient, JenkinsError, _job_path
from jenkins_mcp_server.config import Settings
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


def client(handler, **setting_overrides: object) -> JenkinsClient:
    return JenkinsClient(
        settings(**setting_overrides),
        policy(),
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
    ["//evil.example/steal", "/safe/../../etc", "/./relative"],
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


# --- TLS trust settings ---------------------------------------------------


def test_public_certificate_needs_no_ca_bundle() -> None:
    """The common case: Let's Encrypt or Tailscale, verified by system roots."""
    assert settings(JENKINS_VERIFY_TLS=True).verify is True


def test_ca_bundle_pins_trust_to_that_issuer() -> None:
    assert settings(
        JENKINS_VERIFY_TLS=True, JENKINS_CA_BUNDLE="/certs/ca.crt"
    ).verify == "/certs/ca.crt"


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
        "HtTpS://evil.test/x",       # scheme case is not a prefix match
        " https://evil.test/x",      # leading whitespace defeats startswith
        "//evil.test/x",             # protocol-relative
        "relative/path",             # not absolute
        "/a/../../etc/passwd",       # traversal
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
async def test_audit_write_failure_does_not_fail_a_completed_action(
    tmp_path, caplog
) -> None:
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
