# MODEL-NEUTRAL USAGE + SUBAGENT POLICY v2.7

**Version:** 2.7
**Revises:** MODEL-NEUTRAL-USAGE-SUBAGENT-POLICYv2.6.1.MD
**Change focus:** Make justified native subagent delegation an ordinary engineering option for non-Codex coding agents; preserve verified same-model routing and the cumulative four-subagent ceiling; complete consequential independent review **before** sealing rather than after.

Reusable policy for non-Codex coding agents, whether cloud-hosted or locally running, including Gemini, Qwen, and other compatible agent environments. Apply when the user or project selects it. Codex/gstack tasks should use the separate Codex policy unless explicitly directed otherwise.

**Platform-native subagents are permitted.** Agents may use their own supported delegation tools, native collaboration APIs, child-agent frameworks, or verified local harnesses. They do **not** need to run through Codex or use GPT-6 Luna Max merely because a separate Codex-specific policy requires Luna Max. However, this policy's verified same-underlying-model rule and any **explicitly applicable stricter user/project restriction** remain binding. Do not assume a native spawn automatically inherits the parent's model; establish the effective route as described in Section 12.

**Model-matching examples:** A primary agent running **Gemini 4 Neon** may use confirmed **Gemini 4 Neon** subagents through its native tools; a primary running **Qwen3.8 Flash Next** may use confirmed **Qwen3.8 Flash Next** subagents. A different model or unverified fallback is not an acceptable substitution under this policy.

The goal is to preserve correctness, durability, security, and architectural quality while minimizing unnecessary model usage, duplicated investigation, avoidable rework, and redundant verification. **Optimization is about the best result for total effort, not the lowest possible subagent count.**

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

If the ast-grep agent skill is already installed and available, use it as described below.

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

## 12. NATIVE, VERIFIED SAME-MODEL SUBAGENTS

**Native delegation is an ordinary permitted engineering tool.** The primary agent should proactively consider whether its own supported subagent system could deliver a distinct implementation slice, test an independent hypothesis, validate an external contract, or strengthen a consequential review. Do not reject beneficial delegation merely to minimize the number of agents or reported usage.

**The hard ceiling is four subagents per entire task, counted cumulatively.** This covers every child spawned through native tools, alternate provider tools, nested child-agent delegation, retries, restarts, phases, reviews, and handoffs of the same task. **Four is a maximum, not a target.** Previously spawned children count even when they fail or finish early. The primary agent owns the count; never evade it by relabeling or restarting a phase. Do not permit uncontrolled recursive spawning.

### 12.1 Model matching for native agents

Only subagents running the **same verified underlying model and variant as the primary agent** are permitted under this model-neutral policy. The name of the agent *harness*, provider or role is not the model route.

- **Gemini Neon primary** → native Gemini Neon child, **if** its effective underlying model route is verified as matching.
- **Qwen primary** → native child using the same confirmed Qwen checkpoint and variant.
- **Other primary** → child using that same confirmed underlying model/variant.
- A Codex-only named-model rule does **not automatically apply** to non-Codex tasks. An explicit *task-wide* named-model restriction from the user or project does apply and takes precedence over this policy.

A platform's native agent feature is a valid **way to spawn**, not by itself proof of matching models. Do not trust child self-identification, agent nicknames, marketing labels, guessed defaults, or an unchecked `auto` route.

**Three valid verification paths:**

**A. Explicit matching route.** Read reliable runtime/session routing metadata or effective agent configuration for the primary; select the same resolved model/checkpoint and variant for the child using a supported native tool parameter or configuration; confirm the tool honors that route rather than substituting a default.

**B. Guaranteed native inheritance.** An authoritative native tool contract, runtime configuration, or documented implementation guarantees children inherit the *effective* primary model route unchanged. Confirm that auto-routing, model substitution, independent child defaults, provider fallback, and different-model load balancing cannot break this guarantee. If guaranteed, a visible model-name string is unnecessary; record the inheritance evidence. **Do not impose extra model probing just because the native agent hides the model ID.**

**C. Verified fixed single-model local backend.** When a local harness hides model IDs or lacks a formal inheritance guarantee, inspect trusted effective routing configuration, implementation paths, and/or authoritative request logs. Confirm the parent and child use the same fixed backend serving exactly one matching model checkpoint/variant, with no child default or fallback that can select a different model. Sharing only an endpoint/port does not suffice when multiple models could be selected. A parent or child self-report is not enough.

Use the simplest applicable path. Reuse trustworthy routing evidence during an unchanged session; recheck only if the agent configuration, effective route, backend, or tool contract changes. **If none of these paths establishes a match, use zero subagents and continue with the primary agent where feasible.** Do not silently substitute another model or stall a bounded task repeatedly probing routes.

**Stricter requirements still win:** If a user explicitly imposes a specifically named subagent model for the current task (such as GPT-6 Luna Max), the verified route must also meet that restriction. This document does not silently override a separate, explicitly applicable user, repository, or platform restriction. Conversely, don't transplant a rule scoped specifically to Codex into unrelated Gemini/Qwen tasks without an instruction requiring it.

### 12.2 Risk- and value-based delegation

The correct count depends on independent value, separability, risk, and integration cost, not a rigid quota:

| Task profile | Practical guidance |
| --- | --- |
| Clear, local fix or cosmetic UI adjustment | Usually **0**; **1** is reasonable for a distinct high-value check |
| Medium implementation with separable pieces | Consider **1–2** native subagents when the work can be integrated cleanly |
| Large, genuinely multi-subsystem implementation | **2–3** may help; **4** is allowed only for four materially distinct assignments |
| Security, authorization, privacy, concurrency, system-process lifecycle, migration, data loss, or durability risks | Strongly consider an **independent targeted reviewer or hypothesis-checker** when a consequential failure mode needs scrutiny |
| Ambiguous debugging with credible competing causes | One agent can test an alternative hypothesis while the primary makes safe progress |

A short change can be high-risk. Do not under-delegate simply because the task is narrow; do not over-delegate because the tools are available. To justify a child, name its separate question or deliverable and the confidence, speed, or correctness it adds. The expected benefit must outweigh the coordination and integration cost.

### 12.3 Appropriate native subagent work

Good assignments include:

- independently verifying the **actual semantics** of an unfamiliar CLI/API, OS subsystem, or external dependency;
- tracing a distinct call path, architecture boundary, or compatibility concern;
- testing a competing debugging hypothesis;
- implementing a nonoverlapping component with agreed interfaces and file ownership;
- implementing or validating regression tests for a distinct invariant;
- security, privacy, permission, and data-integrity review;
- concurrency, worker cancellation, subprocess lifecycle, shutdown, recovery, and durability review;
- migration or cross-version compatibility validation;
- independent scrutiny of a **consequential conclusion** before it becomes a seal or merge gate.

**TaskPup lesson:** A real systemd command-contract check before sealing can catch an issue that synthetic parser tests cannot. Use such a lane for relevant integrations, without making every change undergo ceremony.

### 12.4 Native coordination and limits

- Each child gets a distinct deliverable, bounded scope, acceptance criteria, and evidence expectations.
- State clearly whether it may edit code, and which files or interfaces it owns. Avoid competing edits.
- The primary agent retains responsibility for architecture, integration, reviewing child changes, final verification, and user-facing conclusions.
- Independent review may intentionally repeat a narrow check to challenge high-impact evidence; other duplication should be avoided.
- Spawn a useful child when its lane becomes clear, not all four preemptively.
- Keep primary work progressing while children work when safe.
- **Do not allow a child to spawn further children by default.** Nested delegation needs explicit primary coordination, the same verified-route rule, and available slots within the **single cumulative cap of four**.
- Native delegation does not authorize broader repository access, file writes, credentials, shell commands, system changes, commits, or pushes beyond the original task.
- Child conclusions are evidence, not automatic authority. Resolve material disagreements with targeted verification.

### 12.5 Subagent task contract

For each native spawn, specify:

1. The precise question or deliverable, and why delegation materially helps.
2. Scope, relevant source/files/interfaces, and ownership boundaries.
3. Whether the task is read-only or permits code changes.
4. Acceptance criteria, tests, commands, and evidence to return.
5. What must not be explored, changed, committed, or delegated.
6. Whether it contributes implementation, independent analysis, regression coverage, or a pre-seal review.
7. The verified routing path (A/B/C) relied on, with trustworthy evidence retained by the primary.

Request clear separation between confirmed defects, hypotheses, and remaining uncertainty. When possible, findings include file paths, lines, reproducible behavior, and test outcomes.

### 12.6 Review timing and a real seal

When a consequential independent review is **requested, planned, or justified** as a merge/sealing gate, it must occur **before declaring the feature sealed**, preferably before the final sealing commit/push and always before merging.

Preferred sequence for such work:

**IMPLEMENT → TARGETED REVIEW → FIX CONFIRMED FINDINGS → VERIFY → SEAL / COMMIT / PUSH → MERGE**

A small routine fix does not automatically need independent review. This is a **risk-based gate**, not a ritual.

- Review the actual code and external behavior where relevant, not solely a child report or fabricated fixtures.
- Fix material review findings before the seal; verify the delta with focused checks and broaden only for affected shared infrastructure or release risk.
- Reuse earlier, still-valid verification; do not repeat full investigations and packaging checks by reflex.
- If an earlier commit is useful for backup or collaboration, call it a **development checkpoint**. Do not call it sealed/merge-ready until the review and verification gates pass.
- If a P1/P0 issue is found, keep the work unsealed until corrected and reviewed. Do not manufacture a loop of seal → review → reseal.
- A review after merging or sealing may still uncover defects, but it is not a substitute for a planned pre-seal review.

---

## 13. PROMPT DESIGN FOR PLATFORM-NATIVE AGENTS

Give the primary agent an explicit delegation **decision**, not an arbitrary mandatory child count or a blanket ban. A native agent may proactively spawn its own verified same-model children within authorized scope; it does not need Codex tooling or per-spawn user approval.

Prompts should establish:

- authorized task, acceptance criteria, safe boundaries, and stop conditions;
- likely independent workstreams, high-impact external contracts, or review questions;
- the distinction between implementation and final independent scrutiny;
- child file ownership and who integrates results;
- **up to four cumulative native same-model subagents** when beneficial, with zero still fine;
- that Path A, B, **or** C can verify model matching, and the agent should not demand self-identification when an authoritative inheritance or fixed-backend route suffices;
- that an explicitly applicable stricter named-model restriction must still be honored;
- pre-seal completion of any planned consequential review.

### Typical prompt choices

**Routine fix:** Primary agent usually handles it, but one independent check is fine for a real risk.

**Medium implementation:** One or two children may verify a distinct external contract, implement nonoverlapping code, or build useful regression coverage.

**Complex task:** Plan two or more independent lanes early when boundaries are clear, but do not spawn extra children without a reason.

**Security, durability, concurrency, service management, migrations:** Strongly consider a focused independent check before sealing, even if implementation is short.

**Final-gate ambiguity:** Use justified independent scrutiny before declaring a consequential result sealed.

### Missing material

When an expected report, file, or attachment has not yet arrived, ask the user for it and pause only the dependent step. Continue independent safe work when possible. Do not fabricate material or mark the entire task `BLOCKED` solely because an attachment is in transit; report a genuine unavailable input when applicable.

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

1. Establish the requirement, risk, acceptance criteria, and **evidence threshold** for completion.
2. Inspect the minimum relevant code and, for integrations, check the real external command/API behavior where feasible.
3. Identify specific useful native subagent lanes or independent checks; verify the model route **before** spawning.
4. Plan the smallest coherent implementation and coordinate file ownership if delegating.
5. Implement and run focused tests.
6. Complete **planned, consequential independent review before sealing**; fix confirmed findings.
7. Verify the final delta; widen testing only when changes, release work, or remaining uncertainty justify it.
8. Review the actual diff, final safety boundaries, and evidence.
9. Seal, commit/push, and merge **only when authorized and the applicable gates pass**.
10. Stop when the requested outcome is proven.

Not every task needs a seal, commit, merge, child agent, or external review. Do not manufacture a release process for a narrow investigation. Equally, do not defer a known-needed review until after the feature has been called sealed.

For OS tools and external APIs, passing synthetic/mock parsing tests alone is not evidence that the constructed command or request works. When feasible, run at least one meaningful command-contract or live integration comparison against the target system.

Do not alternate endlessly between broad investigation and implementation without new evidence.

---

## 17. USAGE-AWARE BEHAVIOR

Usage optimization should influence **how** the model works, not whether it performs required work.

Good optimization:

- narrower searches;
- targeted reads;
- reusing context;
- focused tests;
- proactive, justified platform-native subagents for distinct implementation, debugging, contract checks, regression coverage, and pre-seal review when their value outweighs coordination cost;
- avoiding duplicate verification;
- stopping when sufficient evidence exists.

Bad optimization:

- skipping necessary tests;
- weakening security review;
- skipping migration validation;
- accepting uncertainty silently;
- avoiding required architectural analysis;
- failing to investigate a real regression risk;
- refusing beneficial native delegation solely to minimize child count;
- placing a known-needed independent review **after** sealing rather than before it.

Correctness remains the priority.

## TIME SPENT

Every coding-agent task should report the total elapsed wall-clock time spent on the task when that information is available.

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

Every coding-agent prompt must require a final report containing:

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

- Subagents used: `<0-4>` cumulatively for the whole task, including native and nested spawns.
- Primary model: `<verified model name/ID if available; otherwise unavailable only if an authoritative Path B or C still proves same-model route>`.
- Child model(s): `<verified model name/ID, or same primary route verified by Path B/C>`.
- Purpose and distinct contribution of each child, including any independent pre-seal review.
- Native delegation interface and route verification: `<explicit match | guaranteed native same-model inheritance | verified single-model local backend>`, with the trustworthy evidence.
- Whether any requested/planned pre-seal independent review completed before the seal.
- If verification was unavailable or an explicitly applicable stricter named-model rule could not be met, use zero children and explain briefly.

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

The purpose of this policy is not to make an agent do less work.

The purpose is to make agents do **less unnecessary work**, while allowing their own verified subagents to do useful work.

The preferred task is:

- narrow enough to understand;
- broad enough to be correct;
- verified enough to trust;
- stopped when complete.
