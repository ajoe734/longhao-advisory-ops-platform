# Agent coordination documents

The repository-root `AGENTS.md` is authoritative for every agent. This directory adds no conflicting business rules.

Read `PARALLEL_DEVELOPMENT.md`, `HERDR_RUNBOOK.md`, and the assigned file under `task-packets/`. Fourteen module streams are scheduled together. A provider module's unfinished implementation is resolved with the shared mock and contract fixture, not by pausing the consumer stream. Shared-file changes are coordinated by a named steward while module-owned paths remain independently editable.

Keep proposed decisions distinct from confirmed user policies. Completion reports must distinguish specification validation, module implementation, integration validation and production readiness.
