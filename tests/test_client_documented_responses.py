"""Documented response projections for job, build, and console reads."""

from collections.abc import Callable

import httpx
import pytest

from jenkins_mcp_server.audit import AuditLogger
from jenkins_mcp_server.client import JenkinsClient, JenkinsError
from jenkins_mcp_server.config import Settings
from jenkins_mcp_server.security import Policy


def make_client(
    handler: Callable[[httpx.Request], httpx.Response],
    **settings_overrides: object,
) -> JenkinsClient:
    values: dict[str, object] = {
        "JENKINS_URL": "https://jenkins.test",
        "JENKINS_USERNAME": "user",
        "JENKINS_TOKEN": "token",
        "JENKINS_MAX_RETRIES": 0,
    }
    values.update(settings_overrides)
    settings = Settings(**values)  # type: ignore[arg-type]
    policy = Policy(
        read_only=False,
        allow_job_write=True,
        allow_build_write=True,
        allow_node_write=True,
        allow_admin_request=True,
        job_patterns=["AI/*"],
    )
    return JenkinsClient(settings, policy, AuditLogger(), httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_list_jobs_projects_top_level_and_entry_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "actions" not in request.url.params["tree"]
        return httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "name": "build",
                        "url": "https://jenkins.test/job/AI/job/build/",
                        "color": "blue",
                        "_class": "org.jenkinsci.plugins.workflow.job.WorkflowJob",
                        "actions": [{"secret": "must not escape"}],
                    }
                ],
                "unexpectedTopLevel": {"secret": "must not escape"},
            },
        )

    client = make_client(handler)
    result = await client.list_jobs("AI")

    assert result == {
        "jobs": [
            {
                "name": "build",
                "fullName": "AI/build",
                "url": "https://jenkins.test/job/AI/job/build/",
                "color": "blue",
                "_class": "org.jenkinsci.plugins.workflow.job.WorkflowJob",
            }
        ]
    }
    assert "secret" not in str(result).casefold()
    await client.close()


@pytest.mark.asyncio
async def test_list_jobs_rejects_malformed_entries() -> None:
    client = make_client(lambda request: httpx.Response(200, json={"jobs": [None]}))

    with pytest.raises(JenkinsError, match="malformed job-list JSON"):
        await client.list_jobs()

    await client.close()


@pytest.mark.asyncio
async def test_get_job_projects_documented_state_and_build_references() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        tree = request.url.params["tree"]
        assert request.url.params["depth"] == "0"
        assert "actions" not in tree
        assert "downstreamProjects" not in tree
        return httpx.Response(
            200,
            json={
                "name": "build",
                "fullName": "AI/build",
                "description": "safe",
                "buildable": True,
                "healthReport": [
                    {
                        "description": "Build stability: No recent builds failed.",
                        "score": 100,
                        "pluginSecret": "must not escape",
                    }
                ],
                "lastBuild": {
                    "number": 7,
                    "url": "https://jenkins.test/job/AI/job/build/7/",
                    "actions": [{"secret": "must not escape"}],
                },
                "downstreamProjects": [
                    {"fullName": "Production/secret", "url": "https://secret/"}
                ],
                "actions": [{"secret": "must not escape"}],
            },
        )

    client = make_client(handler)
    result = await client.get_job("AI/build")

    assert result == {
        "name": "build",
        "fullName": "AI/build",
        "description": "safe",
        "buildable": True,
        "healthReport": [
            {
                "description": "Build stability: No recent builds failed.",
                "score": 100,
            }
        ],
        "lastBuild": {
            "number": 7,
            "url": "https://jenkins.test/job/AI/job/build/7/",
        },
    }
    assert "production" not in str(result).casefold()
    assert "secret" not in str(result).casefold()
    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [[], "job", 7, None])
async def test_get_job_rejects_malformed_top_level(payload: object) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if payload is None:
            return httpx.Response(200, content=b"null")
        return httpx.Response(200, json=payload)

    client = make_client(handler)

    with pytest.raises(JenkinsError, match="malformed job JSON"):
        await client.get_job("AI/build")

    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"healthReport": {}},
        {"healthReport": [None]},
        {"builds": {}},
        {"lastBuild": "not-an-object"},
    ],
)
async def test_get_job_rejects_malformed_nested_state(payload: object) -> None:
    client = make_client(lambda request: httpx.Response(200, json=payload))

    with pytest.raises(JenkinsError, match="malformed job JSON"):
        await client.get_job("AI/build")

    await client.close()


@pytest.mark.asyncio
async def test_build_info_projects_state_and_redacts_sensitive_parameters() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        tree = request.url.params["tree"]
        assert request.url.params["depth"] == "0"
        assert "artifacts" not in tree
        assert "changeSet" not in tree
        return httpx.Response(
            200,
            json={
                "number": 7,
                "building": False,
                "result": "SUCCESS",
                "duration": 1234,
                "actions": [
                    {"causes": [{"userName": "internal-user"}]},
                    {
                        "parameters": [
                            {
                                "name": "ENVIRONMENT",
                                "value": "staging",
                                "_class": "hudson.model.StringParameterValue",
                                "pluginField": "must not escape",
                            },
                            {
                                "name": "PASSWORD",
                                "value": "password-secret",
                                "_class": "hudson.model.PasswordParameterValue",
                            },
                            {
                                "name": "GITHUB_TOKEN",
                                "value": "token-secret",
                                "_class": "hudson.model.StringParameterValue",
                            },
                            {
                                "name": "serviceToken",
                                "value": "compact-token-secret",
                                "_class": "hudson.model.StringParameterValue",
                            },
                            {
                                "name": "COMPLEX",
                                "value": {"secret": "nested-secret"},
                                "_class": "plugin.ComplexParameterValue",
                            },
                        ]
                    },
                ],
                "artifacts": [{"fileName": "secret.txt"}],
                "changeSet": {"items": [{"msg": "internal change"}]},
            },
        )

    client = make_client(handler)
    result = await client.build_info("AI/build", 7)

    assert result == {
        "building": False,
        "number": 7,
        "result": "SUCCESS",
        "duration": 1234,
        "actions": [
            {
                "parameters": [
                    {
                        "name": "ENVIRONMENT",
                        "_class": "hudson.model.StringParameterValue",
                        "value": "staging",
                    },
                    {
                        "name": "PASSWORD",
                        "_class": "hudson.model.PasswordParameterValue",
                        "value": "[redacted]",
                    },
                    {
                        "name": "GITHUB_TOKEN",
                        "_class": "hudson.model.StringParameterValue",
                        "value": "[redacted]",
                    },
                    {
                        "name": "serviceToken",
                        "_class": "hudson.model.StringParameterValue",
                        "value": "[redacted]",
                    },
                    {
                        "name": "COMPLEX",
                        "_class": "plugin.ComplexParameterValue",
                        "value": "[unsupported non-scalar value]",
                    },
                ]
            }
        ],
    }
    rendered = str(result)
    assert "password-secret" not in rendered
    assert "token-secret" not in rendered
    assert "compact-token-secret" not in rendered
    assert "nested-secret" not in rendered
    assert "internal-user" not in rendered
    await client.close()


@pytest.mark.asyncio
async def test_build_info_redacts_operator_defined_parameter_names() -> None:
    client = make_client(
        lambda request: httpx.Response(
            200,
            json={
                "number": 8,
                "actions": [
                    {
                        "parameters": [
                            {
                                "name": "DEPLOY_AUTH",
                                "value": "local-secret",
                                "_class": "hudson.model.StringParameterValue",
                            },
                            {
                                "name": "REGION",
                                "value": "eu-west-1",
                                "_class": "hudson.model.StringParameterValue",
                            },
                        ]
                    }
                ],
            },
        ),
        MCP_REDACT_PARAMETER_PATTERNS="*_auth,signing_*",
    )

    result = await client.build_info("AI/build", 8)

    parameters = result["actions"][0]["parameters"]
    assert parameters[0]["value"] == "[redacted]"
    assert parameters[1]["value"] == "eu-west-1"
    assert "local-secret" not in str(result)
    await client.close()


@pytest.mark.asyncio
async def test_build_info_omits_non_finite_parameter_numbers() -> None:
    client = make_client(
        lambda request: httpx.Response(
            200,
            content=(
                b'{"number":9,"actions":[{"parameters":['
                b'{"name":"LOAD","value":NaN,'
                b'"_class":"hudson.model.DoubleParameterValue"}]}]}'
            ),
            headers={"Content-Type": "application/json"},
        )
    )

    result = await client.build_info("AI/build", 9)

    assert result["actions"][0]["parameters"][0]["value"] == (
        "[unsupported non-scalar value]"
    )
    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"actions": {}},
        {"actions": [None]},
        {"actions": [{"parameters": {}}]},
        {"actions": [{"parameters": [None]}]},
        {"previousBuild": "not-an-object"},
    ],
)
async def test_build_info_rejects_malformed_response(payload: object) -> None:
    client = make_client(lambda request: httpx.Response(200, json=payload))

    with pytest.raises(JenkinsError, match="malformed build JSON"):
        await client.build_info("AI/build", 7)

    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("start", [True, False, 1.5, "1"])
async def test_console_rejects_non_integer_start(start: object) -> None:
    client = make_client(
        lambda request: pytest.fail(f"invalid start reached Jenkins: {request.url}")
    )

    with pytest.raises(ValueError, match="non-negative integer"):
        await client.console("AI/build", 7, start=start)  # type: ignore[arg-type]

    await client.close()


@pytest.mark.asyncio
async def test_console_rejects_invalid_more_data_header() -> None:
    client = make_client(
        lambda request: httpx.Response(
            200,
            content=b"output",
            headers={"X-Text-Size": "7", "X-More-Data": "yes"},
        )
    )

    with pytest.raises(JenkinsError, match="invalid X-More-Data"):
        await client.console("AI/build", 7)

    await client.close()


@pytest.mark.asyncio
async def test_console_requires_cursor_when_more_data_remains() -> None:
    client = make_client(
        lambda request: httpx.Response(
            200,
            content=b"output",
            headers={"X-More-Data": "true"},
        )
    )

    with pytest.raises(JenkinsError, match="omitted X-Text-Size"):
        await client.console("AI/build", 7)

    await client.close()


@pytest.mark.asyncio
async def test_console_rejects_regressive_cursor_even_when_complete() -> None:
    client = make_client(
        lambda request: httpx.Response(
            200,
            content=b"output",
            headers={"X-Text-Size": "9", "X-More-Data": "false"},
        )
    )

    with pytest.raises(JenkinsError, match="invalid X-Text-Size"):
        await client.console("AI/build", 7, start=10)

    await client.close()


@pytest.mark.asyncio
async def test_console_allows_empty_non_advancing_poll_with_more_data() -> None:
    client = make_client(
        lambda request: httpx.Response(
            200,
            content=b"",
            headers={"X-Text-Size": "10", "X-More-Data": "true"},
        )
    )

    result = await client.console("AI/build", 7, start=10)

    assert result["next_start"] == 10
    assert result["more_data"] is True
    await client.close()
