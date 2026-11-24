# CODEX / GSTACK USAGE + SUBAGENT POLICY v2.6

**Version:** 2.6
**Revises:** v2.5
**Change focus:** Make justified GPT-6 Luna Max delegation a normal engineering option rather than an exception; bring consequential independent review before sealing; preserve narrow scope, verified model routing, and the cumulative four-subagent ceiling.

Current standing project rule.

Apply this policy to **every future Codex or gstack prompt** unless the user explicitly overrides part of it for a specific task.

The goal is to preserve correctness, durability, security, and architectural quality while minimizing unnecessary model usage, duplicated investigation, and redundant verification.

---

## 1. CORE USAGE PRINCIPLE

Use the **narrowest investigation and verification sufficient to complete the task correctly**.

Optimize for efficient model usage without weakening:

- correctness;
- security;
- durability;
- architecture boundaries;
- data integrity;
- migration safety;
- concurrency safety;
- recovery behavior;
- required verification.

Do not trade correctness for token savings.

Do not spend usage on work that does not materially increase confidence in the requested result.

---

## 2. INVESTIGATION DISCIPLINE

Begin with the smallest scope that can plausibly answer the task.

Prefer:

- the current diff;
- changed files;
- failing tests;
- directly affected symbols;
- relevant call sites;
- relevant configuration;
- known error messages;
- existing task context.

Expand outward only when evidence shows that broader investigation is necessary.

Do not begin with a whole-repository review unless the task itself requires one.

### Avoid unnecessary exploration

Do not:

- broadly browse unrelated directories;
- inspect files merely because they are nearby;
- explore speculative improvement opportunities;
- search for unrelated defects during a bounded task;
- inspect generated files, build outputs, caches, vendored dependencies, or artifacts unless relevant;
- perform general architectural exploration when the affected boundary is already known.

Investigation should answer a concrete question.

If there is no concrete question, do not search.

---

## 3. DELTA-FIRST WORKFLOW

For follow-up work, bug fixes, reviews, regressions, or incremental implementation, start with the delta.

Typical investigation order:

1. Current diff.
2. Changed files.
3. Relevant tests.
4. Direct callers and callees.
5. Shared abstractions affected by the change.
6. Broader subsystem only if evidence requires it.
7. Repository-wide search only if necessary to verify a global invariant.

Do not rediscover the entire architecture for every task.

If prior task context, test output, review notes, diffs, or confirmed findings are provided, use them as starting evidence.

Do not restart the investigation from zero unless there is a concrete reason to distrust or invalidate earlier evidence.

---

## 4. CONTEXT + FILE-READ DISCIPLINE

Prefer targeted reads over broad file dumps.

Read only the portions of files needed to understand the relevant behavior.

Do not reread a file or section unless:

- it changed;
- a later finding creates a concrete reason to revisit it;
- the needed detail was not captured earlier;
- conflicting evidence requires rechecking it.

Reuse established facts.

Do not independently rediscover facts already proven earlier in the same task.

Avoid repeatedly opening the same source merely for reassurance.

---

## 5. SEARCH DISCIPLINE

Every search should answer a specific question.

Prefer:

- exact symbol searches;
- filename searches;
- exact error messages;
- exact call-site searches;
- AST queries;
- known configuration keys;
- targeted test names.

Avoid:

- vague keyword fishing;
- repeated equivalent searches;
- searching the same concept through multiple tools without need;
- repository-wide text search when a symbol-level query is sufficient.

Stop searching once sufficient evidence exists.

Do not continue exploring adjacent code simply because it may be interesting.

---

## 6. AST-GREP SKILL

The ast-grep agent skill is already installed and available.

Do **not** install, reinstall, update, replace, or modify the ast-grep skill unless the user explicitly requests it.

Use ast-grep only when it provides a concrete advantage for structural investigation.

Appropriate uses include:

- tracing definitions;
- tracing references;
- locating call sites;
- finding structural relationships;
- locating specific syntax or AST patterns;
- identifying narrowly scoped refactor targets;
- verifying structural invariants across relevant code;
- checking whether a change affects matching constructs elsewhere.

Do not invoke ast-grep merely because it exists.

Prefer ordinary direct inspection when it is sufficient.

Avoid broad or duplicate ast-grep searches.

Prefer precise structural queries.

ast-grep findings are evidence for the main model to evaluate, not automatically authoritative conclusions.

---

## 7. VERIFICATION LADDER

Use the least expensive verification that can confidently establish correctness.

Escalate only when necessary.

Preferred order, when applicable:

1. Static inspection of the changed logic.
2. Focused unit or regression tests.
3. Relevant test module or subsystem.
4. Closely related integration tests.
5. Broader integration suite.
6. Full suite.

Run the full suite when justified by:

- cross-cutting behavior;
- shared infrastructure changes;
- migrations;
- packaging or release work;
- security-sensitive changes;
- concurrency changes;
- durability or recovery changes;
- final sealing;
- uncertainty that narrower verification cannot resolve.

Do not run an expensive unchanged test suite repeatedly unless subsequent changes could affect its result.

Do not add redundant verification merely to increase apparent confidence.

A focused test that directly proves the relevant invariant may be sufficient for a narrow fix.

---

## 8. FAILURE-DRIVEN ESCALATION

Increase scope only in response to evidence.

Examples:

- focused test failure → inspect the affected subsystem;
- unexpected call site → expand structural search;
- shared abstraction change → widen verification;
- schema change → inspect migration and compatibility boundaries;
- concurrency issue → inspect synchronization and lifecycle behavior;
- durability issue → inspect persistence and recovery paths;
- security boundary issue → widen security review;
- unexplained behavior → form and test targeted hypotheses.

Do not begin with maximum investigation breadth "just in case."

---

## 9. STOP CONDITIONS

Before investigating a non-trivial task, identify what evidence would be sufficient to conclude it.

Stop when:

- the relevant behavior is understood;
- the defect or requirement is localized;
- the required change is clear;
- affected architectural boundaries are established;
- focused verification passes;
- no unresolved evidence indicates broader impact.

Do not continue searching for hypothetical defects after the requested task is sufficiently established unless the task explicitly asks for a broader audit.

Do not turn every bounded fix into a general review.

---

## 10. CHANGE DISCIPLINE

Make the smallest coherent change that solves the confirmed problem while preserving established architecture.

Do not:

- opportunistically refactor unrelated code;
- rename unrelated symbols;
- reformat unrelated files;
- clean up unrelated code during a bounded fix;
- introduce speculative abstractions;
- create parallel mechanisms that duplicate existing behavior;
- broaden the feature beyond the requested requirement.

Prefer extending an appropriate existing abstraction over introducing a second competing path.

Do not design for hypothetical future needs unless there is a concrete present requirement.

Scope creep is a defect.

---

## 11. TEST CREATION DISCIPLINE

Add tests when they provide meaningful confidence.

Prioritize tests for:

- confirmed defects;
- newly introduced behavior;
- previously uncovered invariants;
- regression prevention;
- important edge cases;
- failure handling.

Prefer extending existing relevant test modules.

Do not create redundant test structures when existing ones are appropriate.

Do not add tests that merely duplicate existing assertions without proving a distinct invariant.

Avoid excessive combinatorial testing unless each dimension represents meaningfully different behavior.

---

## 12. GPT-6 LUNA MAX SUBAGENTS

Only **authoritatively confirmed GPT-6 Luna Max** subagents are permitted in Codex and gstack tasks. The primary agent may be a different model, but that **does not** authorize a different subagent model.

**Hard ceiling: four subagents for the entire task.** Count every spawned subagent cumulatively across implementation, review, retries, restarts, and handoffs of the same task. The count does not reset between phases. **Four is a maximum, not a target.**

**Delegation is an ordinary, permitted tool when useful.** The primary agent should actively consider whether focused parallel work or independent scrutiny would make the result faster, more correct, safer, or easier to verify. It may delegate proactively without asking for separate approval per spawn when the work is inside the user's authorized scope. Do **not** use "save usage" as a blanket reason to reject beneficial delegation.

Equally, do not delegate merely to reach a number, generate activity, or duplicate work that the primary agent can finish more efficiently alone. **Optimization means best result for total effort, not fewest agents.**

### 12.1 Authoritative model-route verification

Before spawning a subagent, establish using the **actual available tool contract or trusted runtime routing information** that it will run as GPT-6 Luna Max.

- Where the authoritative collaboration interface exposes `gpt-6-luna` with `max` reasoning, select those explicit settings and verify the interface accepts them as GPT-6 Luna Max.
- If another supported interface represents the same model differently, require equally authoritative, unambiguous routing evidence.
- A subagent's conversational self-identification, agent nickname, task title, visual label, or unverified model claim **does not** establish its actual model.
- Do not silently use a default model, an `auto` route that may select another model, or a substitute model.
- If GPT-6 Luna Max cannot be confirmed and selected, **spawn zero subagents** and continue with the primary agent where feasible.
- Reuse valid routing evidence within an unchanged session. Recheck only when model routing, agent configuration, or tool contracts change; do not repeatedly probe identical unavailable routes.

Record the verified model route and reasoning configuration in the final report when available. If no spawn occurred, do not claim routing was verified.

### 12.2 Risk- and value-based delegation

Use the task's actual risk, separability, and verification needs, not a rigid quota:

| Task situation | Practical guidance |
| --- | --- |
| Simple, localized change with clear tests | Usually **0**; use **1** if an independent check resolves a consequential doubt |
| Medium feature with distinct components or meaningful independent checks | **1–2** is reasonable when it reduces bottlenecks or catches likely errors |
| Complex implementation across independent subsystems | **2–3** may be useful; **4** only if four genuinely independent assignments justify coordination overhead |
| Security, permissions, concurrency, data integrity, migrations, service/process lifecycle, durability, or recovery | Strongly consider **at least one targeted independent reviewer or hypothesis-checker** when a specific failure mode matters |
| Time-sensitive debugging with competing plausible causes | A focused alternative-hypothesis subagent can be valuable while the primary agent proceeds |

These are *suggestions, not defaults or permissions to exceed the cap*. The right count may still be zero. A task can be small in line count but high-risk in consequences.

**Positive justification** means being able to name the separate question or deliverable and what confidence, speed, or quality it adds. The expected benefit should exceed task setup, duplicated context, integration, and review costs.

Do not artificially bar subagents from medium-sized work, narrowly scoped bug fixes, or final-gate reviews when a real benefit is present.

### 12.3 Appropriate assignments

Subagents may perform **read-only analysis, implementation, or focused testing** if explicitly authorized by their task contracts. Good assignments include:

- validating an unfamiliar external API or CLI contract against **actual supported behavior**, not just mocked outputs;
- tracing a separate subsystem, call path, or compatibility boundary;
- investigating a competing debugging hypothesis independently;
- implementing a nonoverlapping component with clear file ownership;
- developing targeted regression tests for a distinct invariant;
- reviewing security, authorization, privacy, and data-integrity boundaries;
- analyzing concurrency, worker cancellation, process lifecycle, durability, or recovery;
- checking migration or cross-version compatibility;
- independently challenging a **high-impact conclusion** before it becomes a seal or merge gate.

**Example from TaskPup's lessons:** When implementing systemd integration, an independent reviewer checking the *real command semantics* before sealing can provide more confidence than adding several tests around fabricated responses. Use such reviews when relevant, not as blanket extra bureaucracy.

### 12.4 Coordination and ownership

- Assign each subagent a distinct question, subsystem, file set, or acceptance criterion.
- State whether edits are allowed and which files the subagent owns.
- Parallel coding is permitted only when interfaces and ownership are coordinated; avoid simultaneous conflicting edits.
- The primary agent retains responsibility for architecture, integration, reviewing delegated code, final verification, and user-facing results.
- An independent review may intentionally overlap implementation evidence when challenging it adds value. Otherwise avoid duplicate investigations.
- Spawn when a useful parallel lane becomes clear; do not spawn all four in advance.
- Keep primary work moving while subagents investigate, provided it is safe to do so.
- Reuse completed subagent findings and tests. Do not dispatch a new agent merely to repeat a completed check.
- Subagent findings are **evidence, not automatic authority**. Validate claims before acting.

### 12.5 Delegation contract

Each subagent instruction must specify:

1. The precise question or deliverable and **why delegation helps**.
2. Bounded scope, files/interfaces, and ownership boundaries.
3. Whether the task is read-only or allows code changes.
4. Acceptance criteria and evidence/tests to produce.
5. What not to explore or modify.
6. Whether the result is an implementation input, independent review, or final-gate finding.

Request clear separation of **confirmed defects**, **hypotheses**, and **remaining uncertainty**, with file paths, commands, test results, and reproduction steps where possible.

### 12.6 Review timing and sealing

If a consequential independent review is requested, planned, or justified as a merge/seal gate, **perform it before declaring the feature sealed**, before the final sealing commit/push when practical, and always before merging.

The intended order for such work is:

**IMPLEMENT → TARGETED REVIEW → FIX FINDINGS → VERIFY → SEAL/COMMIT/PUSH → MERGE**

An ordinary small fix does not require an automatic independent review. The review gate is risk-based, not ceremonial.

- Review the actual integration and external behavior, not just the subagent's summary.
- Complete material review findings and targeted verification **before** final sealing.
- If a review uncovers a defect, keep the work **unsealed** until the correction passes the gate.
- Reuse unchanged verification evidence; rerun tests that could be invalidated by the fix, plus full-suite checks when final sealing or cross-cutting risk warrants them.
- Do not routinely seal, then order a planned review, then reseal. A seal is the final evidence-backed decision, not an intermediate checkpoint.
- If code must be committed earlier for backup or collaboration, call that a **development checkpoint**, not sealed or merge-ready.

---

## 13. PROMPT DESIGN

Write Codex and gstack task prompts with a **delegation decision**, not a hardcoded blanket prohibition or a mandatory agent count.

The primary agent should know:

- authorized scope, acceptance criteria, and stop conditions;
- whether separate workstreams, independent hypotheses, or high-impact verification could help;
- which files or interfaces can be owned independently;
- what must be integrated and finally verified by the primary agent;
- that **up to four confirmed GPT-6 Luna Max subagents are permitted when justified**, including meaningful use of one to three;
- that unverified or substituted models are forbidden, and zero remains appropriate when delegation offers no benefit.

For medium or complex tasks, identify likely useful delegation opportunities early, while allowing the agent to decide based on what it finds. A subagent can independently establish the semantics of an unfamiliar external dependency while the primary agent builds the integration. Do not wait until after sealing to schedule a review that was known to be useful during implementation.

### Examples

**Routine bounded fix:** Usually primary-agent only, but permit one focused check when there is a distinct failure mode worth validating.

**Medium implementation:** Consider an independent contract check, a nonoverlapping component, or targeted test coverage. One or two may be worthwhile.

**Complex architecture or multiple subsystems:** Consider two or more complementary assignments early. Use three or four only when the divisions are concrete and integration remains manageable.

**Security, durability, concurrency, migration, recovery, service controls:** An independent reviewer is often valuable before sealing, even when the implementation is not large.

**Final-gate ambiguity:** Use targeted independent scrutiny to resolve a consequential uncertainty before calling work sealed.

### Missing inputs or attachments

When an expected report, file, or attachment is not yet available, **request it and pause that dependent step for the user's response**. Do not immediately issue a terminal `BLOCKED` report solely because the user is still attaching the material. Continue safely independent work if appropriate; do not invent missing content. If material remains unavailable or the user elects not to provide it, report the actual limitation.

---

## 14. EVIDENCE REUSE

Reuse valid evidence already gathered during the task.

Examples:

- passed test output;
- inspected source;
- confirmed call sites;
- validated schemas;
- established invariants;
- prior subagent findings that the main model has checked.

Do not re-run or re-read evidence unless:

- the code changed;
- conflicting evidence emerged;
- the earlier result is insufficient;
- reproducibility needs confirmation.

Independent duplication is acceptable only when independent verification itself has value.

---

## 15. REPOSITORY-WIDE REVIEW RULE

A repository-wide review should be intentional, not accidental.

Use one only when the task requires:

- final sealing;
- broad architectural review;
- repository-wide invariant checking;
- migration readiness;
- security audit;
- release readiness;
- unknown ownership of a defect;
- broad regression risk.

Even during a repository-wide review:

- prioritize high-risk areas;
- avoid rereading irrelevant files;
- avoid speculative improvements;
- separate confirmed defects from optional cleanup;
- do not turn review into uncontrolled refactoring.

---

## 16. IMPLEMENTATION LOOP

For most bounded tasks, prefer:

1. Establish the requirement, risk level, and acceptance criteria.
2. Inspect the minimum relevant code and available real-world API/system behavior.
3. Identify any **specific justified delegation** or independent verification that would materially help.
4. Plan a small, coherent implementation and coordinate distinct ownership if delegating.
5. Implement the change and run focused verification.
6. Complete any planned independent review **before the seal**; address confirmed findings.
7. Expand verification only as needed, including a final full suite when justified by sealing or change impact.
8. Review the resulting diff and final evidence.
9. Seal, commit/push, and merge **only when explicitly authorized and the applicable gates pass**.
10. Stop once the requested outcome and evidence threshold are satisfied.

Not every task requires sealing, a commit, or an independent reviewer. Do not invent a release workflow for a narrow investigation.

For integrations against external tools or operating-system services, include a meaningful check of **real command/API semantics** where feasible. Passing mocks alone does not establish that the external command is valid.

Do not alternate endlessly between exploration and implementation without new evidence. Do not call a provisional implementation checkpoint "sealed".

---

## 17. USAGE-AWARE BEHAVIOR

Usage optimization should influence **how** the model works, not whether it performs required work.

Good optimization:

- narrower searches;
- targeted reads;
- reusing context;
- focused tests;
- proactive, justified use of subagents for separate implementation, contract checks, debugging, or independent verification when the benefit outweighs coordination cost;
- avoiding duplicate verification, especially after prior unchanged evidence is already valid;
- stopping when sufficient evidence exists.

Bad optimization:

- skipping necessary tests;
- weakening security review;
- skipping migration validation;
- accepting uncertainty silently;
- avoiding required architectural analysis;
- failing to investigate a real regression risk;
- treating all subagents as wasteful even when independent checks could prevent consequential rework;
- performing a known-needed independent review only after declaring a feature sealed.

Correctness remains the priority.

## TIME SPENT

Every Codex or gstack task should report the total elapsed wall-clock time spent on the task when that information is available.

Report:

- Task duration: `<actual elapsed time>`
- Start time: `<timestamp if available>`
- End time: `<timestamp if available>`

When useful and directly observable, the report may also include a concise phase breakdown, for example:

- Investigation: `<duration>`
- Implementation: `<duration>`
- Verification: `<duration>`

Do not estimate or invent timing information.

If exact elapsed time or timestamps are unavailable, state:

`Task duration: unavailable`

Do not infer duration from:
- token usage;
- number of tool calls;
- perceived task complexity;
- model usage percentages;
- timestamps that do not actually represent task start and completion.

The purpose of time reporting is to help evaluate workflow efficiency over repeated tasks, not to impose a speed target.

Do not sacrifice correctness, verification, security, durability, or architectural quality merely to reduce reported task duration.
---

## 18. END-OF-TASK REPORT

Every Codex or gstack prompt must require a final report containing:

### RESULT

A concise final outcome.

Examples:

- FIXED
- IMPLEMENTED
- SEALED
- REVIEW COMPLETE
- BLOCKED

### CHANGES

Summarize the meaningful changes made.

Avoid narrating trivial edits.

### VERIFICATION

Report the tests, checks, or validation actually performed.

Do not claim verification that was not run.

### SCOPE

Report:

- investigation scope;
- files or subsystems materially inspected;
- broader verification intentionally skipped, if relevant;
- remaining uncertainty.

If no meaningful uncertainty remains, state:

`Remaining uncertainty: none identified.`

### SUBAGENTS

Report:

- Subagents used: `<0-4>` (cumulative across the task).
- Model of each subagent: `GPT-6 Luna Max`, if one was used.
- Authoritative model-route verification method/evidence, if delegation occurred.
- Purpose and distinct contribution of each subagent.
- Whether any planned pre-seal independent review was completed, when applicable.

If none were used, state:

`Subagents used: 0`

### TIME

Report:

- Task duration: `<actual elapsed time if available>`
- Start time: `<timestamp if available>`
- End time: `<timestamp if available>`

Optionally include directly observed phase timing when useful.

If exact timing is unavailable:

`Task duration: unavailable`

Do not estimate or invent timing.

### USAGE

Report exact usage telemetry when it is directly available.

Prefer:

- Starting short-window / 5-hour usage: `<value if available>`
- Ending short-window / 5-hour usage: `<value if available>`
- Short-window usage consumed by this task: `<exact delta if directly calculable from available values>`
- Starting weekly usage: `<value if available>`
- Ending weekly usage: `<value if available>`
- Weekly usage consumed by this task: `<exact delta if directly calculable from available values>`

If only remaining/end-of-task values are exposed, report:

- Remaining short-window / 5-hour usage: `<value if available>`
- Remaining weekly usage: `<value if available>`

Do not estimate usage values or deltas.

Only calculate a usage delta when both required measurements are directly available and comparable.

If exact usage information is unavailable:

- state that it is unavailable;
- do not estimate;
- do not invent values.
---

## 19. FINAL PRINCIPLE

The purpose of this policy is not to make Codex do less work.

The purpose is to make Codex do **less unnecessary work**.

The preferred task is:

- narrow enough to understand;
- broad enough to be correct;
- verified enough to trust;
- stopped when complete.
