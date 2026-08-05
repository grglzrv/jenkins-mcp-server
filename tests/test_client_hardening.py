"""Regression tests for path-traversal, log pagination, and CSRF crumb handling."""

import httpx
import pytest

from jenkins_mcp_server.audit import AuditLogger
from jenkins_mcp_server.client import JenkinsClient, _job_path
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
