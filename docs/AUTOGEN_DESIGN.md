# AutoGen Design Document (Path 2: Multi-Agent Proactive Code Review)

This document explains the Path 2 pivot, the academic reasoning behind
it, the exact design of Method D (AutoGen proactive review), and the
experimental controls that keep it comparable to the preserved Path 1
(Reflexion reproduction) work. It is written to stand alone, but it
assumes the reader has already seen the main [README](../README.md)
and, for implementation detail, [docs/IMPLEMENTATION_GUIDE.md](IMPLEMENTATION_GUIDE.md).

**The Reflexion reproduction (Methods A/B/C) described in the main
README is unchanged and fully preserved.** This document only adds
Method D and the infrastructure needed to run it alongside the
existing three methods.

---

## 1. Why we changed from Path 1 to Path 2

Path 1 (small-scale Reflexion reproduction) is complete through
verification: three methods implemented, audited, corrected (4 verified
defects), and covered by 67 passing tests (see git history `889c7cd` →
`9050f0c` → `067de57`). That work is preserved exactly as-is on the
`path1-reflexion-reproduction` branch and is **reused, not discarded**,
as the experimental baseline for Path 2.

Our third presentation is on AutoGen, a multi-agent orchestration
framework. Rather than treat Presentation 3 as an unrelated topic, Path
2 asks a research question that depends directly on the
already-verified Reflexion implementation: **does proactively reviewing
every candidate (not just failing ones) catch mistakes that
failure-triggered reflection never gets a chance to see?** This lets us
build on, rather than replace, our prior work, and gives the three
presentations a single connected research arc (see Section 2).

## 2. The connection between Toolformer, Reflexion, and AutoGen

- **Toolformer** (Presentation 1): a single LLM learns, through
  self-supervised fine-tuning, when and how to invoke external tools
  (e.g., a calculator or search API) to improve its own output. The
  locus of improvement is the model itself gaining a new capability.
- **Reflexion** (Presentation 2): a single LLM agent improves across
  attempts at the *same* task by evaluating its own output against
  feedback (e.g., failing tests) and generating an explicit verbal
  self-critique ("reflection") that is fed into the next attempt. The
  locus of improvement is iterative self-correction driven by
  failure signals.
- **AutoGen** (Presentation 3): instead of one agent reflecting on
  itself, multiple specialized agents with distinct roles and
  system prompts converse to produce a result, via a general-purpose
  orchestration framework (`autogen-agentchat`). The locus of
  improvement is role separation and structured inter-agent
  communication, which can surface a *second, differently-biased
  opinion* that a single self-reflecting agent cannot produce on
  itself.

The throughline: **Toolformer** extends what a single model can do;
**Reflexion** extends how a single agent can learn from its own
failures across attempts; **AutoGen** extends who participates in
producing and judging an attempt. Path 2 operationalizes exactly this
last step by asking whether giving the Reflexion workflow a second,
independent, proactive reviewer role (an AutoGen-style role split)
catches anything the original failure-triggered, single-agent
reflection loop misses.

## 3. The specific limitation of our Reflexion implementation

Looking at `agents.iterate` (preserved, unchanged): a reflection is only
ever generated when `not evaluation.passed` -- i.e., only after the
*generated* internal tests fail. If the generated tests are incomplete
(a known, explicitly documented limitation; see the main README's
verification history and `evaluator.parse_tests`'s docstring), a
candidate can pass all internal tests while still being wrong, and
**no reflection is ever produced for it**, because the loop stops
immediately (`stop_reason='internal_pass'`). This is not a defect to
correct in Path 1 (it is a faithful property of the Reflexion
algorithm as described in the paper); it is a testable limitation that
motivates a differently-structured Method D.

## 4. The original AutoGen A4 motivation, and our adaptation

The AutoGen paper's application A4 ("Dynamic Group Chat") includes a
Writer/Safeguard role split in which a Safeguard agent reviews content
for **safety** (e.g., harmful content), independent of whether the
Writer considers its own output finished. We borrow the structural idea
-- a second agent that reviews proactively, not only on explicit
failure -- but our Reviewer checks **functional correctness and missed
edge cases on HumanEval programming problems**, not safety. This is
explicitly a small-scale adaptation of AutoGen's role-separation
mechanism for a different purpose, not a reproduction of the original
A4 safety experiment, and we do not claim the AutoGen paper itself
demonstrates better HumanEval accuracy than Reflexion -- that is
precisely the open question Path 2 investigates empirically.

## 5. The four experimental methods

| Method | File | Candidate attempts | Reflection/Review | Acceptance condition |
|---|---|---|---|---|
| A. Baseline | `agents.py::initial_code` | 1 (fixed) | none | n/a (always reported, no accept/reject concept) |
| B. Testing | `agents.py::iterate(use_reflection=False)` | up to 3 | none | internal tests pass |
| C. Reflexion | `agents.py::iterate(use_reflection=True)` | up to 3 | verbal reflection **only after internal-test failure** | internal tests pass |
| D. AutoGen review | `autogen_agents.py::run_autogen_review` | up to 3 | Reviewer examines **every** candidate, including passing ones | internal tests pass **and** reviewer validly approves |

All four methods receive the **same shared initial candidate**
(`agents.initial_code`, called once per problem). Methods B, C, and D
receive the **same shared generated tests** (`agents.generate_tests`,
called once per problem). See `experiment.solve_problem`.

## 6. AutoGen architecture

### 6.1 Library and API choice

We use the modern **AgentChat** API from `autogen-agentchat==0.7.5`
(pinned; see `requirements.txt`), specifically:

- `autogen_agentchat.agents.BaseChatAgent` -- the abstract base class
  both our agents subclass.
- `autogen_agentchat.messages.TextMessage` -- the concrete message type
  exchanged between the controller and each agent.
- `autogen_agentchat.base.Response` -- the wrapper each agent returns
  from `on_messages`.
- `autogen_core.CancellationToken` -- required by the `on_messages`
  signature; we construct a fresh one per call (no cross-call
  cancellation is needed for this synchronous, sequential workflow).

We deliberately did **not** use `AssistantAgent` (AutoGen's built-in
agent that owns its own model-client object and internal message
history) because it would require re-implementing our existing
`LLM.ask`-based prompt/response logging, token accounting, and mocked
test infrastructure a second time inside an AutoGen-native model-client
adapter. Instead, `ProgrammerAgent` and `ReviewerAgent` are small custom
`BaseChatAgent` subclasses whose `on_messages` implementation calls
`LLM.ask(stage, prompt)` directly -- so every Method D inference call
is logged through exactly the same `llm.events` mechanism, with the
same token-usage-availability tracking, as Methods A/B/C. We did not
install AutoGen Studio, and we did not mix in the legacy AutoGen v0.2
(`pyautogen`) API.

### 6.2 What AutoGen provides vs. what our harness controls

See the module docstring in `autogen_agents.py` for the full
explanation; in short:

- **AutoGen provides:** the `BaseChatAgent` contract, `TextMessage`,
  `Response`, `CancellationToken` -- i.e., the typed agent/message
  abstractions themselves.
- **Our harness provides:** when to call each agent (the proactive
  review loop), the candidate-attempt budget, what context each agent
  receives, the Reviewer response parser and its malformed-response
  policy, and the single code executor (`DockerEvaluator`, unchanged).

### 6.3 Agent roles

- **Programmer Agent** (`ProgrammerAgent`): generates the first-revision
  and all subsequent revisions. Receives the public problem
  specification, the previous candidate, the shared generated tests,
  the internal test outcome/feedback, and the latest reviewer decision
  and criticism. Returns a complete runnable Python module (reusing
  `evaluator.strip_fence`, exactly like `agents.py`).
- **Reviewer Agent** (`ReviewerAgent`): examines the candidate, public
  specification, shared tests, and internal test outcome -- **even when
  internal tests pass**. Returns a strict JSON response
  (`{"decision": "APPROVE"|"REVISE", "feedback": "..."}`).
- **Executor**: the existing, unmodified `evaluator.DockerEvaluator`.
  It is not an LLM and never changes between methods.
- **Controller**: `experiment.solve_problem` (unchanged dispatch logic)
  plus `autogen_agents.run_autogen_review` (the new orchestration
  function), which enforces the attempt budget and stopping policy.

## 7. Agent message exchange

For each candidate attempt (up to the shared max-attempts budget):

1. Controller calls `DockerEvaluator.internal(...)` directly (no agent
   involved -- the executor is deterministic and non-LLM).
2. If there are no valid generated tests, the controller stops
   immediately with `stop_reason='no_valid_tests'`, exactly mirroring
   Methods B/C -- **no agent is invoked and no false acceptance is
   possible.**
3. Otherwise, the controller sends a single `TextMessage` (role
   `'controller'`) to the Reviewer via `reviewer.on_messages([...],
   CancellationToken())`. The Reviewer returns a `Response` whose
   `chat_message` is a `TextMessage` containing its JSON decision.
4. The controller parses that response with `parse_review_response`
   (strict: anything other than well-formed `{"decision": "APPROVE"|
   "REVISE", "feedback": "<string>"}` is marked `valid=False` and is
   never treated as approval).
5. If internal tests passed **and** the review is valid and
   `APPROVE`, the controller stops: `stop_reason='accepted'`,
   `workflow_accept=True`.
6. Otherwise, if another attempt is available, the controller sends a
   second `TextMessage` to the Programmer, including the test
   feedback and the Reviewer's decision/criticism (or an explicit note
   that the review was invalid). The Programmer's `Response` becomes
   the next candidate.
7. If the attempt budget is exhausted, the controller stops with
   `stop_reason='trial_limit'` and returns the last candidate,
   regardless of its approval status (`workflow_accept` can only be
   `True` via step 5).

Every message exchanged is captured in the row's `history[i]['messages']`
list (controller→reviewer prompt, reviewer→controller response,
controller→programmer prompt, programmer→controller response) for full
auditability, in addition to the standard `llm.events` log (stage
labels `autogen_reviewer` / `autogen_programmer`).

## 8. Algorithm and termination policy

```
candidate = shared_initial_code
for attempt in 1..max_attempts:
    internal_result = DockerEvaluator.internal(candidate, shared_tests, entry_point)
    if shared_tests is empty:
        stop(reason='no_valid_tests', workflow_accept=False)
    review = Reviewer.on_messages(reviewer_prompt(candidate, internal_result))
    parsed = parse_review_response(review.content)     # never defaults to APPROVE
    if internal_result.passed and parsed.valid and parsed.decision == 'APPROVE':
        stop(reason='accepted', workflow_accept=True)
    if attempt == max_attempts:
        stop(reason='trial_limit', workflow_accept=False)
    candidate = Programmer.on_messages(programmer_prompt(candidate, internal_result, parsed))
evaluate candidate with DockerEvaluator.heldout(...)   # only after the loop above has fully stopped
```

A **candidate attempt** means one generated solution (one Programmer
call that produces code), not every message or Reviewer call --
`review_calls` is tracked as a separate counter from `attempts`,
because every non-`no_valid_tests` attempt triggers exactly one
Reviewer call, but only `attempts - 1` Programmer calls (the initial
candidate is shared, not Programmer-generated).

## 9. A simple illustrative example

*(Hypothetical, for explanation only -- not an actual logged run.)*

Problem: implement `is_balanced(s)`, returning whether parentheses in
`s` are balanced.

- **Attempt 1 candidate:** handles `()`, `(())`, but not an empty
  string correctly (returns `False` for `""` instead of `True`).
- **Generated tests:** `assert is_balanced("()") == True`,
  `assert is_balanced("(()") == False` -- neither test happens to cover
  the empty-string case, so **internal tests pass**.
- Under Method C (Reflexion): internal tests passed, so the loop stops
  immediately at `stop_reason='internal_pass'`. No reflection is ever
  generated. The empty-string bug ships to held-out scoring unexamined.
- Under Method D (AutoGen): the Reviewer is invoked anyway (tests
  passed, but review still happens), and might respond
  `{"decision": "REVISE", "feedback": "The function does not handle
  the empty string; an empty string has no unmatched brackets and
  should be considered balanced."}`. The Programmer then revises using
  that criticism, and a second attempt is evaluated.

This illustrates the exact mechanism under test -- it is **not**
guaranteed that the Reviewer always catches such gaps (it is an LLM
critic, not an oracle), and the Reviewer can also be wrong, request
unnecessary revisions, or introduce a regression into a candidate that
was already correct. Both outcomes are measured, not assumed.

## 10. Experimental controls

Preserved exactly across all four methods:
- Same underlying model (one `LLM` instance, one `--model` value).
- Same benchmark tasks per run (`dataset.select`, same seed).
- Same shared initial candidate (A/B/C/D).
- Same shared generated tests (B/C/D).
- Same maximum candidate-attempt budget, 3 by default (B/C/D).
- Same `DockerEvaluator` instance and Docker image for every method.
- Same official held-out scoring procedure (`DockerEvaluator.heldout`),
  invoked only after every method's final candidate is frozen.
- Same disjoint pilot problems (`HumanEval/0`, `HumanEval/1`), excluded
  from the main sample.
- Same deterministic, seeded benchmark selection (`dataset.select`).

**Not equal, and not claimed to be equal:** inference cost. Method D
makes one additional Reviewer call per attempt compared to Method C's
reflection call, and Method C's reflection call only happens on
failure, whereas Method D's Reviewer call happens on every attempt
(including the first, even if it passes). `experiment.stats` /
`analyze_results.summarize` report `mean_calls` and `mean_total_tokens`
per method precisely so this cost difference is visible, not hidden.
Our comparison evaluates the complete proactive-review workflow --
including its different stopping policy and inference budget -- not
the isolated effect of merely having the `autogen-agentchat` package
installed.

## 11. Metrics

Three distinct outcomes are tracked per candidate, never conflated:

1. **Internal-test pass** (`internal_pass`): did the shared generated
   tests pass on the final candidate.
2. **Workflow acceptance** (`workflow_accept`): did the *experimental
   workflow itself* consider the candidate finished.
   - Testing/Reflexion: identical to internal-test pass (unchanged
     from Path 1).
   - AutoGen: internal-test pass **and** a valid Reviewer `APPROVE`.
     A candidate returned only because the attempt budget ran out is
     **not** workflow-accepted, even if its internal tests happened to
     pass on that final attempt.
3. **Official held-out pass** (`heldout_pass`): the real HumanEval
   `check(candidate)` function, exactly as before.

Two false-acceptance rates are reported (see
`analyze_results.summarize`):

- **Test false acceptance** (`false_accept_pct_of_accepted`): of
  candidates with `internal_pass=True`, the percentage that fail
  official evaluation. Comparable across all three iterative methods.
- **Workflow false acceptance**
  (`workflow_false_accept_pct_of_workflow_accepted`): of candidates
  with `workflow_accept=True`, the percentage that fail official
  evaluation. For Testing/Reflexion this is identical to test false
  acceptance by construction. For AutoGen it can differ, because its
  acceptance denominator is smaller and conditioned on reviewer
  approval.

A zero denominator in either measure is reported as `None`
(unavailable), never `0`, exactly matching the Path 1 policy
(`verification/copilot_corrections_report.md`, correction D). Baseline
rows have no accept/reject concept and are excluded from both measures,
unchanged from Path 1.

## 12. Additional computation required by review

Each AutoGen candidate attempt (beyond the shared initial candidate and
shared generated tests) costs exactly one Reviewer call, plus one
Programmer call only if a revision is actually triggered. In the worst
case (3 attempts, every attempt reviewed and revised), Method D issues
up to 3 Reviewer calls + 2 Programmer calls = 5 additional LLM calls,
versus Method C's worst case of 2 reflection calls + 2 revision calls =
4 additional LLM calls for the same 3-attempt budget. The exact,
measured difference per real run will be reported in `mean_calls` /
`mean_total_tokens` once real experiments are executed (not yet run;
see Section 13).

## 13. Limitations

- The Reviewer is an LLM critic, not a trusted correctness oracle: it
  can be wrong, request unnecessary revisions, or fail to catch a real
  defect. Both directions of error are measured, not assumed away.
- A malformed Reviewer response is treated as "no usable review this
  attempt" (see `parse_review_response`), which costs an extra
  revision opportunity rather than silently defaulting to approval --
  this is a deliberate conservative choice, not a proof that malformed
  responses never occur in practice with real models.
- `agents.generate_tests`'s known limitation (LLM-generated tests can
  be syntactically valid but semantically incomplete) applies to Method
  D exactly as it does to Methods B/C; Method D's Reviewer is one
  mitigation under test for this limitation, not a guaranteed fix.
- No real Docker integration test or real LLM benchmark run has been
  executed for Method D yet (see Section 14); all verification so far
  uses scripted mock LLM responses and the existing Docker-shaped
  `Evaluation` dataclass directly, per the documented constraints for
  this phase of work.
- We do not claim the original AutoGen paper demonstrates better
  HumanEval performance than Reflexion -- Section 4 is explicit that
  this is an untested, small-scale adaptation, and the actual
  comparison is this project's own open empirical question.

## 14. Current status and readiness for Docker integration testing

- Implementation: complete (`autogen_agents.py`, `experiment.py`,
  `analyze_results.py` updated; backward-compatible `--include-autogen`
  flag; `config.json` now records the selected `methods` list).
- Software verification: complete using mocked LLM responses and real
  `BaseChatAgent`/`TextMessage`/`Response` objects
  (`tests/test_autogen_agents.py`); full suite including the preserved
  Path 1 tests: **83/83 passing** (see
  `verification/copilot_autogen_tests.txt`).
- **Not yet done:** running `experiment.py --include-autogen --pilot`
  against the real `python:3.11-slim` Docker image and a real,
  configured LLM endpoint. The codebase appears ready for this next
  step (the `DockerEvaluator` code path for Method D is identical to
  Methods B/C -- it calls the same `internal`/`heldout` methods with
  the same argument shapes), but this has not been empirically
  confirmed and no real benchmark numbers exist yet.
