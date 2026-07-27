---
status: accepted
---

# CLI, MCP, and Hooks Are the Version 0.1 Product Surfaces

Version 0.1 treats the CLI, an explicit MCP server, and post-session import
hooks as its supported product surfaces. All three call the same service
interfaces. MCP calls are visible client tool calls, and hooks process session
transcripts the user already owns; hidden prompt injection remains out of
scope.

The existing loopback HTTP API remains a compatibility adapter, but every new
lifecycle command does not require a duplicate HTTP route. Raven integration,
a watcher daemon, and a dashboard are deferred so the first release completes
one small agent-memory loop without becoming an agent platform.
