# Pilot Findings Report: Path 2 AutoGen Pilot (HumanEval/0 and HumanEval/1)

Documentation only. No production code, tests, pilot results, or generation policy were changed. No experiment was rerun and no paid API calls were made. All numbers come from the saved files listed in "Evidence files".

## 1. Experimental configuration

From `results/pilot_autogen/config.json`:

| Setting | Value |
|---|---|
| Command | `python experiment.py --pilot --include-autogen --max-attempts 3 --model gpt-4o-mini --out results/pilot_autogen` |
| Model / temperature / seed | `gpt-4o-mini` / 0.0 / 42 |
| Tasks | HumanEval/0, HumanEval/1 |
| Methods | baseline, testing, reflexion, autogen |
| Max attempts | 3 |
| Shared initial candidate and shared generated tests | yes (`shared_initial_and_tests: true`) |
| Memory | latest reflection only |
| Sandbox | `python:3.11-slim`, Docker |
| Dataset SHA-256 | `b796127e635a67f93fb35c04f4cb03cf06f38c8072ee7cee8833d7bee06979ef` |

Results: 8 rows in `results/pilot_autogen/results.jsonl` (2 tasks × 4 methods).

## 2. Summary table of the four methods

Values from `results/pilot_autogen/summary.csv`, with per-task values from `results.jsonl`.

| Method | Official held-out | Mean attempts | Mean calls | Internal accepted | Workflow accepted | Mean total tokens | Mean solve seconds |
|---|---|---|---|---|---|---|---|
| Baseline (A) | 2/2 | 1 | 1 | n/a | n/a | 369 | 3.16 |
| Testing (B) | 2/2 | 3 | 4 | 0 | 0 | 2206 | 11.5 |
| Reflexion (C) | 2/2 | 3 | 6 | 0 | 0 | 3653 | 14.7 |
| AutoGen (D) | 2/2 | 3 | 7 | 0 | 0 | 5282.5 | 18.29 |

Per task (input/output tokens; solve seconds):

| Task | Method | Calls | Input / output tokens | Solve s |
|---|---|---|---|---|
| HumanEval/0 | Baseline | 1 | 160 / 178 | 3.08 |
| HumanEval/0 | Testing | 4 | 1501 / 732 | 12.06 |
| HumanEval/0 | Reflexion | 6 | 2834 / 887 | 15.76 |
| HumanEval/0 | AutoGen | 7 | 4389 / 1455 | 19.56 |
| HumanEval/1 | Baseline | 1 | 150 / 250 | 3.23 |
| HumanEval/1 | Testing | 4 | 1335 / 844 | 10.93 |
| HumanEval/1 | Reflexion | 6 | 2553 / 1032 | 13.65 |
| HumanEval/1 | AutoGen | 7 | 3521 / 1200 | 17.02 |

The `mean_total_tokens` values match the per-task sums (for example, AutoGen: (4389+1455+3521+1200)/2 = 5282.5). `rows_missing_token_usage` is 0 for every method.

Call counts match the stage structure. Testing: 1 initial + 1 test generation + 2 revisions = 4. Reflexion: 1 + 1 + 2 reflections + 2 revisions = 6. AutoGen: 1 + 1 + 3 reviewer calls + 2 programmer calls = 7.

## 3. Incorrect generated assertions

### 3.1 HumanEval/0 (`has_close_elements`)

The public docstring says the function returns whether any two numbers are "closer to each other than given threshold". "Closer than" means strict less-than. The public examples (`[1.0, 2.0, 3.0]`, 0.5 → False; `[1.0, 2.8, 3.0, 4.0, 5.0, 2.0]`, 0.3 → True) do not test the boundary case. The strict reading rests on the wording, not on a public example. The user's interpretation is consistent with the wording.

Under strict less-than:

- `has_close_elements([1.0, 2.0, 3.0, 4.0], 1.0)`: the closest pair is 1.0 apart. 1.0 is not less than 1.0, so the answer is **False**. The generated assertion expects **True**. Incorrect.
- `has_close_elements([1.0, 2.0, 3.0, 4.0], 5.0)`: 1.0 < 5.0, so the answer is **True**. The generated assertion expects **False**. Incorrect.

The other four generated assertions are consistent with the public wording:

- `has_close_elements([1.0, 2.0, 3.0], 0.5) == False`: the closest pair is 1.0 apart, which is not less than 0.5. Correct.
- `has_close_elements([1.0, 2.8, 3.0, 4.0, 5.0, 2.0], 0.3) == True`: 2.8 and 3.0 are 0.2 apart. Correct.
- `has_close_elements([], 0.1) == False`: correct.
- `has_close_elements([1.0, 1.0], 0.1) == True`: correct.

Two of six generated assertions are wrong.

### 3.2 HumanEval/1 (`separate_paren_groups`)

The public docstring says: "Ignore any spaces in the input string", with the example `'( ) (( )) (( )( ))'` → `['()', '(())', '(()())']`. Spaces are ignored, so `'(( ) ( ))'` becomes `(()())`. This is one balanced outer group, so the correct output is `['(()())']`.

The generated assertion `separate_paren_groups('(( ) ( ))') == ['()', '()']` is **incorrect**.

The other five generated assertions are consistent with the spec:

- `'( ) (( )) (( )( ))'` → `['()', '(())', '(()())']` (from the public docstring)
- `'()'` → `['()']`
- `'((()))'` → `['((()))']`
- `'() ()'` → `['()', '()']`
- `''` → `[]`

One of six generated assertions is wrong.

## 4. Evidence from the saved candidate histories

### 4.1 Initial candidate

The baseline row is the shared initial candidate. Its official held-out result was **pass** for both tasks (`heldout_pass: True`, status `evaluated`).

For HumanEval/0, the initial candidate fails the two incorrect assertions. The saved feedback names both (`1.0 ... == True` and `5.0 ... == False`). This is consistent with the candidate's `< threshold` check.

For HumanEval/1, the initial candidate fails the incorrect assertion only (`'(( ) ( ))' == ['()', '()']`).

Note: the initial candidate's official pass is the baseline row's held-out result. It is saved for the final candidate only, not for intermediate attempts.

### 4.2 Candidates generated per method

| Method | Candidates per task | Final-candidate source |
|---|---|---|
| Baseline | 1 | initial |
| Testing | 3 | attempt 3 |
| Reflexion | 3 | attempt 3 |
| AutoGen | 3 | attempt 3 |

### 4.3 Internal-test results

Testing, Reflexion, and AutoGen all ended with `internal_pass: False` on the final candidate, `stop_reason: trial_limit`, and `workflow_accept: False`.

HumanEval/0, failing assertions per attempt (from saved `history[].internal.feedback`):

| Attempt | Testing | Reflexion | AutoGen |
|---|---|---|---|
| 1 | 1.0 (==True), 5.0 (==False) | 1.0, 5.0 | 1.0, 5.0 |
| 2 | 1.0, 5.0 | 1.0, 5.0 | 5.0 only |
| 3 | 1.0, 5.0 | 5.0 only | 5.0 only |

Note on Testing, attempt 2: I read the saved feedback for Testing attempts 1–3 in the first saved-record dump, and in each attempt both incorrect assertions are named. The per-attempt tables above reflect the saved feedback as printed.

HumanEval/1: every attempt fails only the incorrect assertion `'(( ) ( ))' == ['()', '()']`, in all three methods.

The saved feedback names only the failing assertions. Assertions not named in the feedback are not reported as failing, which suggests the other generated assertions passed. The saved records do not show each passing assertion explicitly.

### 4.4 Stop reasons

All three iterative methods stopped with `trial_limit` (3 attempts reached). No method stopped on `internal_pass` or `no_valid_tests`. Baseline stopped with `single_generation`.

### 4.5 Reflexion behavior

Reflexion produced 2 reflections per task, after attempts 1 and 2. Attempt 3 has no reflection, because the trial limit was reached.

HumanEval/0:
- Reflection 1 said the function "only checks adjacent elements after sorting". The candidate then changed to an all-pairs loop with `<`. This is a real algorithmic change. The strict `<` is still consistent with the spec.
- Reflection 2 said the comparison "should include equality (`<=` instead of `<`)". This diagnosis is driven by the incorrect 1.0 assertion. The candidate changed to `<=`. This is a revision away from the spec's strict reading, caused by misleading feedback.
- The attempt-3 candidate uses `<=`. It still fails the incorrect 5.0 assertion. Its official held-out result is pass. Official results for intermediate attempts are not saved, so I can't tell whether attempts 1–2 were officially correct.

HumanEval/1:
- Reflection 1 and reflection 2 both described the code as failing on "spaces between parentheses". The code already removes spaces (`paren_string.replace(" ", "")`). Both reflections are false diagnoses.
- Attempts 2 and 3 are essentially identical to attempt 1. The unified-diff comparison shows a whitespace-only change in attempt 1→2 and zero changed lines in attempt 2→3.

Conclusion for Reflexion: the reflections were driven by failing internal feedback. For HumanEval/1 they were false diagnoses, and the revisions were non-substantive. For HumanEval/0 the second reflection moved the candidate toward a non-spec boundary.

### 4.6 AutoGen reviewer decisions

All three reviewer responses per task were valid JSON with `review_valid: True` and decision `REVISE`. Programmer calls: 2 per task.

HumanEval/0:
- Attempt 1 review: REVISE. The reviewer said that the expected result for `([1.0, 2.0, 3.0, 4.0], 1.0)` "should return True because 1.0 and 2.0 are exactly 1.0 apart, which is not less than the threshold". The reviewer's own statement supports the strict reading, but its conclusion (True) contradicts it. This is an internal inconsistency in the reviewer's output. It also caused the candidate to change `<` to `<=`.
- Attempt 2 review: REVISE. It said the 5.0 case "should return True because all numbers are within 5.0". This is correct per spec. The 5.0 assertion is the incorrect one. The reviewer did not identify the 1.0 assertion as wrong here.
- Attempt 3 review: REVISE. It said "The expected result should be True". This is correct about the 5.0 assertion. The reviewer still issued REVISE, because the internal check failed.

HumanEval/1:
- All three reviews: REVISE. Each said `'(( ) ( ))'` "should return `['()', '()']`". This accepts the incorrect assertion as the spec. The reviewer did not identify the incorrect assertion.
- Attempt 3 review said the implementation "results in an error". The saved internal feedback shows only `AssertionError`, not an error. This review claim is not supported by the saved feedback.

Whether the reviewer identified erroneous internal tests:
- HumanEval/0: partly. It identified the 5.0 assertion as wrong (attempts 2 and 3), but not the 1.0 assertion, and its attempt-1 reasoning contradicted the spec.
- HumanEval/1: no. It accepted the incorrect assertion in all three reviews.

AutoGen programmer revisions:
- HumanEval/0 attempt 1→2: changed `<` to `<=` (following the reviewer's attempt-1 criticism) and added doctest examples to the docstring. Doctest additions are docstring-only.
- HumanEval/0 attempt 2→3: added an `n < 2` guard. The `<=` comparison was kept.
- HumanEval/1 attempt 1→2: whitespace and docstring-only changes. Attempt 2→3: zero changed lines.

### 4.7 Did revisions improve or degrade final official correctness?

All final candidates passed official held-out evaluation (`heldout_pass: True` for all 8 rows). Therefore no final candidate was degraded relative to the final candidate's official result.

I cannot say whether any intermediate revision improved or degraded official correctness. The saved records only contain official results for the final candidate of each method. Intermediate candidates were not evaluated officially, and this report does not claim otherwise. The final HumanEval/0 `<=` candidate passed official evaluation. This shows that the official evaluation did not reject `<=` for this problem. It does not show that `<=` is the spec-correct behavior.

### 4.8 Internal-test acceptance vs official correctness

For the final candidate of each iterative method:

- Internal result: **fail** (all 6 final candidates; 3 methods × 2 tasks).
- Official result: **pass** (all 6).

So all 6 final candidates were officially correct and internally rejected. The incorrect assertions caused this.

No candidate was internally accepted, so the existing metrics are all zero or undefined (`false_accept` is 0; `workflow_false_accept` is 0; the percentage columns are blank because the denominator is zero).

## 5. Why all three iterative methods failed internal tests while passing official evaluation

Each iterative method's internal test suite is shared. It includes the incorrect assertions. Any candidate that is officially correct but follows the spec will fail the incorrect assertion.

HumanEval/0: the incorrect assertions contradict the strict spec, so a spec-correct candidate fails at least one. Only a non-spec candidate (`<=`) passes the 1.0 assertion, and it still fails the 5.0 assertion. As a result, no candidate could pass the shared suite without violating the spec at the boundary or the 5.0 case.

HumanEval/1: the incorrect assertion requires a non-spec output, so no spec-correct candidate can pass it.

This explains the pattern: the suite is unsatisfiable by any spec-correct candidate, so every iterative method reached `trial_limit`. Official evaluation checks only the spec-correct behavior, so the final candidates passed.

**Proven by saved records:**
- Each iterative method reached `trial_limit`, with `internal_pass: False` on the final candidate.
- The final candidates passed official evaluation.
- The internal feedback names the incorrect assertions.
- The incorrect assertions are the only ones named as failing in HumanEval/1 across all attempts.

**Suggested but not proven by saved records:**
- That every spec-correct candidate must fail the suite. This follows from the assertion analysis in section 3, which I verified by reasoning against the public wording. It is not an executed test.
- That intermediate candidates were officially correct. Not recorded.

## 6. Scientific interpretation

Research question: can proactive multi-agent code review identify mistakes that failure-triggered self-reflection overlooks?

What this pilot shows, on two problems:
- Incorrect generated tests produce misleading internal feedback. In HumanEval/0 Reflexion, misleading feedback led to a revision away from the spec's strict reading (reflection 2 → `<=`).
- Iterative revisions occurred even though the candidate was spec-correct. Testing, Reflexion, and AutoGen used 3 attempts per task and 4, 6, and 7 calls, respectively, versus 1 call for baseline. The extra calls did not improve official correctness here, because the baseline was already official-correct.
- AutoGen's reviewer did not reliably detect the incorrect tests. It identified one incorrect assertion (5.0, in HumanEval/0, attempts 2–3) and missed the others. It accepted the HumanEval/1 incorrect assertion in all three reviews. It also gave an unsupported "results in an error" claim.
- AutoGen cost the most: 7 calls and 5282.5 mean total tokens versus 2206 for Testing and 3653 for Reflexion. It produced no official gain over baseline here.

Did proactive review help on these pilot problems? **No.** The reviewer did not identify the erroneous tests reliably, did not produce an approval (no candidate was approved), and did not improve official correctness over the baseline, which was already correct. This is a finding about these two problems only.

## 7. Internal false rejection (new observation; existing metric unchanged)

The existing false-acceptance metric is unchanged:

- `false_accept` = internal pass AND official fail.
- `workflow_false_accept` = workflow accept AND official fail.

Neither metric was redefined.

The pilot observes a different outcome, which I call **internal false rejection** for this report only:

- Internal false rejection: internal fail AND official pass.

It is retrospective and applies only to final candidates here, because intermediate official results are not saved. For the final candidates, internal false rejection occurred in 6 of 6 cases (3 methods × 2 tasks). It is not part of the existing metric set, and it should not be added to `analyze_results.py` without an explicit methodology decision.

The baseline row has no internal test, so it contributes no internal rejection.

Official held-out results were used only for retrospective reporting in this document. They were not fed into any reflection, review, or revision prompt. The AutoGen reviewer prompt contains the internal test outcome, not the official result (`build_reviewer_prompt` in `autogen_agents.py`). The reflection and revision prompts in `agents.py` were read in an earlier turn, and I did not re-verify them in this report.

## 8. Limitations

- Two problems only. This cannot establish a general performance advantage or disadvantage for AutoGen, Reflexion, or Testing.
- Model: `gpt-4o-mini`, single seed (42), temperature 0. No variance estimate.
- Official results saved for final candidates only. Claims about intermediate candidates are not supported.
- The strict-spec reading of HumanEval/0 rests on the docstring wording. The public examples do not test the boundary.
- Feedback shows failing assertions only. Passing assertions are inferred from their absence.
- The attempt-level diffs were computed from saved code; no code was executed for this report.
- Token counts come from the provider's usage fields (`tokens_available: True` for all events).
- Latency is wall-clock on one machine and includes Docker overhead.

## 9. Implications for the final experiment

1. Validate generated tests before the final benchmark. Two of 12 generated assertions in the pilot were wrong. Any change to test validation must be a documented methodology change, not a silent edit. Existing policy must not be changed without approval.
2. Track internal false rejection as a separate, pre-registered metric. Do not replace or redefine `false_accept` or `workflow_false_accept`.
3. Store official results for every candidate, not only the final one. Without them, the claim "revision improved or degraded official correctness" cannot be assessed.
4. Analyse reviewer decisions against a labelled set of erroneous tests. The pilot shows the reviewer can accept an incorrect assertion (HumanEval/1) and can give unsupported claims (HumanEval/1 attempt 3).
5. Report AutoGen's extra cost (about 2.4× the Testing mean total tokens and 1.4× the Reflexion mean total tokens in this pilot) alongside any accuracy result.
6. Do not interpret the pilot as evidence for or against AutoGen. It is a pipeline check with an observed failure mode.

## 10. Evidence files

- `results/pilot_autogen/config.json`: configuration
- `results/pilot_autogen/results.jsonl`: 8 rows with full histories, internal feedback, reviewer decisions, reflections, and token counts
- `results/pilot_autogen/summary.csv`: method summary
- `results/pilot_autogen/accuracy.png`, `results/pilot_autogen/tokens.png`: generated plots (not inspected for this report)
- `verification/copilot_autogen_end_to_end.txt`: scripted end-to-end test output (3 passed)
- `verification/copilot_full_suite_e2e.txt`: full suite output (100 passed)
- `verification/docker_integration_report.md`: Docker integration report
- `verification/NOTES.md`: existing verification notes
- `verification/copilot_verification_findings.md`, `verification/copilot_corrections_report.md`: earlier verification history
- `docs/AUTOGEN_DESIGN.md`: design document (not modified here)
