"""Tests for enabling/disabling destructive actions independently of writes."""

import httpx
import pytest

from jenkins_mcp_server.audit import AuditLogger
from jenkins_mcp_server.client import JenkinsClient
from jenkins_mcp_server.config import Settings
from jenkins_mcp_server.security import DESTRUCTIVE_ACTIONS, Policy, PolicyError


def policy(**overrides: object) -> Policy:
    values: dict[str, object] = {
        "read_only": False,
        "allow_job_write": True,
        "allow_build_write": True,
        "allow_node_write": True,
        "allow_admin_request": False,
        "job_patterns": ["*"],
    }
    values.update(overrides)
    return Policy(**values)  # type: ignore[arg-type]


def client(pol: Policy) -> JenkinsClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/crumbIssuer/api/json":
            return httpx.Response(
                200, json={"crumbRequestField": "Jenkins-Crumb", "crumb": "c"}
            )
        return httpx.Response(200, json={})

    settings = Settings(
        JENKINS_URL="https://jenkins.test",
        JENKINS_USERNAME="u",
        JENKINS_TOKEN="t",
        JENKINS_MAX_RETRIES=0,
    )  # type: ignore[call-arg]
    return JenkinsClient(
        settings, pol, AuditLogger(None), transport=httpx.MockTransport(handler)
    )


# --- defaults -------------------------------------------------------------


def test_job_delete_is_opt_in_even_with_job_write_enabled() -> None:
    """The whole point: create/update allowed, delete still refused."""
    with pytest.raises(PolicyError, match="job.delete"):
        policy(allow_destructive=True).require_destructive("job.delete", "AI/build")


def test_job_write_alone_still_permits_creation() -> None:
    policy().require_write("job", "AI/build")


def test_enabling_job_delete_permits_it() -> None:
    policy(allow_destructive=True, allow_job_delete=True).require_destructive(
        "job.delete", "AI/build"
    )


# --- master switch --------------------------------------------------------


@pytest.mark.parametrize("action", sorted(DESTRUCTIVE_ACTIONS))
def test_master_switch_disables_every_destructive_action(action: str) -> None:
    pol = policy(
        allow_destructive=False,
        allow_job_delete=True,
        allow_job_update=True,
        allow_build_stop=True,
    )
    with pytest.raises(PolicyError, match="MCP_ALLOW_DESTRUCTIVE"):
        pol.require_destructive(action, "AI/build")


@pytest.mark.parametrize("action", sorted(DESTRUCTIVE_ACTIONS))
def test_all_destructive_actions_pass_when_fully_enabled(action: str) -> None:
    pol = policy(allow_destructive=True, allow_job_delete=True)
    pol.require_destructive(action, "AI/build")


def test_unknown_action_is_rejected_loudly() -> None:
    with pytest.raises(ValueError, match="not a known destructive action"):
        policy().require_destructive("job.nuke")


# --- interaction with existing gates --------------------------------------


def test_read_only_still_wins_over_destructive_flags() -> None:
    pol = policy(read_only=True, allow_job_delete=True, allow_destructive=True)
    with pytest.raises(PolicyError, match="read-only"):
        pol.require_destructive("job.delete", "AI/build")


def test_category_gate_still_applies_to_destructive_actions() -> None:
    pol = policy(
        allow_destructive=True, allow_build_write=False, allow_build_stop=True
    )
    with pytest.raises(PolicyError, match="build"):
        pol.require_destructive("build.stop", "AI/build")


def test_job_allowlist_still_applies_to_destructive_actions() -> None:
    pol = policy(
        allow_destructive=True, job_patterns=["AI/*"], allow_job_delete=True
    )
    with pytest.raises(PolicyError, match="not allowed"):
        pol.require_destructive("job.delete", "Production/secret")


def test_build_stop_disabled_does_not_block_triggering() -> None:
    pol = policy(allow_build_stop=False)
    pol.require_write("build", "AI/build")
    with pytest.raises(PolicyError, match="build.stop"):
        pol.require_destructive("build.stop", "AI/build")


# --- end-to-end through the client ---------------------------------------


@pytest.mark.asyncio
async def test_client_delete_job_blocked_by_default() -> None:
    jc = client(policy())
    with pytest.raises(PolicyError):
        await jc.delete_job("AI/build")
    await jc.close()


@pytest.mark.asyncio
async def test_client_stop_build_blocked_when_disabled() -> None:
    jc = client(policy(allow_build_stop=False))
    with pytest.raises(PolicyError):
        await jc.stop_build("AI/build", 3)
    await jc.close()


@pytest.mark.asyncio
async def test_client_cancel_queue_blocked_when_disabled() -> None:
    jc = client(policy(allow_build_stop=False))
    with pytest.raises(PolicyError):
        await jc.cancel_queue(42)
    await jc.close()


@pytest.mark.asyncio
async def test_bringing_node_online_does_not_require_destructive_switch() -> None:
    jc = client(policy(allow_destructive=False, allow_node_write=True))
    result = await jc.toggle_node("agent", False)
    assert result == {"node": "agent", "offline": False}
    await jc.close()


@pytest.mark.asyncio
async def test_client_trigger_build_unaffected_by_stop_being_disabled() -> None:
    jc = client(policy(allow_build_stop=False))
    # Trigger must still work; only the destructive counterpart is blocked.
    with pytest.raises(Exception) as exc:
        await jc.build("AI/build")
    assert not isinstance(exc.value, PolicyError)
    await jc.close()


def test_settings_expose_the_new_flags_with_safe_defaults() -> None:
    settings = Settings(
        JENKINS_URL="https://jenkins.test",
        JENKINS_USERNAME="u",
        JENKINS_TOKEN="t",
    )  # type: ignore[call-arg]
    assert settings.allow_destructive is False
    assert settings.allow_job_delete is False
    assert settings.allow_job_update is True
    assert settings.allow_build_stop is True
