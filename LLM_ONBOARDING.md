# LLM Onboarding

First-stop guide for any LLM working inside `longhao-advisory-ops-platform`.

## 1. Read Order

1. `AI_COLLABORATION_GUIDE.md`
2. `ai-status.json` (machine truth; `current-work.md` is only a human summary)
3. `AGENTS.md`
4. `docs/00-document-index.md`
5. `agents/PARALLEL_DEVELOPMENT.md`
6. `project/module-manifest.json`
7. `docs/01-product-scope.md`
8. `docs/02-SA.md`
9. `docs/03-SD.md`
10. `docs/modules/README.md`
11. `api/openapi.yaml`
12. `docs/09-api-events.md`
13. `docs/08-data-model.md`
14. `docs/11-security-rbac.md`
15. `docs/18-decision-register.md`
16. `qa/uat.md`

Current mode is `supervisor_managed_execution`.

## 2. First Prompt

```bash
python3 tools/development-orchestrator/bin/ai_status.py prompt
```

## 3. Shared Truth Rules

- Task state changes only through `tools/development-orchestrator/bin/ai-status.sh`.
- Work only inside your task worktree and its declared write scope.
- Follow `AGENTS.md` for product, data-safety and external-system boundaries.
