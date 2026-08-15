from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # case_sensitive is deliberate. pydantic-settings matches environment
    # variables case-insensitively by default, so `jenkins_token` would be read
    # as JENKINS_TOKEN. The chart blocks chart-owned names in mcp.extraEnv, but
    # it can only reject the spellings it knows, and a lowercase duplicate would
    # then override the credential and policy values the chart validated.
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="",
        extra="ignore",
        case_sensitive=True,
    )

    jenkins_url: str = Field(alias="JENKINS_URL")
    jenkins_username: str = Field(alias="JENKINS_USERNAME")
    jenkins_token: str = Field(alias="JENKINS_TOKEN")
    jenkins_verify_tls: bool = Field(default=True, alias="JENKINS_VERIFY_TLS")
    jenkins_ca_bundle: Path | None = Field(default=None, alias="JENKINS_CA_BUNDLE")
    jenkins_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        alias="JENKINS_TIMEOUT_SECONDS",
    )
    # How many requests this process may have in flight at Jenkins. httpx
    # defaults to 100 connections, which is a sensible general-purpose default
    # and a poor one for a shared controller: an agent can fan out tool calls freely,
    # and Jenkins serves its UI, its agents and every other integration from one
    # Jetty thread pool. Ten keeps a replica a well-behaved client; raise it if
    # the controller has headroom.
    jenkins_max_concurrency: int = Field(
        default=10, alias="JENKINS_MAX_CONCURRENCY", ge=1, le=100
    )
    jenkins_max_retries: int = Field(
        default=3,
        ge=0,
        le=10,
        alias="JENKINS_MAX_RETRIES",
    )

    transport: Literal["stdio", "streamable-http"] = Field(
        default="streamable-http",
        alias="MCP_TRANSPORT",
    )
    host: str = Field(default="0.0.0.0", alias="MCP_HOST")
    port: int = Field(default=8000, ge=1, le=65535, alias="MCP_PORT")
    mount_path: str = Field(default="/mcp", alias="MCP_PATH")
    health_host: str = Field(default="0.0.0.0", alias="MCP_HEALTH_HOST")
    health_port: int = Field(default=8081, ge=0, le=65535, alias="MCP_HEALTH_PORT")
    # Reserve health-handler capacity before spawning a thread. Combined with
    # the per-socket timeout, this bounds slow or incomplete probe traffic.
    health_max_connections: int = Field(
        default=64, ge=1, le=1024, alias="MCP_HEALTH_MAX_CONNECTIONS"
    )

    read_only: bool = Field(default=False, alias="MCP_READ_ONLY")
    allow_job_write: bool = Field(default=True, alias="MCP_ALLOW_JOB_WRITE")
    allow_build_write: bool = Field(default=True, alias="MCP_ALLOW_BUILD_WRITE")
    allow_node_write: bool = Field(default=False, alias="MCP_ALLOW_NODE_WRITE")
    allow_admin_request: bool = Field(default=False, alias="MCP_ALLOW_ADMIN_REQUEST")
    # The Groovy console runs arbitrary code on the controller, which is a
    # different decision from allowing an arbitrary API call. Minibridge's
    # sensitive-pattern guardrail refuses it when enabled, but that layer is
    # optional, so the always-enforced layer must not be the weaker of the two.
    allow_script_console: bool = Field(
        default=False, alias="MCP_ALLOW_SCRIPT_CONSOLE"
    )
    # Master switch for irreversible actions (job delete/update, build stop,
    # queue cancel, node offline). Setting this false disables all of them at once.
    allow_destructive: bool = Field(default=False, alias="MCP_ALLOW_DESTRUCTIVE")
    # Deleting a job is irreversible, so it is opt-in even when job writes are on.
    allow_job_delete: bool = Field(default=False, alias="MCP_ALLOW_JOB_DELETE")
    allow_job_update: bool = Field(default=True, alias="MCP_ALLOW_JOB_UPDATE")
    allow_build_stop: bool = Field(default=True, alias="MCP_ALLOW_BUILD_STOP")
    # Origins Jenkins may use in the task and build URLs it reports, beyond
    # jenkins_url itself. Jenkins advertises its own configured root, which
    # behind a reverse proxy or ingress differs from the address this client
    # connects through, so those deployments must name it here to have queue
    # item lookups and cancellation trust it. Empty means only jenkins_url is
    # trusted for that decision; listings are unaffected either way.
    #   MCP_JENKINS_PUBLIC_ORIGINS=https://ci.example.com
    jenkins_public_origins: str = Field(
        default="", alias="MCP_JENKINS_PUBLIC_ORIGINS"
    )

    allowed_jobs: str = Field(default="*", alias="MCP_ALLOWED_JOBS")
    # Additional case-insensitive globs for parameter names whose values must
    # never cross the MCP response boundary. The built-in password/token/
    # credential detection remains active; this closes the gap for locally
    # named secrets such as DEPLOY_AUTH or SIGNING_MATERIAL.
    redact_parameter_patterns: str = Field(
        default="",
        alias="MCP_REDACT_PARAMETER_PATTERNS",
    )
    # Cap on the encoded request target, which the body cap does not cover.
    # 8192 is a conservative interoperability boundary and remains configurable
    # because proxy and Jenkins request-line limits vary by deployment.
    max_request_target_bytes: int = Field(
        default=8192, ge=256, le=65536, alias="MCP_MAX_REQUEST_TARGET_BYTES"
    )
    # Cap the exact encoded request body before any Jenkins or crumb request.
    # Ten MB matches the response boundary without imposing a smaller,
    # unsurveyed limit on existing job definitions.
    max_request_bytes: int = Field(
        default=10_000_000, ge=1024, le=100_000_000, alias="MCP_MAX_REQUEST_BYTES"
    )
    # Bound every response from Jenkins. Console and administrator calls use
    # the smaller max_log_bytes limit because their payload is returned as
    # text; ordinary API and config responses must be complete to be useful.
    max_response_bytes: int = Field(
        default=10_000_000,
        ge=1024,
        alias="MCP_MAX_RESPONSE_BYTES",
    )
    max_log_bytes: int = Field(default=1_000_000, ge=1, alias="MCP_MAX_LOG_BYTES")
    audit_log_path: Path | None = Field(default=None, alias="MCP_AUDIT_LOG_PATH")
    audit_max_bytes: int = Field(default=0, ge=0, alias="MCP_AUDIT_MAX_BYTES")
    audit_backup_count: int = Field(
        default=0,
        ge=0,
        le=100,
        alias="MCP_AUDIT_BACKUP_COUNT",
    )
    # Whether an unwritable audit file should take the pod out of service.
    # Off by default: records also go to the process logs, which is the durable
    # path in a cluster, so a failed redundant copy should not stop the server
    # answering requests it can still serve and audit.
    audit_required_for_readiness: bool = Field(
        default=False, alias="MCP_AUDIT_REQUIRED_FOR_READINESS"
    )

    @field_validator("jenkins_url")
    @classmethod
    def normalize_url(cls, value: str) -> str:
        normalized = value.rstrip("/")
        parsed = urlsplit(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("JENKINS_URL must be an absolute HTTP or HTTPS URL")
        if parsed.username or parsed.password:
            raise ValueError("JENKINS_URL must not contain embedded credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("JENKINS_URL must not contain a query string or fragment")
        return normalized

    @field_validator("mount_path")
    @classmethod
    def normalize_path(cls, value: str) -> str:
        if not value.startswith("/"):
            value = "/" + value
        return value.rstrip("/") or "/mcp"

    @model_validator(mode="after")
    def _check_cross_field_settings(self) -> Settings:
        """Reject contradictory TLS and audit settings.

        The two settings contradict each other, and honouring either one would
        be a guess about intent, so fail instead of guessing.
        """
        if self.jenkins_ca_bundle and not self.jenkins_verify_tls:
            raise ValueError(
                "JENKINS_CA_BUNDLE is set but JENKINS_VERIFY_TLS is false. "
                "A CA bundle only has meaning when TLS verification is enabled. "
                "Remove the bundle to disable verification, or set "
                "JENKINS_VERIFY_TLS=true to verify against it."
            )
        if self.audit_required_for_readiness and not self.audit_log_path:
            raise ValueError(
                "MCP_AUDIT_REQUIRED_FOR_READINESS requires MCP_AUDIT_LOG_PATH"
            )
        rotation_values = (self.audit_max_bytes, self.audit_backup_count)
        if bool(rotation_values[0]) != bool(rotation_values[1]):
            raise ValueError(
                "MCP_AUDIT_MAX_BYTES and MCP_AUDIT_BACKUP_COUNT must either both "
                "be zero or both be positive"
            )
        if any(rotation_values) and not self.audit_log_path:
            raise ValueError("audit rotation requires MCP_AUDIT_LOG_PATH")
        return self

    @property
    def verify(self) -> bool | str:
        """The httpx `verify` argument.

        A path pins trust to that CA, True uses the system trust store, which is
        what a publicly issued certificate such as Let's Encrypt or Tailscale
        needs, and False disables verification entirely.
        """
        if self.jenkins_ca_bundle:
            return str(self.jenkins_ca_bundle)
        return self.jenkins_verify_tls

    @property
    def trusted_origins(self) -> tuple[str, ...]:
        """Origins whose task URLs may identify a job, as scheme://host[:port]."""
        origins: list[str] = []
        for raw in (self.jenkins_url, *self.jenkins_public_origins.split(",")):
            candidate = raw.strip()
            if not candidate:
                continue
            parsed = urlsplit(candidate)
            if not parsed.scheme or not parsed.netloc:
                continue
            origins.append(f"{parsed.scheme}://{parsed.netloc}".lower())
        return tuple(dict.fromkeys(origins))

    @property
    def job_patterns(self) -> list[str]:
        return [p.strip() for p in self.allowed_jobs.split(",") if p.strip()]

    @property
    def parameter_redaction_patterns(self) -> list[str]:
        return [
            pattern.strip()
            for pattern in self.redact_parameter_patterns.split(",")
            if pattern.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
