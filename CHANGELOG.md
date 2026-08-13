# Changelog

All notable changes are documented here. The project follows Semantic Versioning
and uses the categories below for every release. Empty categories are retained
with an explicit `None` so compatibility, security, upgrade impact, and known
limitations are never left ambiguous. GitHub Release notes are rendered directly
from the matching version entry after CI validates it.

## [Unreleased]

### Highlights

- None.

### New Features

- None.

### Improvements

- None.

### Bug Fixes

- None.

### Breaking Changes

- None.

### Known Issues

- None.

### Security

- None.

### Upgrade Notes

- No action required.

## [2.9.4] - 2026-08-13

### Highlights

- Health responses no longer expose a server/runtime fingerprint.

### New Features

- None.

### Improvements

- The unnecessary `Server` header is omitted from successful and error
  responses on the direct health listener.

### Bug Fixes

- None.

### Breaking Changes

- None. Only the `Server` header is removed; status codes, bodies and the other
  headers are unchanged.

### Known Issues

- None.

### Security

- `BaseHTTPRequestHandler` advertises its implementation and the exact Python
  patch level by default. Omitting that optional header reduces passive
  fingerprinting on the direct health port. This is defense in depth, not a
  substitute for applying Python and base-image security updates. The MCP port
  was checked separately and does not expose a versioned runtime banner.

### Upgrade Notes

- No action required.

## [2.9.3] - 2026-08-13

### Highlights

- Direct health traffic and Jenkins request targets now have explicit,
  configurable boundaries instead of inheriting unbounded or dependency-owned
  behavior.

### New Features

- `mcp.maxRequestTargetBytes` (`MCP_MAX_REQUEST_TARGET_BYTES`, default 8192)
  caps the exact encoded path and query this server sends to Jenkins, including
  a configured Jenkins context path.
- `mcp.healthMaxConnections` (`MCP_HEALTH_MAX_CONNECTIONS`, default 64) caps
  concurrent connections to the direct server's `/healthz` and `/readyz`
  listener. Minibridge continues to publish its own health endpoint.

### Improvements

- Request-target measurement uses HTTPX's prepared URL, so percent-encoding,
  query parameters, and a Jenkins context path cannot escape the boundary. The
  check runs before the CSRF crumb is fetched and records a redacted
  `request_target_too_long` audit entry.
- Health capacity is reserved before `ThreadingHTTPServer` creates a handler
  thread. Partial request lines expire after five seconds, excess connections
  are closed, and refusal warnings are rate-limited to prevent a second log
  amplification path.

### Bug Fixes

- Unexpected health-handler exceptions once again reach the standard server
  error path; only routine disconnect and timeout errors are suppressed.

### Breaking Changes

- A path or query whose exact encoded request target exceeds 8192 bytes is
  refused before contacting Jenkins. Raise `mcp.maxRequestTargetBytes` before
  upgrading only if a measured legitimate request needs more and every proxy
  and Jenkins is configured to accept it.

### Known Issues

- The MCP transport has already parsed tool arguments before the request-target
  boundary runs; this protects Jenkins egress and proxy interoperability, not
  the memory used to receive an MCP call.
- The direct health listener still uses one Python thread per admitted
  connection. The connection cap and timeout bound that exposure; they do not
  turn the health server into an asynchronous server.

### Security

- `MCP_MAX_REQUEST_BYTES` bounds the body, not an agent-controlled path or
  query. Oversized targets are now rejected predictably before Jenkins or an
  intermediary chooses a different limit.
- The direct health listener previously accepted held-open partial requests
  without a socket timeout or connection bound, allowing an exposed health port
  to create unbounded handler threads. Admission and timeout are now bounded;
  keep the health Service private unless external monitoring requires it.

### Upgrade Notes

- No action is required for the shipped defaults. Operators with unusually long
  Jenkins request targets or more than 64 legitimate simultaneous direct health
  connections should measure demand and set the corresponding typed Helm value
  explicitly before upgrading.

## [2.9.2] - 2026-08-12

### Highlights

- Release and smoke image builds tolerate short upstream asset-delivery
  interruptions instead of stranding an otherwise valid release.

### New Features

- None.

### Improvements

- The pinned Minibridge archive and checksum downloads use the same bounded
  retry policy in every Docker build path.
- k3s smoke jobs retry the complete installer only when its binary or checksum
  download fails; deterministic install and service-start failures still fail
  immediately.

### Bug Fixes

- Fixed intermittent release failures caused by transient HTTP 503 responses,
  connection resets, and incomplete downloads from upstream release-asset
  endpoints.

### Breaking Changes

- None.

### Known Issues

- A persistent upstream outage still fails after the bounded retries; rerun the
  failed workflow after the upstream service recovers.

### Security

- None. Downloaded Minibridge archives remain pinned and checksum-verified.

### Upgrade Notes

- No runtime or configuration change. No action required.

## [2.9.1] - 2026-08-12

### Highlights

- The size of an audit record is no longer chosen by whoever calls the tools.

### New Features

- None.

### Improvements

- Each audit record is capped at 16 KiB. Strings are bounded to 1024 encoded
  JSON bytes and keep an identifying prefix, original UTF-8 byte count, and
  SHA-256 digest when truncated. Container cardinality and depth, mapping keys,
  tuples, binary values, and non-JSON objects are bounded centrally in `_line`,
  so many individually small fields cannot bypass the record ceiling. Ordinary
  job paths and statuses remain byte-for-byte unchanged.

### Bug Fixes

- None.

### Breaking Changes

- None. A field long enough to be truncated was already unreadable.

### Known Issues

- The bound applies to the audit record, not to the tool argument itself. A very
  large argument is still buffered by the MCP transport before any of this runs.
- A deliberately long identifier is not retained in full. Use the SHA-256
  digest embedded in the truncated field to correlate equal values; retain the
  originating MCP gateway record separately when the complete argument is
  required for an investigation.

### Security

- Job and node names reach the audit record verbatim, so a refused call with a
  two megabyte name wrote six megabytes across the audit file and the process
  log stream. Recording refusals, added in 2.8.1, is what made that reachable
  without any successful call: an agent that cannot touch a single job could
  still fill the disk backing the audit volume and flood the stream a SIEM
  ingests. Rotation bounded the file on disk but not the volume of data pushed
  through the log pipeline.
- The initial per-string character limit was insufficient: JSON escaping,
  mapping keys, tuples, and containers with many smaller values could still
  exceed the intended boundary. Enforcement now uses serialized-byte and
  complete-record limits, and the direct k3s smoke verifies a live oversized
  policy denial through Streamable HTTP.

### Upgrade Notes

- No action required.

## [2.9.0] - 2026-08-12

### Highlights

- Request bodies sent to Jenkins are now bounded, closing the asymmetry with
  responses.

### New Features

- `mcp.maxRequestBytes` (`MCP_MAX_REQUEST_BYTES`, default 10000000) caps the body
  this server will send to Jenkins.

### Improvements

- HTTPX-compatible serialization happens once before the CSRF crumb is fetched,
