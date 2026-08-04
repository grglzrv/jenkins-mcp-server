from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import quote

import httpx

from . import __version__
from .audit import AuditLogger
from .config import Settings
from .security import Policy


class JenkinsError(RuntimeError):
    pass


TRAVERSAL_SEGMENTS = {".", ".."}


def _job_path(full_name: str) -> str:
    parts = [part for part in full_name.strip("/").split("/") if part]
    if not parts:
        raise ValueError("job_name must not be empty")
    if any(part in TRAVERSAL_SEGMENTS for part in parts):
        raise ValueError(
            f"job_name must not contain '.' or '..' path segments: {full_name!r}"
        )
    return "/".join(f"job/{quote(part, safe='')}" for part in parts)


class JenkinsClient:
    def __init__(
        self,
        settings: Settings,
        policy: Policy,
        audit: AuditLogger,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.policy = policy
        self.audit = audit
        self.http = httpx.AsyncClient(
            base_url=settings.jenkins_url,
            auth=(settings.jenkins_username, settings.jenkins_token),
            verify=settings.verify,
            timeout=settings.jenkins_timeout_seconds,
            follow_redirects=False,
            transport=transport,
            headers={"User-Agent": f"jenkins-mcp-server/{__version__}"},
        )
        self._crumb: tuple[str, str] | None = None
        # Set when the controller has no crumb issuer, so the probe runs once.
        self._crumb_disabled = False

    async def close(self) -> None:
        await self.http.aclose()

    async def _get_crumb(self) -> tuple[str, str] | None:
        if self._crumb is not None:
            return self._crumb
        if self._crumb_disabled:
            return None
        response = await self.http.get("/crumbIssuer/api/json")
        if response.status_code == 404:
            # CSRF protection is off on this controller. Remember it, otherwise
            # every write pays for a 404 round trip first.
            self._crumb_disabled = True
            return None
        response.raise_for_status()
        data = response.json()
        self._crumb = (data["crumbRequestField"], data["crumb"])
        return self._crumb

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
    ) -> httpx.Response:
        request_headers = dict(headers or {})
        if method.upper() in {"POST", "PUT", "DELETE"}:
            crumb = await self._get_crumb()
            if crumb:
                request_headers[crumb[0]] = crumb[1]

        last_error: Exception | None = None
        crumb_refreshed = False
        attempt = 0
        while attempt <= self.settings.jenkins_max_retries:
            try:
                request_kwargs: dict[str, Any] = {
                    "params": params,
                    "json": json,
                    "headers": request_headers,
                }
                if isinstance(data, (str, bytes)):
                    request_kwargs["content"] = data
                else:
                    request_kwargs["data"] = data

                response = await self.http.request(method, path, **request_kwargs)
                if response.status_code in expected:
                    self.audit.emit(
                        action,
                        "success",
                        status=response.status_code,
                        path=path,
                    )
                    return response

                # A cached crumb goes stale when Jenkins rotates the session.
                # Re-issue it once rather than surfacing a hard 403.
                if (
                    response.status_code == 403
                    and not crumb_refreshed
                    and "crumb" in response.text.lower()
                ):
                    crumb_refreshed = True
                    self._crumb = None
                    crumb = await self._get_crumb()
                    if crumb:
                        request_headers[crumb[0]] = crumb[1]
                        continue

                retryable = response.status_code in {429, 502, 503, 504}
                if retryable and attempt < self.settings.jenkins_max_retries:
                    await asyncio.sleep(min(2**attempt, 5))
                    attempt += 1
                    continue

                body = response.text[:2000]
                self.audit.emit(
                    action,
                    "failure",
                    status=response.status_code,
                    path=path,
                )
                raise JenkinsError(
                    f"Jenkins returned {response.status_code}: {body}"
                )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
                if attempt >= self.settings.jenkins_max_retries:
                    break
                await asyncio.sleep(min(2**attempt, 5))
                attempt += 1

        raise JenkinsError(f"Jenkins request failed: {last_error}")

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
        return response.json()

    async def list_jobs(self, folder: str | None = None) -> Any:
        path = _job_path(folder) if folder else ""
        return await self.api(path, tree="jobs[name,url,color,_class]")

    async def get_job(self, job_name: str) -> Any:
        self.policy.check_job(job_name)
        return await self.api(_job_path(job_name), depth=2)

    async def get_job_config(self, job_name: str) -> str:
        self.policy.check_job(job_name)
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
        self.policy.require_write("job", job_name)
        parent, _, leaf = job_name.rpartition("/")
        path = f"/{_job_path(parent)}/createItem" if parent else "/createItem"
        await self.request(
            "POST",
            path,
            action="job.create",
            params={"name": leaf or job_name},
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
        self.policy.require_destructive("job.update", job_name)
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
        self.policy.require_destructive("job.delete", job_name)
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
        self.policy.require_write("job", job_name)
        operation = "enable" if enabled else "disable"
        await self.request(
            "POST",
            f"/{_job_path(job_name)}/{operation}",
            action=f"job.{operation}",
            expected=(200, 302),
        )
        return {"job": job_name, "enabled": enabled}

    async def copy_job(self, source: str, target: str) -> dict[str, Any]:
        self.policy.check_job(source)
        self.policy.require_write("job", target)
        await self.request(
            "POST",
            "/createItem",
            action="job.copy",
            params={"name": target, "mode": "copy", "from": source},
            expected=(200, 302),
        )
        return {"source": source, "target": target}

    async def build(
        self,
        job_name: str,
        parameters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.policy.require_write("build", job_name)
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
        self.policy.require_destructive("build.stop", job_name)
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
        self.policy.check_job(job_name)
        return await self.api(f"{_job_path(job_name)}/{build_number}", depth=2)

    async def console(
        self,
        job_name: str,
        build_number: int | str = "lastBuild",
        start: int = 0,
    ) -> dict[str, Any]:
        self.policy.check_job(job_name)
        response = await self.request(
            "GET",
            f"/{_job_path(job_name)}/{build_number}/logText/progressiveText",
            action="build.console",
            params={"start": start},
        )
        raw = response.content[: self.settings.max_log_bytes]
        truncated = len(response.content) > self.settings.max_log_bytes
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
        return await self.api("queue", depth=2)

    async def cancel_queue(self, item_id: int) -> dict[str, Any]:
        self.policy.require_destructive("queue.cancel")
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
                    running.append(
                        {"node": computer.get("displayName"), **current}
                    )
        return running

    async def nodes(self) -> Any:
        return await self.api("computer", depth=2)

    async def node_info(self, node_name: str) -> Any:
        encoded_node = quote(node_name, safe="")
        return await self.api(f"computer/{encoded_node}", depth=2)

    async def toggle_node(
        self,
        node_name: str,
        offline: bool,
        message: str = "Managed by MCP",
    ) -> dict[str, Any]:
        self.policy.require_destructive("node.offline")
        current = await self.node_info(node_name)
        if bool(current.get("temporarilyOffline")) != offline:
            encoded_node = quote(node_name, safe="")
            await self.request(
                "POST",
                f"/computer/{encoded_node}/toggleOffline",
                action="node.toggle",
                params={"offlineMessage": message},
                expected=(200, 302),
            )
        return {"node": node_name, "offline": offline}

    async def scan_multibranch(self, job_name: str) -> dict[str, Any]:
        self.policy.require_write("job", job_name)
        await self.request(
            "POST",
            f"/{_job_path(job_name)}/build",
            action="multibranch.scan",
            expected=(201, 302),
        )
        return {"scan_triggered": job_name}

    async def admin_request(
        self,
        method: str,
        path: str,
        body: str | None = None,
        content_type: str = "application/json",
    ) -> dict[str, Any]:
        self.policy.require_write("admin")
        if not path.startswith("/") or re.match(r"^https?://", path):
            raise ValueError("path must be a Jenkins-relative absolute path")
        # '//host/x' is a protocol-relative reference, not a Jenkins path.
        if path.startswith("//"):
            raise ValueError("path must not be protocol-relative")
        if any(part in TRAVERSAL_SEGMENTS for part in path.split("/")):
            raise ValueError("path must not contain '.' or '..' segments")
        response = await self.request(
            method.upper(),
            path,
            action="admin.request",
            data=body,
            headers={"Content-Type": content_type},
            expected=tuple(range(200, 400)),
        )
        return {
            "status": response.status_code,
            "headers": dict(response.headers),
            "body": response.text[: self.settings.max_log_bytes],
        }
