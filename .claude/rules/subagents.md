---
description: Subagent delegation, authority and scope, and the single-writer rule for shared docs. Use when delegating work to subagents.
alwaysApply: true
---

# Subagents

**Delegation**
- Delegate only when the task calls for it (multi-file, parallelizable, or context-heavy). If the main agent can do it without meaningful context cost, do it inline.
- Also inline when: I ask or imply it, or I'm doing a quick thing rather than a spec. Unsure → ask.
- Model: haiku for trivial, well-defined tasks; sonnet when implementation is uncertain.
- Every brief includes: goal, pass criteria, branch/worktree, files in scope, decisions already made.

**Authority & scope**
- I talk to subagents only through the main agent. Every decision needing my input (incl. module structure) is resolved with me **before** delegation. A subagent hitting an unresolved decision stops and reports back; main agent relays to me.
- Subagents don't devise their own work — scope is the work-package/spec agreed between me and the main agent. Deviating (adding, dropping, reshaping tasks) needs a near-blocking reason; even then, the subagent stops and reports up rather than acting on it. A scope change ships only after I confirm; the main agent then updates the spec and its pass criteria, and the merge into main carries that updated spec file along with the code.
- Budget guard: if a subagent or task runs well beyond expectation (default: >30 tool calls, repeated failed attempts, or main context getting heavy), pause and notify me with status + options (continue / narrow scope / stop).
- Subagents never commit. Main agent verifies against Definition of done, then commits.
- Parallel work: hold back items touched by >1 process; tell me and propose a workaround.

**Shared docs — single writer (main agent), after merge**
- Catalogue, graph: generated only (banner "GENERATED — do not edit"). On conflict, regenerate.
- `next-steps.md`: subagents list deferrals in their final report; main agent proposes them to me and appends only once I confirm.
- Specs: read-only for subagents; status changes by main agent.
- Enforcement: briefs list shared docs as out of scope; main agent rejects any subagent diff touching them.
