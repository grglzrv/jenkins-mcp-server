from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass


class PolicyError(PermissionError):
    pass


# Actions that irreversibly remove or overwrite state. Each is gated by its
# category flag AND by the destructive flags below, so an operator can allow
# creating and triggering jobs while still forbidding deletion.
DESTRUCTIVE_ACTIONS = frozenset(
    {"job.delete", "job.update", "build.stop", "queue.cancel", "node.offline"}
)

GLOB_META = re.compile(r"[*?[]")


def _job_segments(job_name: str) -> list[str]:
    if not job_name.strip("/").strip():
        raise PolicyError("Job name must not be empty")
    if job_name != job_name.strip("/") or "//" in job_name:
        raise PolicyError(
            f"Job '{job_name}' has leading, trailing, or repeated '/' separators"
        )
    segments = job_name.split("/")
    if any(part in {".", ".."} for part in segments):
        raise PolicyError(
            f"Job '{job_name}' contains path traversal segments and is rejected"
        )
    return segments


@dataclass(frozen=True)
class Policy:
    read_only: bool
    allow_job_write: bool
    allow_build_write: bool
    allow_node_write: bool
    allow_admin_request: bool
    job_patterns: list[str]
    allow_destructive: bool = False
    allow_job_delete: bool = False
    allow_job_update: bool = True
    allow_build_stop: bool = True

    def check_job(self, job_name: str) -> None:
        _job_segments(job_name)
        if not self.allows_job(job_name):
            raise PolicyError(f"Job '{job_name}' is not allowed by MCP_ALLOWED_JOBS")

    def allows_job(self, job_name: str) -> bool:
        """Return whether a concrete job path is in the configured allowlist."""
        _job_segments(job_name)
        return any(fnmatch.fnmatchcase(job_name, pattern) for pattern in self.job_patterns)

    def allows_job_or_descendant(self, job_name: str) -> bool:
        """Allow browsing a folder that may contain an allowed job.

        Jenkins lists folders before their children. For ``AI/*`` the ``AI``
        folder therefore has to remain visible even though ``AI`` itself does
        not match the pattern. Patterns beginning with a wildcard can match a
        descendant of any folder, so their ancestors cannot be narrowed safely.
        """
        _job_segments(job_name)
        if self.allows_job(job_name):
            return True
        prefix = job_name.strip("/") + "/"
        for pattern in self.job_patterns:
            match = GLOB_META.search(pattern)
            literal_prefix = pattern[: match.start()] if match else pattern
            if not literal_prefix or literal_prefix.startswith(prefix):
                return True
        return False

    def require_destructive(self, action: str, job_name: str | None = None) -> None:
        """Gate an irreversible action.

        The action must clear its category flag (via require_write), the master
        destructive switch, and its own per-action flag.
        """
        if action not in DESTRUCTIVE_ACTIONS:
            raise ValueError(f"'{action}' is not a known destructive action")
        category = {
            "job.delete": "job",
            "job.update": "job",
            "build.stop": "build",
            "queue.cancel": "build",
            "node.offline": "node",
        }[action]
        self.require_write(category, job_name)
        if not self.allow_destructive:
            raise PolicyError(
                f"Destructive action '{action}' is disabled by MCP_ALLOW_DESTRUCTIVE"
            )
        per_action = {
            "job.delete": self.allow_job_delete,
            "job.update": self.allow_job_update,
            "build.stop": self.allow_build_stop,
            "queue.cancel": self.allow_build_stop,
            "node.offline": True,  # already covered by allow_node_write
        }[action]
        if not per_action:
            raise PolicyError(f"Destructive action '{action}' is disabled")

    def require_write(self, category: str, job_name: str | None = None) -> None:
        if self.read_only:
            raise PolicyError("Server is in read-only mode")
        if job_name:
            self.check_job(job_name)
        allowed = {
            "job": self.allow_job_write,
            "build": self.allow_build_write,
            "node": self.allow_node_write,
            "admin": self.allow_admin_request,
        }.get(category, False)
        if not allowed:
            raise PolicyError(f"Write category '{category}' is disabled")
