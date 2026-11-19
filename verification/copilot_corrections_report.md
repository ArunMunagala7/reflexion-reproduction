# Stage 3 Corrections Report

Base state before this stage: commit `9050f0c` (56/56 tests passing:
11 original + 45 extended verification tests). No production code was
modified before this stage; only tests and documentation were added in
Stage 2.

All four corrections described below were approved by the student based on
the Stage 2 findings (`verification/copilot_verification_findings.md`)
before implementation. No Git commits or pushes were made by the assistant.

---

## Correction 1: Generated-test validation (vacuous assertions)

**1. Original implementation behavior.** `evaluator.py::parse_tests`
accepted any top-level `assert` statement containing, anywhere in its AST,
a `Call` node referencing the entry-point function by name. It did not
check whether that call was actually reachable/executed at runtime.

**2. Student-directed verification question.** Does
`assert True or f(1) == 2` or `assert f(1) == f(1)` get accepted by the
parser, and can an intentionally wrong, deterministic fixture
(`def f(x): return 999`) pass them? The student specifically asked that the
report **not** conflate the two examples: the short-circuit case can pass
even when `f` is undefined (the right operand of `or` is never evaluated),
while the self-comparison case normally still raises `NameError` if `f` is
undefined, because both sides of `==` are evaluated.

**3. Confirmed defect or reporting weakness.** Confirmed defect. Both
assertion shapes were accepted by `parse_tests` and both passed against the
wrong fixture (Stage 2, Investigation 1). This is a real false-acceptance
risk: a deterministically wrong candidate could be scored
`internal_pass=True` purely because of a vacuous generated test, preventing
Reflexion's verbal-reflection/revision mechanism from ever running on that
problem.

**4. Before-correction executable evidence.**
`verification/copilot_regression_before.txt` shows
`test_regr_01_vacuous_constant_shortcircuit_rejected` and
`test_regr_02_identical_expression_selfcompare_rejected` both **FAILED**
against the unmodified implementation, with `parse_tests` returning the
accepted assertion instead of `[]`.

**5. Implemented correction.** Added two conservative, AST-based helper
checks in `evaluator.py`:
- `_is_constant_shortcircuit(node)`: flags an `ast.BoolOp` (`or`/`and`)
  where at least one operand is an `ast.Constant` (e.g., the literal
  `True` in `True or f(1) == 2`). Only fires on literal constants, never on
  names, calls, or other expressions.
- `_is_identical_expression_selfcompare(node)`: flags a single-operator
  `ast.Compare` with `Eq` where `ast.dump(left) == ast.dump(comparator)`,
  i.e., both sides are syntactically identical (e.g., `f(1) == f(1)`).
  Does **not** fire on `f(f(1)) == 3` or `f(1) + f(2) == 5`, since neither
  side is a duplicate of the other.

`_contains_vacuous_pattern` walks the full assertion expression tree
(`ast.walk`) so a vacuous sub-clause nested inside a larger expression is
still caught. `parse_tests` now rejects the entire batch (returns `[]`) if
any assertion matches either pattern, consistent with the existing
invalid-test policy (the whole batch is already rejected for any other
validation failure, e.g., a non-`Assert` top-level node).

The docstring explicitly documents which patterns are rejected and states
that this is a conservative heuristic, not a general semantic-correctness
checker — incomplete or wrong-expected-value generated tests remain an
acknowledged, unaddressed limitation.

**6. After-correction executable evidence.**
`verification/copilot_regression_after.txt` (and the combined run)
shows `test_regr_01_*` and `test_regr_02_*` now **PASS**, along with
`test_regr_03_ordinary_meaningful_assertion_still_accepted`,
`test_regr_04_property_based_different_inputs_not_rejected`, and
`test_regr_05_valid_nested_comparison_not_accidentally_rejected`, confirming
the fix does not over-reject legitimate multi-call or compound assertions.
The reconciled tests in `tests/test_extended_verification.py`
(`test_inv1_parser_rejects_shortcircuit_assertion`,
`test_inv1_parser_rejects_selfcompare_assertion`) also pass.

**7. Remaining limitations.** This heuristic only catches two specific,
obvious vacuous shapes. It does not detect, for example, a generated test
with a plausible-but-wrong expected numeric literal, tautological
conditions expressed differently (e.g., using `1` instead of literal
`True`), or vacuous patterns hidden behind helper function calls. These
remain open limitations of LLM-generated testing in this harness and
should be stated as such in the paper.

**8. Impact on the research experiment.** Reduces (but does not eliminate)
the risk that Methods B/C report false internal acceptance due to
obviously-vacuous generated tests, which directly affects the validity of
the "internal pass" and "false acceptance" metrics central to the
Presentation 2 narrative about Reflexion's dependence on reliable
evaluation feedback. The policy applies identically to Testing and
Reflexion since they share the same generated test suite (Step 7
invariant preserved).

---

## Correction 2: Entry-point existence/callability validation

**1. Original implementation behavior.** `sandbox_runner.py::evaluate`
executed `exec(data['code'], ns)` then directly `exec(test, ns)` with no
check that the candidate module actually defined a callable matching
`entry_point`. Correctness was an emergent property of whichever assertion
happened to be present: a meaningful assertion calling the missing/non
-callable function would raise `NameError`/`TypeError` (caught, scored as
failure), but a vacuous assertion would not exercise it at all and could
pass.

**2. Student-directed verification question.** Is the expected entry point
explicitly checked for existence and callability, independent of whatever
assertion shape happens to be generated?

**3. Confirmed defect or reporting weakness.** Confirmed defect/gap
(Stage 2, Investigation 1, point 5): no explicit check existed.

**4. Before-correction executable evidence.**
`verification/copilot_regression_before.txt` shows
`test_regr_06_missing_entry_point_cannot_pass_internal_evaluation` and
`test_regr_07_noncallable_entry_point_cannot_pass_internal_evaluation`
**FAILED** — both scenarios (missing function, non-callable `f = 5`)
returned `passed=True` when evaluated against a deliberately vacuous test
string passed directly into `evaluate()`.

**5. Implemented correction.** Added `check_entry_point(ns, entry_point)`
inside `sandbox_runner.py::evaluate`, called immediately after executing
the candidate module and **before** executing any assertion, for both
internal and held-out evaluation paths. It returns a descriptive error
string if the entry point is missing from the namespace or is not
`callable(...)`; `evaluate` then reports `passed=False` with that message
without attempting to run the assertion at all. This check runs
unconditionally — for every test in the internal loop, and once before the
held-out `check(candidate)` call — so it cannot be bypassed regardless of
which assertion shape is present. No Docker isolation flags or subprocess
invocation logic were touched; the check executes inside the same
already-sandboxed `evaluate()` function.

**6. After-correction executable evidence.**
`verification/copilot_regression_after.txt` shows both `test_regr_06_*` and
`test_regr_07_*` now **PASS**. The reconciled characterization tests in
`tests/test_extended_verification.py`
(`test_inv1_shortcircuit_no_longer_masks_undefined_entry_point`,
`test_inv1_explicit_entrypoint_existence_and_callability_check_now_present`,
`test_inv1_meaningful_assertion_rejects_undefined_entry_point`,
`test_inv5_noncallable_entry_point_cannot_produce_misleading_success_under_meaningful_test`)
also pass, confirming both the previously-masked vacuous case and the
previously-exposed meaningful case now fail uniformly, and that correct
candidates, syntax errors, and runtime exceptions retain their existing
(already-correct) behavior
(`test_regr_08_valid_callable_entry_point_remains_supported`,
`test_regr_09_empty_suite_cannot_establish_success`,
`test_inv5_syntax_error_reported_correctly`,
`test_inv5_runtime_exception_reported_correctly` all still pass).

**7. Remaining limitations.** This check only verifies existence and
callability, not that the callable has the correct arity/signature; a
candidate defining `f` with the wrong number of parameters would still be
caught, but only via the existing exception-catching path (a `TypeError`
on the call), not by this new check specifically.

**8. Impact on the research experiment.** Strengthens defense-in-depth
against false internal acceptance for the specific missing/non-callable
entry-point case, independent of the Correction 1 test-validation fix,
without weakening Docker isolation (no sandbox flags changed).

---

## Correction 3: Stop-reason classification precedence

**1. Original implementation behavior.** In `agents.py::iterate`, the
branch order checked `if attempt == max_attempts: reason = 'trial_limit'`
**before** `if not tests: reason = 'no_valid_tests'`. With
`max_attempts=1` and an empty test list, this meant `stop_reason` was
reported as `'trial_limit'` even though the true cause was the absence of
valid generated tests.

**2. Student-directed verification question.** With `max_attempts=1` and
no valid internal tests, is the reported stop reason `no_valid_tests` or
`trial_limit`?

**3. Confirmed defect or reporting weakness.** Confirmed defect (reporting
ambiguity, not a correctness/control-flow bug — the loop still stopped at
the right time; only the recorded *reason* was imprecise).

**4. Before-correction executable evidence.**
`verification/copilot_regression_before.txt` shows
`test_regr_10_no_valid_tests_precedes_trial_limit_at_max_attempts_one`
**FAILED**, with `stop_reason == 'trial_limit'` instead of the intended
`'no_valid_tests'`.

**5. Implemented correction.** Reordered the two checks in
`agents.py::iterate` so `if not tests: reason = 'no_valid_tests'` is
evaluated **before** `if attempt == max_attempts: reason = 'trial_limit'`.
Precedence is now, in order: `internal_pass` → `no_valid_tests` →
`trial_limit`, exactly matching the student's specified intent. No
additional LLM calls are introduced by this reordering — both branches
already `break` out of the loop without calling the LLM, so this is a
pure reclassification of an existing early-exit path. The candidate-attempt
count (`len(history)`) and the latest-reflection-only memory policy are
unaffected, since neither branch was changed with respect to LLM calls or
history recording.

**6. After-correction executable evidence.**
`verification/copilot_regression_after.txt` shows `test_regr_10_*` now
**PASSES**, along with the reconciled
`test_inv2_max_attempts_one_no_valid_tests` in
`tests/test_extended_verification.py`. All other Investigation 2
regression/characterization tests covering `max_attempts=1` pass/fail
cases, 3rd-attempt success, all-attempts failure, `max_attempts<1`
validation, no-call-after-final-attempt (both modes), and latest-reflection
usage continue to pass unmodified, confirming the reordering did not
disturb any other stop-reason or control-flow behavior.

**7. Remaining limitations.** None identified for this specific
reclassification; it is a narrow, well-tested change.

**8. Impact on the research experiment.** Any paper claims based on
`stop_reason == 'no_valid_tests'` counts (e.g., "test generation failed
for N problems") will now be accurate even for the edge case where
emptiness is discovered on the final allowed attempt.

---

## Correction 4: Missing token-usage reporting

**1. Original implementation behavior.** `llm.py::LLM.ask` always recorded
`input_tokens`/`output_tokens` as `0` when the provider's response object
had no `usage` block (`u = result.usage; ... u.prompt_tokens if u else 0`).
This was indistinguishable from a genuinely-measured zero-token response.
`analyze_results.py::summarize` then computed `mean_total_tokens` over all
rows unconditionally, silently treating any such gap as zero cost.

**2. Student-directed verification question.** Is missing token usage
explicitly identifiable as unavailable, or silently represented as a
measured zero?

**3. Confirmed defect or reporting weakness.** Confirmed reporting
weakness (not a scoring-correctness bug — it does not affect pass/fail
determination, only cost/token reporting fidelity).

**4. Before-correction executable evidence.**
`verification/copilot_regression_before.txt` shows
`test_regr_11_missing_token_usage_marked_unavailable_not_zero` **FAILED**:
with a faked OpenAI client returning `usage=None`, the logged event
recorded `input_tokens=0, output_tokens=0` with no availability indicator
at all (`event.get('tokens_available', 'MISSING_KEY')` returned the
sentinel, confirming the key did not exist).

**5. Implemented correction.**
- `llm.py`: `Response` gained a `tokens_available: bool = True` field.
  `ask()` now computes `tokens_available = u is not None`, stores `None`
  (not `0`) for `input_tokens`/`output_tokens` when usage is unavailable,
  includes `tokens_available` in the logged event dict, and prints an
  explicit warning (`WARNING: provider returned no token usage data for
  stage=...`) when this occurs, so the condition is visible during a live
  run rather than discovered only afterward.
- `experiment.py::stats`: now computes sums only over events where
  `tokens_available` is true (or absent, for backward compatibility with
  any pre-existing event dicts), sets `input_tokens`/`output_tokens` to
  `None` if *no* event in the arm has usage data, and reports an
  arm-level `tokens_available` flag that is `False` if *any* event in
  that arm is missing usage (so a partial total is still flagged as
  incomplete, not silently presented as complete).
- `analyze_results.py::summarize`: computes `mean_total_tokens` only over
  rows with available, non-`None` token counts; reports it as `None`
  (not `0.0`) if no row in a mode has usable data; adds a
  `rows_missing_token_usage` count per mode; and prints a warning when any
  row in a mode is missing usage. The `tokens.png` plot now labels any
  mode with unavailable usage as `"unavailable"` on its bar rather than
  plotting a misleading zero-height bar.
- The existing shared-event cross-arm attribution logic (baseline/testing/
  reflexion sharing the initial-generation and test-generation events) is
  unchanged; this correction only changes *how missing data within that
  attribution* is represented, not the attribution logic itself.

**6. After-correction executable evidence.**
`verification/copilot_regression_after.txt` shows
`test_regr_11_missing_token_usage_marked_unavailable_not_zero` now
**PASSES**. The existing
`test_inv4_missing_token_usage_represented_as_zero_no_warning_raised` in
`tests/test_extended_verification.py` was intentionally **left unchanged**
(not reconciled) because it exercises a different input shape — a
hand-constructed row dict with an explicit `input_tokens=0` and no
`tokens_available` key at all, simulating a legacy/pre-correction result
file. `analyze_results.summarize`'s backward-compatible default
(`x.get('tokens_available', True)`) correctly still treats such a legacy
row as a measured zero, so this test continues to pass and now
additionally documents the backward-compatibility behavior for
historical result files, which is intentional, not an oversight.

**7. Remaining limitations.** This correction only distinguishes
"unavailable" from "zero" at the per-event and per-arm/per-mode reporting
level; it does not retroactively recover token counts for a provider that
truly never returns usage data, and `rows_missing_token_usage` must still
be manually checked by the student before interpreting `mean_total_tokens`
in the final paper.

**8. Impact on the research experiment.** Prevents a silent, misleadingly
low or zero token/cost figure from being reported during the real
benchmark run if a provider fails to return usage data; surfaces the
condition immediately via a printed warning during the run itself.

---

## Step 7 invariant check (after all four corrections)

All of the following were re-confirmed by the full 67-test suite in
`verification/copilot_regression_after.txt`:

- Three experimental methods (baseline/testing/reflexion) unchanged in
  structure — `experiment.py::solve_problem` orchestration logic untouched.
- Same underlying LLM abstraction (`llm.py::LLM`); only its token-usage
  bookkeeping changed, not its request logic or model selection.
- Shared initial candidate and shared generated tests across arms —
  untouched (`test_inv3_testing_and_reflexion_share_initial_candidate_and_tests`
  still passes).
- Equal maximum candidate-attempt budgets for Testing/Reflexion — untouched
  (`max_attempts` parameter and loop bound unchanged).
- Reflection exclusively in the Reflexion method —
  `test_inv3_testing_mode_never_calls_reflection_stage` and
  `test_inv2_no_unnecessary_call_after_final_attempt_testing_mode` still
  pass.
- Latest-reflection-only memory policy — `last_reflection` overwrite logic
  in `agents.py::iterate` untouched;
  `test_inv2_most_recent_reflection_used_in_revision_prompt` still passes.
- No official benchmark tests/reference solutions in LLM prompts —
  `public_problem()` whitelist untouched;
  `test_inv3_sentinels_excluded_from_every_llm_prompt` still passes.
- All final candidates frozen before official scoring —
  `experiment.py::solve_problem`'s `groups` dict construction (all arms
  resolved before any `evaluator.heldout` call) is untouched;
  `test_inv3_all_candidates_selected_before_any_heldout_call` and
  `test_inv3_heldout_result_cannot_influence_revision_decision` still pass.
- No independent trusted verifier introduced into the revision loop — the
  new entry-point check in `sandbox_runner.py` runs only inside the
  existing sandboxed `evaluate()` function using the same
  internally-generated tests; it does not consult the official benchmark
  reference solution or tests at any point, and does not add any new
  external verification source.
- Test-validation policy applies equally to both iterative methods — both
  Testing and Reflexion call the same shared `tests` list produced once by
  `agents.generate_tests`/`evaluator.parse_tests`; the vacuous-assertion
  rejection happens once, upstream of both arms.

---

## Reevaluation summary

- `verification/copilot_regression_before.txt`: regression tests run
  against the **unmodified** implementation — **6 failed, 5 passed** (of
  11 total; one additional test-authoring mistake on an unrelated nested
  -parenthesis expectation was caught and fixed before this official
  capture, documented inline in `tests/test_regressions.py`'s
  `test_regr_05` docstring).
- `verification/copilot_regression_after.txt`: full combined suite
  (`tests/test_harness.py` + `tests/test_extended_verification.py` +
  `tests/test_regressions.py`) run after all four corrections and the
  Step 8 reconciliation — **67 passed, 0 failed**.
  - 11 original harness tests: unchanged, still passing.
  - 45 extended verification tests: 7 were reconciled (updated to assert
    the corrected behavior, each with an inline docstring explaining the
    original expectation, why it represented old behavior, and the new
    expectation) and now pass; the remaining 38 were already correct
    characterizations and are unchanged.
  - 11 new regression tests: all now pass, having failed appropriately
    (6 of them) before the corresponding corrections were implemented.

No test was silently removed, weakened, or had its assertion loosened to
force a pass. Every reconciled test's docstring documents the original
(pre-correction) expectation alongside the new one, preserving the
original verification evidence in prose even though the assertion itself
changed.

---

## Final Git diff summary

Working tree status at the time of this report (not yet committed by the
assistant, per instruction):

```
 M README.md
 M agents.py
 M analyze_results.py
 M evaluator.py
 M experiment.py
 M llm.py
 M sandbox_runner.py
 M tests/test_extended_verification.py
?? tests/test_regressions.py
?? verification/copilot_regression_after.txt
?? verification/copilot_regression_before.txt
?? verification/copilot_corrections_report.md
```

**Production files modified:**
- `agents.py` — stop-reason precedence reordering (Correction 3).
- `evaluator.py` — vacuous-assertion rejection in `parse_tests`
  (Correction 1).
- `sandbox_runner.py` — explicit entry-point existence/callability check
  (Correction 2).
- `llm.py` — `tokens_available` field and unavailable-usage warning
  (Correction 4).
- `experiment.py` — `stats()` now distinguishes unavailable vs. zero
  tokens per arm (Correction 4).
- `analyze_results.py` — `summarize()` and the tokens plot now distinguish
  unavailable vs. zero tokens per mode (Correction 4).

**Documentation modified:**
- `README.md` — documents the revised test-validation policy, entry-point
  validation, stop-reason classification, token-usage handling, and
  remaining limitations.

**Test files modified/added:**
- `tests/test_extended_verification.py` — 7 characterization tests
  reconciled to assert corrected behavior (Step 8); 38 tests unchanged.
- `tests/test_regressions.py` — new, 11 regression tests (Step 2).

**Verification evidence files added:**
- `verification/copilot_regression_before.txt`
- `verification/copilot_regression_after.txt`
- `verification/copilot_corrections_report.md` (this file)

No files from Stage 1 or Stage 2 (`verification/NOTES.md`,
`verification/initial_tests.txt`, `verification/revised_tests.txt`,
`verification/copilot_baseline_tests.txt`,
`verification/copilot_extended_tests_before.txt`,
`verification/copilot_verification_findings.md`,
`verification/copilot_first_test_run.txt`) were modified or overwritten.
