from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    jenkins_url: str = Field(alias="JENKINS_URL")
    jenkins_username: str = Field(alias="JENKINS_USERNAME")
    jenkins_token: str = Field(alias="JENKINS_TOKEN")
    jenkins_verify_tls: bool = Field(default=True, alias="JENKINS_VERIFY_TLS")
    jenkins_ca_bundle: Path | None = Field(default=None, alias="JENKINS_CA_BUNDLE")
    jenkins_timeout_seconds: float = Field(default=30.0, alias="JENKINS_TIMEOUT_SECONDS")
    jenkins_max_retries: int = Field(default=3, alias="JENKINS_MAX_RETRIES")

    transport: Literal["stdio", "streamable-http"] = Field(
        default="streamable-http",
        alias="MCP_TRANSPORT",
    )
    host: str = Field(default="0.0.0.0", alias="MCP_HOST")
    port: int = Field(default=8000, alias="MCP_PORT")
    mount_path: str = Field(default="/mcp", alias="MCP_PATH")
    health_host: str = Field(default="0.0.0.0", alias="MCP_HEALTH_HOST")
    health_port: int = Field(default=8081, alias="MCP_HEALTH_PORT")

    read_only: bool = Field(default=False, alias="MCP_READ_ONLY")
    allow_job_write: bool = Field(default=True, alias="MCP_ALLOW_JOB_WRITE")
    allow_build_write: bool = Field(default=True, alias="MCP_ALLOW_BUILD_WRITE")
    allow_node_write: bool = Field(default=False, alias="MCP_ALLOW_NODE_WRITE")
    allow_admin_request: bool = Field(default=False, alias="MCP_ALLOW_ADMIN_REQUEST")
    # Master switch for irreversible actions (job delete/update, build stop,
    # queue cancel, node offline). Setting this false disables all of them at once.
    allow_destructive: bool = Field(default=True, alias="MCP_ALLOW_DESTRUCTIVE")
    # Deleting a job is irreversible, so it is opt-in even when job writes are on.
    allow_job_delete: bool = Field(default=False, alias="MCP_ALLOW_JOB_DELETE")
    allow_job_update: bool = Field(default=True, alias="MCP_ALLOW_JOB_UPDATE")
    allow_build_stop: bool = Field(default=True, alias="MCP_ALLOW_BUILD_STOP")
    allowed_jobs: str = Field(default="*", alias="MCP_ALLOWED_JOBS")
    max_log_bytes: int = Field(default=1_000_000, alias="MCP_MAX_LOG_BYTES")
    audit_log_path: Path | None = Field(default=None, alias="MCP_AUDIT_LOG_PATH")

    @field_validator("jenkins_url")
    @classmethod
    def normalize_url(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("mount_path")
    @classmethod
    def normalize_path(cls, value: str) -> str:
        if not value.startswith("/"):
            value = "/" + value
        return value.rstrip("/") or "/mcp"

    @model_validator(mode="after")
    def _check_tls_settings(self) -> Settings:
        """Reject a CA bundle combined with verification disabled.

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
