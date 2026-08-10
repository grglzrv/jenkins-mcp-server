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

    read_only: bool = Field(default=False, alias="MCP_READ_ONLY")
    allow_job_write: bool = Field(default=True, alias="MCP_ALLOW_JOB_WRITE")
    allow_build_write: bool = Field(default=True, alias="MCP_ALLOW_BUILD_WRITE")
    allow_node_write: bool = Field(default=False, alias="MCP_ALLOW_NODE_WRITE")
    allow_admin_request: bool = Field(default=False, alias="MCP_ALLOW_ADMIN_REQUEST")
    # Master switch for irreversible actions (job delete/update, build stop,
    # queue cancel, node offline). Setting this false disables all of them at once.
    allow_destructive: bool = Field(default=False, alias="MCP_ALLOW_DESTRUCTIVE")
    # Deleting a job is irreversible, so it is opt-in even when job writes are on.
    allow_job_delete: bool = Field(default=False, alias="MCP_ALLOW_JOB_DELETE")
    allow_job_update: bool = Field(default=True, alias="MCP_ALLOW_JOB_UPDATE")
    allow_build_stop: bool = Field(default=True, alias="MCP_ALLOW_BUILD_STOP")
    allowed_jobs: str = Field(default="*", alias="MCP_ALLOWED_JOBS")
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
    def job_patterns(self) -> list[str]:
        return [p.strip() for p in self.allowed_jobs.split(",") if p.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
