# Stage 2 Verification Findings

Base commit: `889c7cd` (original 11 tests passing, unmodified).
Production code was **not** modified in this stage. All new tests live in
`tests/test_extended_verification.py` and use only fixed, trusted fixture
strings and scripted mock LLM responses — no API calls, no credentials, no
untrusted code executed outside the existing `sandbox_runner.evaluate`
function (same function already used by the unmodified production
evaluator).

## Test run summary

- Baseline (`tests/test_harness.py`): **11 passed, 0 failed** — see
  `verification/copilot_baseline_tests.txt`.
- Extended (`tests/test_extended_verification.py`), final state: **45
  passed, 0 failed** — see `verification/copilot_extended_tests_before.txt`.
- Combined (`tests/`): **56 passed, 0 failed.**

**Honesty note on test-authoring failures:** on the first execution of the
new file, 3 of 48 tests failed. Investigation showed all three failures were
bugs in the *test expectations I wrote*, not in the production code:
- `test_inv2_most_recent_reflection_used_in_revision_prompt` incorrectly
  assumed the first revision prompt would carry no reflection; in fact a
  reflection is generated immediately before every revision call in
  reflection mode, including the first one. Fixed the assertions to match
  the real (and correct) call order.
- `test_inv4_shared_events_attributed_to_each_applicable_arm` and
  `test_inv4_attributed_calls_exceed_actual_distinct_api_calls` passed only
  one scripted revision (`revisions=[GOOD]`) to a `MockLLM` shared by
  **both** `iterate()` calls inside `solve_problem` (testing and
  reflexion arms each consume one revision from the same iterator), causing
  a `StopIteration` on the second arm. Fixed by supplying two revisions and
  correcting the expected distinct-call count to 5 (initial, tests,
  testing-revision, reflexion-reflection, reflexion-revision).

These were genuine authoring mistakes on my part, corrected and re-run
before being reported here, per your instruction to distinguish defective
test expectations from genuine production defects. No test assertions were
weakened to force a pass — the corrected assertions are logically stricter
or equally strict, not looser.

---

## Investigation 1: Potentially meaningless generated tests

**Student concern:** `parse_tests` only checks that a `Call` to
`entry_point` exists somewhere in the assertion's AST, which does not
guarantee the function is actually evaluated at runtime (short-circuit
`or`, or vacuous self-comparison).

**Verification procedure:** `test_inv1_*` in
`tests/test_extended_verification.py`, using `assert True or f(1) == 2`
and `assert f(1) == f(1)` against a trusted, deterministically-wrong fixture
`def f(x): return 999`.

**Actual observed behavior (all confirmed by passing tests):**
1. `parse_tests` **accepts** both `assert True or f(1) == 2` and
   `assert f(1) == f(1)` (`test_inv1_parser_accepts_*`).
2. The incorrect fixture `return 999` **passes** both vacuous assertions
   (`test_inv1_wrong_fixture_passes_*`).
3. The short-circuit assertion **passes even when `f` is never defined at
   all** (`test_inv1_shortcircuit_passes_even_if_entry_point_undefined`) —
   because the right-hand operand of `or` is never evaluated.
4. A meaningful assertion (`assert f(1) == 2`) **correctly rejects** the
   same wrong fixture (`test_inv1_meaningful_assertion_correctly_rejects_wrong_fixture`)
   and correctly reports a `NameError` when `f` is undefined.
5. There is **no explicit, dedicated check** anywhere in `parse_tests` or
   `sandbox_runner.evaluate` that `entry_point` exists and is callable
   before tests run. Correctness is an emergent property of whichever
   assertion happens to be present: with a non-callable `f = 5`, the
   vacuous assertion passes (masked) while the meaningful assertion raises
   `TypeError` (exposed) — both confirmed in
   `test_inv1_no_explicit_entrypoint_existence_or_callability_check`.
6. Per your instruction not to over-generalize: a legitimate two-call
   property test, `assert f(f(1)) == 3` (meaningful for `f(x)=x+1`), is
   correctly accepted by the parser and correctly distinguishes the
   correct fixture from the wrong one
   (`test_inv1_legitimate_multi_call_test_is_not_rejected`). So the defect
   is specifically about **vacuous test shapes** (short-circuit, trivial
   self-comparison), not about "any assertion calling the function twice."

**File/function:** `evaluator.py::parse_tests`; `sandbox_runner.py::evaluate`.

**Status:** **Confirmed defect** (a gap in generated-test reliability), and
simultaneously **a known limitation of any syntactic test-generation
approach** — no static AST check can fully guarantee runtime coverage. Both
framings are accurate; this is not mutually exclusive.

**Impact on experimental correctness:** This directly affects the internal
acceptance → false-acceptance pipeline discussed in Presentation 2. If a
generated test happens to be vacuous, Method B/C could report
`internal_pass=True` for a wrong candidate, inflating the apparent
usefulness of "passing internal tests" and the measured false-acceptance
rate. Because generated tests are LLM output and not under our control at
generation time, this is a real risk for the live experiment, not just a
theoretical one.

**Recommended correction (for your review, not yet applied):** Add a
runtime check in `sandbox_runner.evaluate` (or a post-hoc static check in
`parse_tests`) that rejects assertions whose top-level boolean operator is
`or`/`and` with a short-circuiting literal operand (e.g., `ast.BoolOp` where
one operand is a constant `True`/`False`), and optionally flag (not
necessarily reject) self-comparisons of the exact same call expression on
both sides of `==`. This is a static heuristic improvement, not a full
runtime coverage-tracing solution, and should be presented as a mitigation,
not a complete fix.

---

## Investigation 2: Retry conditions and stop-reason classification

**Verification procedure:** `test_inv2_*`, directly exercising
`agents.iterate` with scripted `MockLLM`/`MockEvaluator` across all 8
requested scenarios.

**Actual observed behavior (all confirmed by passing tests):**
1. `max_attempts=1`, passing tests → `attempts=1`, `stop_reason='internal_pass'`, zero LLM calls.
2. `max_attempts=1`, failing tests → `attempts=1`, `stop_reason='trial_limit'`, zero LLM calls.
3. `max_attempts=1`, no valid tests (`tests=[]`) → **`attempts=1`,
   `stop_reason='trial_limit'`, NOT `'no_valid_tests'`.** This reproduces
   exactly the ambiguity flagged in the technical audit: the code checks
   `attempt == max_attempts` **before** `not tests`, so on the final
   attempt the empty-tests case is indistinguishable, by `stop_reason`
   alone, from a genuine test failure.
4. Success specifically on the 3rd attempt (`revisions=[BAD, GOOD]`) →
   `attempts=3`, `stop_reason='internal_pass'`.
5. Failure on all three attempts → `attempts=3`, `stop_reason='trial_limit'`.
6. `max_attempts=0` and `max_attempts=-1` both raise `ValueError` as designed.
7. No reflection/revision call occurs after the final allowed attempt in
   either reflection or non-reflection mode (verified by exact event-stage
   list equality, not just counts).
8. The revision prompt for attempt *N* contains only the reflection
   generated for the failure at attempt *N−1*, never an earlier one
   (verified with two distinct sentinel reflection tokens).

**File/function:** `agents.py::iterate`, lines implementing the
`attempt == max_attempts` / `not tests` branch order.

**Status:** **Confirmed, reproducible ambiguity** (not a crash, not
incorrect stopping behavior — the loop still stops at the right time; only
the *recorded reason* is imprecise in this one specific overlap case).

**Impact on experimental correctness:** If your paper reports counts of
`stop_reason == 'no_valid_tests'` as "the number of problems where test
generation failed," that count will be an **undercount** specifically for
problems where test generation failed **and** `max_attempts==1`, or more
generally where emptiness is only discovered on the final attempt. For
`max_attempts=3` (your planned default), this only matters if tests are
already known empty from the start — since `not tests` is checked every
iteration, an empty list detected on attempt 1 of a 3-attempt budget will
still correctly report `no_valid_tests` (confirmed implicitly by the
existing `test_empty_tests_not_passed` in `test_harness.py`, which uses
`max_attempts=3`'s default and empty tests, giving `no_valid_tests`). The
ambiguity is therefore narrower than it first appears: it is specifically
an **off-by-last-attempt** interaction, confirmed to occur in the
`max_attempts=1` case and inferred (not yet separately tested) to also
occur whenever the empty-tests condition first becomes relevant exactly on
the final attempt for `max_attempts>1`.

**Recommended correction (for your review, not yet applied):** Reorder the
checks in `agents.iterate` so `if not tests: reason = 'no_valid_tests'` is
evaluated before `if attempt == max_attempts: reason = 'trial_limit'`, or
equivalently, check emptiness once before entering the attempt loop at all
(since tests are never regenerated mid-loop, emptiness is already knowable
up front). I recommend the latter — checking `if not tests` once before the
loop is simpler and removes the ambiguity entirely, since it does not
depend on which attempt discovers it.

---

## Investigation 3: Held-out evaluation isolation

**Verification procedure:** `test_inv3_*`, with a `MockEvaluator` that
records call order, plus a direct `co_names` inspection of `agents.iterate`.

**Actual observed behavior (all confirmed by passing tests):**
1. For a full `solve_problem` run with `max_attempts=3`, every recorded
   `internal` call precedes every recorded `heldout` call — i.e., the first
   `'heldout'` entry in the call log is immediately followed only by more
   `'heldout'` entries; no interleaving occurs
   (`test_inv3_all_candidates_selected_before_any_heldout_call`).
2. `agents.iterate`'s compiled bytecode contains **no reference to the name
   `heldout` at all** (`'heldout' not in iterate.__code__.co_names`),
   which is a structural (not merely empirical) guarantee that no revision
   decision inside the retry loop can depend on a held-out result
   (`test_inv3_heldout_result_cannot_influence_revision_decision`).
3. No LLM prompt logged for any of the three arms contains either sentinel
   value (`REFERENCE_SECRET_V2`, `HELDOUT_SECRET_V2`) placed in
   `canonical_solution`/`test` (`test_inv3_sentinels_excluded_from_every_llm_prompt`).
4. Testing and Reflexion begin from byte-identical initial code and
   identical generated test lists (`test_inv3_testing_and_reflexion_share_initial_candidate_and_tests`).
5. The `testing` arm's logged LLM events never include a `'reflection'`
   stage (`test_inv3_testing_mode_never_calls_reflection_stage`).

**File/function:** `experiment.py::solve_problem`, `agents.py::iterate`,
`dataset.py::public_problem`.

**Status:** **Confirmed correct** — this was the one area the audit already
flagged as structurally sound, and the new tests provide direct executable
evidence (including a bytecode-level guarantee for point 2, which is
stronger than the prior prompt-content-only check).

**Impact on experimental correctness:** None negative — this supports the
validity of your "all candidates frozen before held-out scoring" claim.

**Recommended correction:** None needed.

---

## Investigation 4: Metrics and scientific validity

**Verification procedure:** `test_inv4_*`, using hand-constructed result
rows and `analyze_results.summarize`, plus a direct reproduction of the
completeness-check logic from `analyze_results.main` against real temp
files.

**Actual observed behavior (all confirmed by passing tests):**
1. False acceptance = (internally-accepted candidates that fail held-out) /
   (internally-accepted candidates) — confirmed with a hand-computed 1-of-2
   case yielding exactly `50.0`.
2. A zero-accepted denominator yields `None`, not `0` or `0.0`
   (`test_inv4_zero_accepted_denominator_is_none_not_zero`).
3. Baseline rows (`internal_pass=None`) are naturally excluded from
   `internal_accepted` and report `false_accept_pct_of_accepted=None`
   regardless of held-out outcome.
4. Shared initial-generation and test-generation events are attributed to
   every arm that logically uses them: baseline=1 call, testing=3 calls,
   reflexion=4 calls, for a single successful revision in each iterative arm.
5. The **sum of attributed `calls` across all three arms (8) exceeds the
   actual distinct number of LLM invocations made (5)** — confirmed
   numerically, not just asserted in prose. This is the clearest executable
   evidence that per-arm "calls"/"tokens" in `results.jsonl` must not be
   summed across modes to estimate total real experiment cost.
6. Missing token usage (`input_tokens=output_tokens=0`) does not raise any
   error or warning anywhere in `summarize()` — it silently produces
   `mean_total_tokens=0`. Confirmed present, not yet corrected.
7. Both incomplete (missing rows for a task) and duplicate (repeated
   task/mode pair) result sets are rejected via a `ValueError`, reproducing
   the exact completeness-check logic used in `analyze_results.main`
   against real temporary files.

**File/function:** `analyze_results.py::summarize`, `analyze_results.py::main`,
`experiment.py::solve_problem`, `llm.py::LLM.ask`.

**Status:**
- Items 1–3, 7: **confirmed correct**, matching your intended design.
- Item 4–5 (shared-event attribution and its cross-arm non-additivity):
  **confirmed, intentional design choice** (already documented in your
  README), now additionally protected by a regression test so a future
  refactor cannot silently change this behavior without a test failure.
- Item 6 (silent zero-token fallback): **confirmed gap, not yet corrected**
  — this is a reporting-limitation/defect borderline: the behavior is
  arguably reasonable (zero is an honest placeholder for "unknown"), but
  the absence of any warning means a broken usage API could go unnoticed
  during a real run.

**Impact on experimental correctness:** Items 1–3 and 7 are load-bearing for
your planned metrics section and are now directly verified. Item 5 is
important to state explicitly in your paper's limitations/methods section
so a reviewer does not misread `summary.csv` totals as total experiment
cost. Item 6 is a monitoring gap, not a scoring-correctness gap — it would
not produce an incorrect accuracy result, only a misleadingly low token/cost
figure if it silently occurred during the real run.

**Recommended correction (for your review, not yet applied):** For item 6,
add a single `print`/log warning in `experiment.py`'s main loop (or in
`analyze_results.summarize`) when any event in a run has
`input_tokens == output_tokens == 0`, so you notice immediately during the
pilot run rather than discovering it after the fact.

---

## Investigation 5: Evaluator behavior and error handling

**Verification procedure:** `test_inv5_*`, using
`sandbox_runner.evaluate` directly for in-process fixture checks, and
`unittest.mock.patch` on `evaluator.subprocess.run` / `evaluator.shutil.which`
to simulate Docker return codes and timeouts **without launching any real
container**, per your instruction to defer live Docker testing.

**Actual observed behavior (all confirmed by passing tests):**
1. Correct candidate passes a meaningful test; incorrect candidate fails it.
2. Syntax errors in candidate code are caught and reported with
   `'SyntaxError'` in the feedback string, not raised.
3. Runtime exceptions (`ValueError`) are caught and reported similarly.
4. Candidate/test code that calls `print()` does not corrupt the returned
   structured result — the dict is well-formed and survives a
   `json.dumps`/`json.loads` round-trip exactly as the real subprocess
   boundary requires.
5. An empty internal test suite always returns `passed=False`,
   `status='no_tests'` — the fix documented in the Stage 1 verification
   (`verification/NOTES.md`) is confirmed still in place and unmodified.
6. `parse_tests` rejects: an assertion with no call to `entry_point`, a
   batch containing a non-`Assert` top-level statement, unparseable syntax,
   and an assertion calling a *different* function name — all return `[]`.
7. A missing entry point produces a caught `NameError`-based failure under
   a meaningful test (no misleading success); a non-callable entry point
   (`f = 5`) produces a caught `TypeError`-based failure under a meaningful
   test.
8. **Mocked Docker subprocess scenarios**, with no real container launched:
   - A `subprocess.TimeoutExpired` on `docker run` results in a cleanup
     `docker rm -f` call and a returned `status='timeout'`,
     `passed=False` — confirming the cleanup path executes and the
     method still returns a well-formed `Evaluation` rather than raising.
   - Return codes `125`, `126`, `127` on `docker run` each raise
     `RuntimeError`, confirming these are treated as infrastructure
     failures that should halt the experiment rather than silently
     scoring a candidate as wrong.
   - A nonzero return code **outside** that set (e.g., `1`, modeling an
     in-container crash such as `os._exit(1)`) is instead scored as a
     failed candidate with `status='execution_error'`, not raised.
   - Malformed (non-JSON) stdout with a zero return code is handled
     without crashing, producing `status='execution_error'`.
   - A well-formed mocked JSON response is parsed correctly into an
     `Evaluation` with the expected `passed`/`status`.

**File/function:** `sandbox_runner.py::evaluate`; `evaluator.py::parse_tests`,
`evaluator.py::DockerEvaluator._run`, `DockerEvaluator.internal`.

**Status:** **Confirmed correct** for every scenario tested, including the
previously *unverified* Docker return-code/timeout classification logic
identified as a gap in the original audit (§7, item "no test triggers an
actual Docker crash"). This closes that specific gap using mocks, as
instructed; a live-Docker confirmation is still appropriately deferred to
your separate integration stage, since mocking `subprocess.run` verifies
the *host-side classification logic* but not real Docker daemon behavior,
real resource-limit enforcement, or real timeout timing.

**Impact on experimental correctness:** Positive — this substantially
increases confidence in the evaluator's error-handling correctness ahead of
the real pilot run, without any paid API calls or real container execution.

**Recommended correction:** None needed for the scenarios tested. Live
Docker integration testing (real timeouts, real resource limits, real
daemon error codes) remains a recommended **separate** stage, as you
indicated.

---

## Consolidated recommendations

**P0 — must address before the real benchmark experiment:**
- Investigation 1: decide whether to add the short-circuit/self-comparison
  heuristic guard to `parse_tests`/`evaluate`, since this directly affects
  the false-acceptance metric central to your Presentation 2 narrative. If
  you choose not to fix it, you must explicitly caveat it in the paper as a
  known limitation of generated-test reliability (the data already exists
  to write that paragraph honestly).
- Investigation 2: fix or explicitly document the `stop_reason` ordering
  ambiguity (recommend moving the empty-tests check before the loop, or at
  minimum before the attempt-limit check) before reporting any
  `no_valid_tests` counts in your results.

**P1 — valuable if time permits:**
- Investigation 4, item 6: add a zero-token-usage warning so a broken
  provider usage field is caught during the pilot, not after.
- Add a direct unit test asserting the exact cross-arm `calls` non-additivity
  arithmetic stays stable if `experiment.py` is ever refactored (now
  partially covered by `test_inv4_attributed_calls_exceed_actual_distinct_api_calls`,
  but consider adding one more case with `max_attempts>1` on both arms).

**P2 — optional/documentation only:**
- Reiterate in the README or paper appendix, near `summary.csv`, that
  per-arm attributed call/token counts are not additively comparable to
  real total experiment spend (Investigation 4, confirmed intentional
  design, already partly documented).
- Consider documenting explicitly that live Docker return-code/timeout
  behavior was verified via mocked `subprocess.run`, not a real container,
  pending the separate integration stage.

No production code was modified in this stage. All findings above are
backed by the executable tests in `tests/test_extended_verification.py`
and the raw pytest output saved in `verification/copilot_baseline_tests.txt`
and `verification/copilot_extended_tests_before.txt`.
