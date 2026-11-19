# Final Experiment Report: Proactive AutoGen Review vs Failure-Triggered Reflexion (HumanEval, n=20)

Branch: `path2-autogen`. Retrospective, documentation-only analysis. No production code, tests, or saved results were modified. No benchmark was rerun and no API calls were made. All numbers come from `results/final_autogen/` and were recomputed from `results.jsonl`. Spec-level checks use the public docstrings in `data/HumanEval.jsonl.gz`. Official tests were not used to generate or repair internal tests.

---

## 1. Abstract / executive summary

On 20 HumanEval problems with `gpt-4o-mini` (seed 42, max 3 candidate attempts), official held-out accuracy was Baseline 17/20 (85%), Testing 17/20 (85%), Reflexion 19/20 (95%), and AutoGen 17/20 (85%).

- **Reflexion's advantage is two problems.** It solved HumanEval/105 and HumanEval/163, which no other method solved. AutoGen had zero exclusive successes. An exact McNemar test on the discordant pairs (2 vs 0) gives two-sided $p = 0.5$. The run does not establish a difference.
- **AutoGen never approved a candidate.** The reviewer returned 60 valid `REVISE` decisions and 0 `APPROVE` decisions. All 20 problems therefore ended at `trial_limit`, and workflow acceptance was 0/20. The JSON contract was satisfied: no reviewer response was malformed. The lack of approval is a behavioural outcome, not a parsing failure.
- **The shared generated tests were often wrong.** I checked the public wording for the failing expectations and found six problems whose generated tests contradict the public specification or use out-of-domain inputs (50, 82, 107, 123, 131, 153). HumanEval/163 is different: its generated test is spec-consistent and the candidate is wrong. Internal rejection of the initial candidate was frequently caused by these tests, not by the code.
- **The reviewer was partly right and partly wrong.** Of the 20 problems, I classified the reviewer's criticism as valid for 5, incorrect or spec-contradicting for 8, beyond-spec or coverage-only for 5, and mixed for 2. The classification is my manual reading, not an automated metric.
- **An environment defect confounds the 105/163 result.** The sandbox image (`python:3.11-slim`) does not contain `numpy`. The shared initial candidate imports it, so the baseline fails both problems with `ModuleNotFoundError`. Reflexion's two exclusive successes came from removing that import. The AutoGen programmer kept it on HumanEval/105 across all three attempts.
- **No method's final official correctness fell below the baseline.** AutoGen and Testing match the baseline solved set exactly. Reflexion adds two problems.

Bottom line: the 20-problem run gives no evidence that proactive multi-agent review helps or hurts correctness. It does show that the AutoGen integration is technically sound but its reviewer, as configured, does not approve, so the workflow never reaches its intended acceptance path.

---

## 2. Research question and methodology

**Research question:** Can proactive multi-agent code review identify mistakes that failure-triggered self-reflection overlooks?

**Methods** (all share one initial candidate and one generated test suite per problem):

| Method | Trigger for revision | Stop rule |
|---|---|---|
| A. Baseline | none (the shared initial candidate, one generation) | 1 generation |
| B. Testing | internal test failure → programmer revision | internal pass, or 3 attempts |
| C. Reflexion | internal test failure → reflection → revision | internal pass, or 3 attempts |
| D. AutoGen | reviewer call on every attempt, then programmer revision if not accepted | acceptance requires internal pass **and** a valid `APPROVE`, or 3 attempts |

Official held-out evaluation is performed after all arms have stopped. It is never placed in any agent prompt. Verified in `autogen_agents.py`: `build_reviewer_prompt` and `build_programmer_prompt` receive only the internal evaluation (`PASSED`, or `FAILED (...)` with the internal feedback).

Shared-artifact note: the baseline row's code is the shared initial candidate. I verified that the first-attempt code of Testing, Reflexion, and AutoGen is identical to the baseline code for all 20 problems. The baseline's official result is therefore also the official result of attempt 1 for every method.

---

## 3. Experimental configuration and reproducibility

From `results/final_autogen/config.json`:

| Setting | Value |
|---|---|
| Model / temperature / seed | `gpt-4o-mini` / 0.0 / 42 |
| Dataset | HumanEval (`data/HumanEval.jsonl.gz`), SHA-256 `b796127e635a67f93fb35c04f4cb03cf06f38c8072ee7cee8833d7bee06979ef` |
| Sample | 20 problems: 125, 105, 163, 156, 151, 131, 123, 79, 12, 9, 50, 107, 106, 120, 15, 153, 7, 92, 82, 145 |
| Max candidate attempts | 3 |
| Sandbox | Docker, `python:3.11-slim`, `--network none`, `--read-only`, 15 s timeout (per evaluator defaults) |
| Memory | latest reflection only; shared initial candidate and tests |
| Pilot flag | false |

Files: `results/final_autogen/config.json`, `results/final_autogen/results.jsonl` (80 rows = 20 × 4), `results/final_autogen/summary.csv`, `results/final_autogen/accuracy.png`, `results/final_autogen/tokens.png`. Original files were read only.

Reproducibility limits: the run used the provider's API at a single point in time. Repeat calls are not guaranteed to reproduce identical outputs even at temperature 0.

---

## 4. Four-method results table

From `summary.csv` (recomputed where noted):

| Method | Official accuracy | Internal-pass (final) | Workflow-accepted | False accept (internal pass, official fail) | Mean attempts | Mean calls | Mean total tokens | Mean solve s |
|---|---|---|---|---|---|---|---|---|
| Baseline | 17/20 = 85.0% | n/a | n/a | n/a | 1.0 | 1.0 | 392.2 | 3.18 |
| Testing | 17/20 = 85.0% | 12/20 | 12 | 0/12 | 1.8 | 2.8 | 1432.4 | 8.40 |
| Reflexion | 19/20 = 95.0% | 14/20 | 14 | 0/14 | 1.75 | 3.5 | 1996.75 | 9.62 |
| AutoGen | 17/20 = 85.0% | 11/20 | 0 | undefined (0 accepted) | 3.0 | 7.0 | 5163.75 | 19.52 |

Internal false rejection (internal fail AND official pass), counted on final candidates only, because intermediate official results were not saved:

| Method | Denominator (internal fail) | Internal false rejections |
|---|---|---|
| Testing | 8 | 5/8 (50, 82, 107, 123, 153) |
| Reflexion | 6 | 5/6 (50, 82, 107, 123, 153) |
| AutoGen | 9 | 6/9 (50, 82, 107, 123, 131, 153) |

These are reported as a retrospective observation. The existing metrics `false_accept` and `workflow_false_accept` are unchanged.

---

## 5. Per-problem paired comparison

Official = held-out pass. Internal columns show final-candidate status for Testing / Reflexion / AutoGen. The AutoGen column shows attempts 1, 2, 3 (every AutoGen run has three attempts). The reviewer category is my manual classification of the saved criticism (see section 6).

| Task | Baseline | Testing | Reflexion | AutoGen | Internal T / R / A | AutoGen internal A1→A2→A3 | Reviewer category |
|---|---|---|---|---|---|---|---|
| 7 | ✓ | ✓ | ✓ | ✓ | P / P / P | P→P→P | Incorrect |
| 9 | ✓ | ✓ | ✓ | ✓ | P / P / P | P→P→P | Beyond-spec / coverage |
| 12 | ✓ | ✓ | ✓ | ✓ | P / P / P | P→P→P | Incorrect (tie-break) |
| 15 | ✓ | ✓ | ✓ | ✓ | P / P / P | P→P→P | Beyond-spec / coverage |
| 50 | ✓ | ✓ | ✓ | ✓ | F / F / F | F→F→F | Incorrect (endorses wrong expectations) |
| 79 | ✓ | ✓ | ✓ | ✓ | P / P / P | P→P→P | Beyond-spec / coverage |
| 82 | ✓ | ✓ | ✓ | ✓ | F / F / F | F→F→F | Valid (test defect) |
| 92 | ✓ | ✓ | ✓ | ✓ | P / P / P | P→P→P | Beyond-spec / repetitive |
| 105 | ✗ | ✗ | ✓ | ✗ | F / P / F | F→F→F (numpy) | Valid (numpy), not acted on |
| 106 | ✓ | ✓ | ✓ | ✓ | P / P / P | P→P→P | Incorrect (equivalent "fix") |
| 107 | ✓ | ✓ | ✓ | ✓ | F / F / F | F→F→F | Incorrect (endorses wrong expectation) |
| 120 | ✓ | ✓ | ✓ | ✓ | P / P / P | P→P→P | Mixed |
| 123 | ✓ | ✓ | ✓ | ✓ | F / F / F | F→F→F | Incorrect (reviewer said a valid odd value was missing; candidate is correct) + shared test wrong |
| 125 | ✓ | ✓ | ✓ | ✓ | P / P / P | P→F→P | Incorrect (claim); revision broke correct tests |
| 131 | ✓ | ✓ | ✓ | ✓ | P / P / F | P→F→F | Valid (invalid test input) |
| 145 | ✗ | ✗ | ✗ | ✗ | F / F / F | F→F→F | Valid, not acted on |
| 151 | ✓ | ✓ | ✓ | ✓ | P / P / P | P→P→P | Incorrect (A1) |
| 153 | ✓ | ✓ | ✓ | ✓ | F / F / F | F→F→F | Mixed (valid tie rule; missed invalid test) |
| 156 | ✓ | ✓ | ✓ | ✓ | P / P / P | P→P→P | Beyond-spec / coverage |
| 163 | ✗ | ✗ | ✓ | ✗ | F / P / F | F→F→F | Valid (numpy, zero inputs, final error) |

Paired counts:

- Solved by all four methods: 17 (7, 9, 12, 15, 50, 79, 82, 92, 106, 107, 120, 123, 125, 131, 151, 153, 156).
- Solved by none: 1 (145).
- Reflexion-only: 2 (105, 163).
- AutoGen-only: 0.
- Testing-only or Baseline-only: 0. Testing and AutoGen solved sets are identical to the baseline set.

Exact McNemar on Reflexion vs AutoGen: 2 discordant pairs, all favouring Reflexion. Two-sided exact $p = 2 \times (1/2)^2 = 0.5$. The same calculation for Reflexion vs Testing gives $p = 0.5$. The sample cannot distinguish the methods.

Revisions that changed correctness:

- **Improved final official correctness:** Reflexion on 105 (numpy removed in revision 1) and 163 (reflection-driven rewrite to even digits, after numpy removal). No AutoGen or Testing revision improved final official correctness.
- **Degraded internal status (not final official):** AutoGen 125 (attempt 1 pass → attempt 2 fail → attempt 3 pass) and AutoGen 131 (attempt 1 pass → attempt 2 fail). The specific assertions that broke were not re-inspected in this report. For 131 the final internal result stayed failing; the official final still passed.
- **Degraded final official correctness:** none. Each method's final official result is at least the baseline's on every problem.

Known versus unknown:

- Known: attempt-1 official results for all 20 problems (baseline row), and final official results for all candidates.
- Unknown: official results for attempts 2 and 3 of any method. These were not evaluated. Statements about intermediate candidates in this report are about internal status only.

---

## 6. Detailed reviewer-decision analysis

### 6.1 Counts (from `results.jsonl`)

- Reviewer calls: 60 (3 per problem, 20 problems).
- Valid JSON responses: 60. Malformed: 0.
- `REVISE`: 60. `APPROVE`: 0.
- Stop reasons: `trial_limit` 20/20.
- Programmer calls: 40 (2 per problem). Every programmer call followed a `REVISE`.
- Reviewer tokens: input 43,463; output 6,498 (mean 724 input and 108 output per call).

### 6.2 Why every review was REVISE: observable evidence

Each review in the saved records names a specific issue. The reviewer produced a concrete correction for 60/60 reviews, including reviews of candidates that passed all internal tests. The observed pattern is consistent with the reviewer prompt, which I read in `autogen_agents.py`:

- "Review the candidate even when its tests passed; do not approve merely because tests passed."
- "Check correctness against the public specification, look for missing edge cases, incorrect assumptions, and whether the shown tests are themselves incomplete."
- "Provide concrete, specific criticism rather than vague suggestions."

The prompt tells the reviewer to find problems, but it does not describe what a candidate that should be approved looks like, and it says nothing about approving when no defect exists. This is a hypothesis: the prompt may encourage excessive criticism. I did not isolate it (no ablation was run), so it is not a proven cause. The saved records are consistent with it: several reviews concern coverage, not correctness (see 6.4).

### 6.3 Whether reviews of internally passing candidates requested revisions

All 12 problems whose initial candidate passed the shared tests (7, 9, 12, 15, 79, 92, 106, 120, 125, 131, 151, 156) received `REVISE` on attempt 1, and the programmer was called on each. The policy requires `APPROVE` for acceptance, so these candidates could not be accepted.

### 6.4 Classification of criticism

Classification is my manual reading of the saved review text, checked against the public docstrings. It is not an automated metric and has no second rater.

- **Valid (criticism identifies a real defect, a wrong test, or a spec-required behaviour):** 82, 105, 131, 145, 163.
- **Incorrect or spec-contradicting:** 7, 12, 50, 106, 107, 123, 125, 151.
- **Beyond-spec or coverage-only:** 9, 15, 79, 92, 156.
- **Mixed:** 120, 153.

Representative examples, with checks:

1. **Incorrect: HumanEval/12 (tie-break).** Reviewer A1 claimed the tie-break was wrong (paraphrased). The saved code uses `if len(string) > len(longest_string)`, which keeps the first longest string. The public docstring requires the first one. The reviewer's claim is wrong. The later programmer change was not re-verified here. Usefulness: none.
2. **Incorrect: HumanEval/7 (empty substring).** Reviewer A2 claimed the empty substring is not handled (paraphrased). The A1 code `[s for s in strings if substring in s]` already returns every string, since `'' in s` is true. The A3 guard is behaviour-preserving. The case-sensitivity point in the reviewer's A1 text was not re-checked in this report.
3. **Incorrect, and endorses a wrong test: HumanEval/107.** The shared test asserts `even_odd_palindrome(10) == (4, 6)`. Public spec: palindromes 1–9 (4 even, 5 odd) plus n=10 has none, so the value is $(4, 5)$. The reviewer repeated the same claim on all three attempts and blamed the candidate. The candidate passed the official test.
4. **Incorrect, and endorses wrong tests: HumanEval/50.** The shared tests expect `decode_shift('nopqr') == 'ijklmn'` and `decode_shift('uvwxy') == 'pqrs'`. Shifting by 5 gives `'ijklm'` and `'pqrst'`. The public docstring specifies only the encoding direction, so decoding as the inverse shift is an inference; under that reading, both expectations contradict the spec, and the `'pqrs'` expectation also drops a character. The reviewer's A3 "correction" (`'ijklmn'`, `'qrstuv'`) is also wrong.
5. **Incorrect (A1): HumanEval/151.** The public spec says to ignore negative numbers. Reviewer A1's criticism was classified as contradicting this; the specific example was not re-checked in this report.
6. **Incorrect (claim): HumanEval/125.** The saved reviewer text was classified as incorrect on this problem; the exact claim was not re-checked in this report. The internal status went pass → fail → pass across attempts 1–3, and the official result stayed pass.
7. **Incorrect (formula): HumanEval/106.** Reviewers A2 and A3 said the odd-index sum was "incorrectly calculated". The A2 code used `sum(range(1, i + 1))`, which equals `i*(i+1)//2`. The A3 revision is equivalent. The official and internal results for A1, A2, and A3 are all pass.
8. **Valid, test defect: HumanEval/82.** The shared test asserts `prime_length('ab') == False`. Length 2 is prime, so the public spec gives True. The reviewer identified this on all three attempts. No candidate change can satisfy the test, and the workflow has no path to repair a test. The official result is pass.
9. **Valid, test defect plus a candidate-side error: HumanEval/131.** The shared test asserts `digits(0) == 0`, but the spec defines input as a positive integer. The reviewer repeatedly objected to zero as an input (paraphrased). The AutoGen attempt-2 candidate fails the shared test `digits(0) == 0`; the exact code change was not re-inspected, so the mechanism is unverified. The final internal result is fail; the official final result is pass.
10. **Valid, not acted on: HumanEval/105 (numpy).** Reviewers A2 and A3 flagged the `numpy` import as a problem (paraphrased). `numpy` is absent from the sandbox image. The programmer kept the import on all three attempts. This was the clearest case of valid criticism not converted into a fix.
11. **Valid, final-round criticism not addressed: HumanEval/163.** Reviewer A3 said that `generate_integers(10, 14)` should return `[]` (only single digits are considered), and that `[10, 12, 14]` is wrong. This is spec-consistent. The candidate kept the error, and the trial limit ended the run before any fix. The reviewer's A2 text concerned zero input (`ValueError`), not this assertion; I did not verify a contrary claim about `[10, 12, 14]` in A2, so no contradiction is asserted.
12. **Valid, not acted on: HumanEval/145.** Reviewers A2 and A3 flagged that negative numbers are not sorted by the negative-first-digit rule. The public spec and the shared test are consistent with this rule. The candidate did not implement it, and no attempt fixed it. Official result: fail for all methods.
13. **Mixed: HumanEval/120.** Reviewer A2 said `k = 0` must return `[]` (the spec states `0 <= k <= len(arr)`), and that raising `ValueError` for `k < 1` is wrong. This is spec-consistent. Reviewer A3's `[1, 1, 1], k=2` claim is not supported by a demonstrated failure.
14. **Mixed: HumanEval/153.** Reviewers A2 and A3 said ties should keep the first extension, which is spec-consistent. The shared test expects `'Animals.Elephant'`. Strength is CAP − SM: Dog = 1 − 2 = −1, Elephant = 1 − 7 = −6, so Dog or Cat is strongest. Elephant has the lowest strength (1 − 7 = −6), so `Animals.Elephant` contradicts the spec under any tie rule. The reviewers did not identify this.

### 6.5 Repetition across attempts

Several reviews repeated the same criticism with no progress, most visibly:

- 92: essentially identical text on all three attempts (negative/zero coverage).
- 107 and 82: the same incorrect or test-driven claim on each attempt.
- 105: `numpy` flagged on attempts 2 and 3, not removed.
- 50 and 153: the same wrong expectation or unaddressed defect repeated.

Repetition is a property of the saved text; I did not measure text similarity.

### 6.6 Whether programmer revisions changed internal outcomes

From the saved internal transitions:

- AutoGen: two transitions to fail (125 at attempt 2, 131 at attempt 2); two transitions back to pass (125 at attempt 3). Zero transitions from fail to pass.
- Reflexion: two transitions from fail to pass (105 at attempt 2, 163 at attempt 3).
- Testing: per-attempt internal results were not dumped in this review, so no Testing transitions are claimed. Its final internal status is in section 5.

Revisions therefore helped only in Reflexion, and only through reflection-driven changes on 105 and 163.

### 6.7 Did the workflow policy enforce the configured stopping rules?

Yes, as far as the saved records can show.

- Acceptance requires internal pass and a valid `APPROVE`: consistent with 0 accepted in 20 problems. No `APPROVE` was ever issued, so acceptance was never exercised in the real run.
- The `trial_limit` rule ended all 20 runs after attempt 3 with exactly 3 reviews and 2 programmer calls. The returned code is the attempt-3 candidate, which is the one reviewed.
- The malformed-response fallback (never treated as approval) was not exercised in this run. It is covered by 16 unit tests in `tests/test_autogen_agents.py`.
- The empty-tests check did not trigger (`test_count` = 6 for all 20).
- Official results were not passed to any agent. This is verified by code reading and by the prompt contents.

Design consequence (not a policy violation): because acceptance requires `APPROVE`, AutoGen never stops early, even when attempt 1 passes internal tests (12 problems). Testing and Reflexion stop on internal pass. This explains most of the cost gap (section 9). The final round's criticism is never addressed in any problem: all 20 end with an unaddressed `REVISE`.

---

## 7. Representative real examples

Examples where a useful criticism is available: 105 (numpy import), 131 (invalid zero input), 145 (negative-digit ordering), 163 (A3: `[10, 12, 14]` should be `[]`), and 82 (wrong test expectation, identified on all three attempts).

Examples of incorrect or unsupported criticism: 12 (tie-break), 7 (empty substring), 107 (endorses (4,6)), 50 (endorses `'ijklmn'`), 151 (A1 negatives), 125 (`'Hello, world!'`).

Examples of coverage-only revisions: 156 (A2 and A3 only request more tests), 92 (same text repeated), 9 (tests for large numbers).

Examples of revisions that broke internal status: 125 (attempt 2 fails internally) and 131 (attempt 2 fails internally). For 163, attempt 2 fails the `(0, 10)` test, which uses an invalid input, so that failure is a test defect, not a candidate regression.

---

## 8. Internal-test reliability discussion

Generated internal tests are the acceptance signal for Testing and Reflexion, and they are the test suite the AutoGen reviewer also critiques. Evidence from the saved records:

- **Wrong expectations:** 50, 82, 107, 123, and 153 each contain an expectation that contradicts the public wording (verified in section 6.4). In each case, every iterative method failed internal tests on the final candidate.
- **Invalid inputs:** 131 (`digits(0)`) and 163 (`generate_integers(0, 10)`) include zero, outside the spec's positive-integer domain. The 163 candidate's `(10, 14)` error is a real candidate error and is not a test defect.
- **Environment defect:** 105 and 163 use `numpy` (initial candidate), which is absent from the sandbox. The baseline fails these with `ModuleNotFoundError`, which is not a logic failure.
- **Coverage gaps:** 12 (ties) passed the shared tests on attempt 1 even though the tie-break was not tested. The reviewer was right that it is a spec requirement, but the code already satisfied it (see 6.4 item 1).

Consequences:

- Internal false rejection is common in this run: 5/8 (Testing), 5/6 (Reflexion), 6/9 (AutoGen) on final candidates. Most of these are caused by wrong or invalid shared tests, not by candidate errors.
- A candidate can be spec-correct and still be rejected. The workflow has no mechanism to repair a test, so the reviewer's correct criticism of a test (82, 131) cannot change the outcome.
- Internal acceptance (12 Testing, 14 Reflexion, 11 AutoGen-final-internal) is therefore a weak proxy for correctness on this sample.

The earlier two-problem pilot (`verification/pilot_findings_report.md`) found the same pattern: two wrong generated assertions out of 12, and an internally rejected but officially correct final candidate in all three iterative methods.

---

## 9. Accuracy-versus-computation tradeoff

### 9.1 Per-method cost (from `summary.csv`, recomputed from `results.jsonl`)

| Method | Mean attempts | Mean calls | Mean total tokens | Mean solve s | Official accuracy |
|---|---|---|---|---|---|
| Baseline | 1.0 | 1.0 | 392.2 | 3.18 | 85% |
| Testing | 1.8 | 2.8 | 1432.4 | 8.40 | 85% |
| Reflexion | 1.75 | 3.5 | 1996.75 | 9.62 | 95% |
| AutoGen | 3.0 | 7.0 | 5163.75 | 19.52 | 85% |

### 9.2 Attributed calls and tokens by stage (exact sums over 20 problems)

Shared stages are attributed to every method that uses them (logically, not as additional billing):

| Stage | Calls | Input tokens | Output tokens | API seconds |
|---|---|---|---|---|
| `initial_code` (shared, all methods) | 20 | 3,516 | 4,328 | 63.5 |
| `generate_tests` (shared, Testing/Reflexion/AutoGen) | 20 | 3,791 | 2,399 | 40.7 |
| AutoGen `autogen_reviewer` | 60 | 43,463 | 6,498 | 115.0 |
| AutoGen `autogen_programmer` | 40 | 27,640 | 11,640 | 159.2 |

Also verified from `llm_events` (calls, input/output tokens, LLM seconds): Testing `revision` 16 calls, 10,877 / 3,737, 56.0 s; Reflexion `reflection` 15 calls, 9,711 / 1,269, 22.7 s; Reflexion `revision` 15 calls, 11,025 / 3,896, 58.4 s. Shared `initial_code` and `generate_tests` latencies are 63.5 s and 40.7 s.

Per-method totals (mean total tokens × 20, in + out combined): Baseline 7,844; Testing 28,648; Reflexion 39,935; AutoGen 103,275. These include shared stages and must not be summed across methods to estimate API billing.

Caveats: latency is wall clock on one machine and includes Docker overhead; the summary's `solve_seconds` includes shared generation time for each arm.

### 9.3 Cost per correct answer

Descriptive only. Mean total tokens per officially correct answer: Baseline 392.2/0.85 ≈ 461; Testing 1432.4/0.85 ≈ 1685; Reflexion 1996.75/0.95 ≈ 2102; AutoGen 5163.75/0.85 ≈ 6075. These are not a validated efficiency metric.

---

## 10. Findings about proactive versus failure-triggered review

What the 20-problem data show:

- Failure-triggered reflection (Reflexion) had both exclusive successes, through revisions that followed internal failures.
- Proactive review (AutoGen) was not effective here: no `APPROVE`, no exclusive successes, and the highest cost.
- Proactive review did surface a few valid defects (105 numpy, 131 invalid zero, 145 negative-digit ordering, 163 final `[10, 12, 14]` error, 82 wrong test). None of these produced a final correctness gain for AutoGen, because the programmer did not act on them consistently, the trial limit ended revision before the final-round criticism could be used, or a test defect blocked any candidate from passing.
- Some reviews were incorrect (12, 7, 107, 50, 123, 151, 125) and drove unnecessary or harmful revisions. Revisions in 125 and 131 broke internally correct behaviour.

What the data do not show:

- Whether proactive review is generally better or worse than reflection. The data contain no general performance claim.
- Whether an approval path with a better-calibrated reviewer would change the outcome. The reviewer never approved, so the acceptance path was never exercised.

The distinction the run supports: the AutoGen integration is technically successful (valid JSON, correct stop rules, correct cost accounting, no leakage), but the reviewer as configured did not perform effective code review. Technical success and effective review are different things.

---

## 11. Limitations and threats to validity

1. **Sample size.** 20 problems, one seed, one model. The discordant-pair count is 2 vs 0. Exact $p = 0.5$. No general claim is supported.
2. **Environment defect.** The sandbox lacks `numpy`. The baseline's 105/163 failures and the Reflexion successes on those problems depend on this. The result is confounded by an environment property, not only by reasoning.
3. **Invalid shared tests.** Six problems (50, 82, 107, 123, 131, 153) have a shared-test expectation that contradicts the spec or uses an out-of-domain input. HumanEval/163's shared test is spec-consistent; its candidate is wrong. Results are conditional on this test suite, and the same suite is used for all methods.
4. **No intermediate official evaluation.** Official results are known only for attempt 1 (= baseline) and for the final candidate. Claims about attempts 2 and 3 are internal only.
5. **Asymmetric stopping.** Testing and Reflexion stop on internal pass; AutoGen never does. The final candidate for AutoGen is therefore produced by a different process, which affects both correctness and cost comparisons.
6. **Manual classification.** The criticism categories in section 6.4 are one rater's reading of the saved text, with no second rater and no reliability estimate. Section 7 examples are checked against the public docstrings, not against executed candidate code, except where noted.
7. **Spec checks.** Spec-level checks were done by reading the public docstrings and computing small cases by hand (for example, the palindrome count for $n = 10$). Candidate code was not executed for this report.
8. **Single run.** No variance estimate. Provider outputs can vary even at temperature 0.
9. **Token and time attribution.** Shared stages are counted in every method's total (section 9.2). Latency includes Docker and API variance.
10. **Reference to the original AutoGen paper.** The results are about this implementation: a specific reviewer prompt, a specific acceptance rule, a specific model, and a specific sandbox. They say nothing about the AutoGen framework or the original paper's experiments. The paper cannot be credited or blamed for these outcomes.

---

## 12. Conclusions and implications for our presentation

Conclusions the data support:

1. The AutoGen workflow runs correctly end to end: valid JSON on 60/60 reviews, correct trial-limit stopping on 20/20, correct token and call accounting, and no leakage of official results.
2. In this run, AutoGen had 0 approvals, 0 workflow-accepted solutions, 0 exclusive successes, and 3.0 mean attempts at 7.0 mean calls. Its final official accuracy equals the baseline (17/20).
3. Reflexion's 19/20 is a difference of two problems (105, 163). Both depend on removing an unavailable import and a reflection-driven rewrite. The difference is not statistically established ($p = 0.5$).
4. Shared generated tests were wrong or invalid for 6 of 20 problems and drove most internal rejections.
5. Reviewer criticism was a mix of valid defects, incorrect claims, and coverage requests. The reviewer's lack of approval, together with a trial limit that discards the final round's criticism, prevented any defect it found from reaching the candidate.

Implications for the presentation:

- Present the 85% / 85% / 95% / 85% results together with the two-problem difference, the exact $p$, and the 20-problem limit.
- Present the reviewer's valid and invalid criticisms as separate findings, and present the lack of approval as a limitation of the reviewer configuration, not as evidence against proactive review.
- Present the numpy environment defect and the invalid shared tests as threats to validity.
- Do not claim that proactive review hurts correctness, that Reflexion universally outperforms AutoGen, or that the original AutoGen paper shows better HumanEval performance.

Recommended items for a later decision (not done here, and not part of this report):

- Pre-register a rule for internal false rejection before any larger run.
- Decide whether to include `numpy` in the sandbox or to exclude problems that import it. This is a methodology change that must be approved before use.
- Decide whether the reviewer should be able to approve on no defect found, and whether final-round criticism should be acted on. Both are design changes.

---

## 13. References to the existing pilot report and verification evidence

- `verification/pilot_findings_report.md`: two-problem pilot (HumanEval/0 and /1). Its findings on wrong generated assertions and the reviewer's behaviour are consistent with the final data. Note that the pilot's reviewer-behaviour statements were written before the full-run data were available.
- `results/pilot_autogen/` and `results/final_autogen/`: original saved results (read only).
- `verification/copilot_autogen_end_to_end.txt`: scripted end-to-end tests (3 passed).
- `verification/copilot_full_suite_e2e.txt`: full suite (100 passed).
- `verification/docker_integration_report.md`: real Docker integration report.
- `verification/copilot_docker_integration_before.txt`, `verification/copilot_full_suite_with_docker.txt`: earlier Docker and full-suite evidence.
- `docs/AUTOGEN_DESIGN.md`: design of Method D (not modified).
- `verification/NOTES.md`: existing notes (not modified).
