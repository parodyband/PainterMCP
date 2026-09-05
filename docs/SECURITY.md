# Trust boundary

Painter MCP is a local, trusted-user automation bridge. The Python and JavaScript
escape hatches execute with Painter's filesystem, network and application access.
They are intentionally not a sandbox. A client possessing the connection-file
credential can mutate projects and execute code. Sessions prevent accidental
namespace collisions; they do not isolate mutually untrusted users.

The listener binds only IPv4 loopback, requires a random startup bearer credential,
checks the exact loopback Host header, rejects browser Origin headers, disables
HTTP proxy routing in its client and has no CORS support. Credentials live in a
restricted-permission user file. Authentication failures do not expose the token.
Request bodies are limited to 1 MiB; worker concurrency, queues, result memory,
session count and image/fact responses have documented bounds. Arbitrary script
namespace memory and native application work cannot be forcibly bounded.

The installer preserves unrelated configuration and detects modified managed
files. Backups of client configuration remain private local files and can contain
the user's other configuration; do not commit them. Generated connections,
projects, export assets, validation images and local installation data are excluded
from Git. Distribution checks reject application binaries and proprietary assets.

CI runs with read-only repository permissions. Only the final tag-release publish
job has `contents:write`. Action versions are pinned to commit IDs. Licensed
Painter CI is manual, disabled by a repository variable, restricted to `main`,
protected by an environment review and isolated on an operator-managed runner.
It is never triggered for pull requests. Do not give unreviewed PR code access to
a licensed workstation or user project directory.

Release updates trust this repository's publisher and the Python package index.
HTTPS origin/redirect restrictions, manifests, size limits, hashes and safe archive
handling protect preparation. Downloaded release scripts are not executed: a verified
wheel is installed into a separate environment. Only a ready package can become
pending, and selection changes on Painter startup. User-edited managed plugin files
block staging/activation. See [update security and lifecycle](UPDATES.md).

Report a security issue privately to the repository owner using GitHub's private
vulnerability reporting when enabled. Do not include tokens, project files or
application binaries in public issues. Minimal reproduction steps and redacted
`doctor` output are normally sufficient.
