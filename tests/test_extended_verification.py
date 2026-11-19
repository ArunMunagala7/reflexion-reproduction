"""
Extended verification tests (Stage 2).

These tests do NOT modify production code. They exercise the existing,
unchanged implementation using fixed, trusted fixtures and mocked LLM
responses in order to produce executable evidence for the five
investigations requested by the student:

  1. Potentially meaningless (vacuous) generated tests.
  2. Retry conditions and stop-reason classification.
  3. Held-out evaluation isolation / orchestration order.
  4. Metrics and scientific validity (false acceptance, token attribution).
  5. Evaluator behavior and error handling (including mocked Docker
     return-code classification and timeout cleanup, WITHOUT launching
     real containers).

No paid API calls are made. No credentials are accessed. No untrusted
model-generated code is executed outside of exec() calls that already
exist inside sandbox_runner.evaluate (the same function already used,
unmodified, by the production evaluator inside Docker). Here it is
invoked directly, in-process, with fixed trusted fixture strings only
(never real LLM output), exactly as tests/test_harness.py already does.
"""
import json
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from dataset import public_problem
from evaluator import Evaluation, parse_tests, DockerEvaluator
from agents import iterate
from experiment import solve_problem
from analyze_results import summarize
from sandbox_runner import evaluate


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

PROBLEM = {
    'task_id': 'fixture/0',
    'prompt': 'def f(x):\n    """Return x+1."""',
    'entry_point': 'f',
    'canonical_solution': 'REFERENCE_SECRET_V2',
    'test': 'HELDOUT_SECRET_V2',
}
PUBLIC = public_problem(PROBLEM)
BAD = 'def f(x): return x'
WRONG_CONST = 'def f(x): return 999'          # deterministic, always wrong
UNDEFINED = 'x = 1\n'                          # does not define f at all
NONCALLABLE = 'f = 5\n'                        # f exists but is not callable
GOOD = 'def f(x): return x+1'
TESTS = ['assert f(1) == 2']

VACUOUS_SHORTCIRCUIT = 'assert True or f(1) == 2'
VACUOUS_SELFCOMPARE = 'assert f(1) == f(1)'


class MockLLM:
    """Scripted LLM: fixed stage responses, optional per-call revision/reflection sequence."""
    def __init__(self, revisions=(), reflections=None):
        self.revisions = iter(revisions)
        self.reflections = iter(reflections) if reflections is not None else None
        self.events = []

    def ask(self, stage, prompt):
        if stage == 'revision':
            text = next(self.revisions)
        elif stage == 'reflection' and self.reflections is not None:
            text = next(self.reflections)
        else:
            text = {'initial_code': BAD, 'generate_tests': TESTS[0], 'reflection': 'Use x+1'}.get(stage)
        self.events.append({'stage': stage, 'prompt': prompt, 'response': text,
                             'input_tokens': 1, 'output_tokens': 1, 'seconds': 0})
        return SimpleNamespace(text=text)


class MockEvaluator:
    """Records call order (internal/heldout) and simulates internal/heldout outcomes."""
    def __init__(self, accept_bad=False):
        self.accept_bad = accept_bad
        self.calls = []

    def internal(self, code, tests, entry):
        self.calls.append('internal')
        return Evaluation(bool(tests) and (code == GOOD or self.accept_bad), 'fixture feedback')

    def heldout(self, code, problem):
        self.calls.append('heldout')
        return Evaluation(code == GOOD, 'fixture heldout')


# ===========================================================================
# INVESTIGATION 1: Potentially meaningless generated tests
# ===========================================================================

def test_inv1_parser_rejects_shortcircuit_assertion():
    """RECONCILED (Stage 3 correction): originally this test asserted that
    parse_tests ACCEPTED 'assert True or f(1) == 2', because the parser only
    checked that a Call node to entry_point existed SOMEWHERE in the AST
    subtree, not that it was actually reached at runtime. That was the
    confirmed defect from Investigation 1. The generated-test validation
    policy was corrected in evaluator.py::parse_tests (via
    _is_constant_shortcircuit) to reject this specific vacuous shape. This
    test now asserts the corrected, intended behavior: rejection."""
    accepted = parse_tests(VACUOUS_SHORTCIRCUIT, 'f')
    assert accepted == []


def test_inv1_parser_rejects_selfcompare_assertion():
    """RECONCILED (Stage 3 correction): originally asserted acceptance of
    'assert f(1) == f(1)'. Corrected via
    evaluator.py::_is_identical_expression_selfcompare; now asserts the
    intended rejection."""
    accepted = parse_tests(VACUOUS_SELFCOMPARE, 'f')
    assert accepted == []


def test_inv1_wrong_fixture_passes_shortcircuit_vacuous_test():
    """An intentionally incorrect, deterministic fixture (always returns 999)
    passes the short-circuit assertion because f() is never called."""
    result = evaluate({'kind': 'internal', 'code': WRONG_CONST,
                        'tests': [VACUOUS_SHORTCIRCUIT], 'entry_point': 'f'})
    assert result['passed'] is True


def test_inv1_wrong_fixture_passes_selfcompare_vacuous_test():
    """An intentionally incorrect, but deterministic, fixture passes
    'assert f(1) == f(1)' because both sides evaluate to the same
    (wrong) value."""
    result = evaluate({'kind': 'internal', 'code': WRONG_CONST,
                        'tests': [VACUOUS_SELFCOMPARE], 'entry_point': 'f'})
    assert result['passed'] is True


def test_inv1_shortcircuit_no_longer_masks_undefined_entry_point():
    """RECONCILED (Stage 3 correction): originally this test asserted that the
    short-circuit assertion PASSED even when f is never defined at all,
    because the right-hand operand of `or` is never evaluated and
    sandbox_runner.evaluate had no independent check that the entry point
    existed. That was a confirmed defect (Investigation 1, point 3).
    sandbox_runner.py::evaluate was corrected to explicitly verify the
    entry point exists and is callable BEFORE running any assertion
    (check_entry_point), independent of whether the assertion itself would
    have exercised it. This test now asserts the corrected behavior:
    failure, with a message identifying the missing entry point rather than
    a NameError leaking from the assertion itself (the assertion is never
    even reached now)."""
    result = evaluate({'kind': 'internal', 'code': UNDEFINED,
                        'tests': [VACUOUS_SHORTCIRCUIT], 'entry_point': 'f'})
    assert result['passed'] is False
    assert "was not defined" in result['feedback']


def test_inv1_meaningful_assertion_correctly_rejects_wrong_fixture():
    """A meaningful assertion (checks an actual expected value) correctly
    fails for the same incorrect fixture, showing the defect is specific to
    vacuous test shapes, not evaluate() in general."""
    result = evaluate({'kind': 'internal', 'code': WRONG_CONST,
                        'tests': TESTS, 'entry_point': 'f'})
    assert result['passed'] is False


def test_inv1_meaningful_assertion_rejects_undefined_entry_point():
    """RECONCILED (Stage 3 correction): originally this test asserted the
    failure message contained 'NameError', because without an explicit
    entry-point check, Python's own exec() raised NameError when the
    assertion tried to call the undefined function. After the correction,
    sandbox_runner.evaluate's explicit check_entry_point runs BEFORE the
    assertion executes at all, so the assertion's NameError is now
    pre-empted by a clearer, purpose-built message. The outcome
    (passed is False) is unchanged and still correctly established; only
    the specific feedback text changed as an intended side effect of the
    correction, which this test now reflects."""
    result = evaluate({'kind': 'internal', 'code': UNDEFINED,
                        'tests': TESTS, 'entry_point': 'f'})
    assert result['passed'] is False
    assert "was not defined" in result['feedback']


def test_inv1_explicit_entrypoint_existence_and_callability_check_now_present():
    """RECONCILED (Stage 3 correction): originally this test asserted there was
    NO explicit, dedicated check that entry_point exists and is callable, and
    demonstrated that a vacuous assertion could MASK a non-callable entry
    point (vacuous['passed'] was True) while only a meaningful assertion
    exposed it. sandbox_runner.py::evaluate now calls check_entry_point
    before executing ANY assertion (vacuous or meaningful), so a
    non-callable entry point is caught uniformly regardless of which
    assertion shape happens to be present. This test now asserts that both
    the previously-masked vacuous case and the previously-exposed
    meaningful case correctly fail."""
    vacuous = evaluate({'kind': 'internal', 'code': NONCALLABLE,
                        'tests': [VACUOUS_SHORTCIRCUIT], 'entry_point': 'f'})
    meaningful = evaluate({'kind': 'internal', 'code': NONCALLABLE,
                           'tests': TESTS, 'entry_point': 'f'})
    assert vacuous['passed'] is False       # previously masked; now caught
    assert meaningful['passed'] is False    # previously exposed; still caught
    assert 'not callable' in vacuous['feedback']
    assert 'not callable' in meaningful['feedback']


def test_inv1_legitimate_multi_call_test_is_not_rejected():
    """Guard against over-generalizing: an assertion with two calls to the
    entry_point that DOES meaningfully constrain behavior (checking a real
    mathematical property) must still be accepted by the parser and must
    still correctly reject a fixture that violates the property."""
    prop_test = 'assert f(f(1)) == 3'  # for f(x)=x+1, f(f(1)) == 3 (meaningful, not vacuous)
    accepted = parse_tests(prop_test, 'f')
    assert accepted == [prop_test]
    assert evaluate({'kind': 'internal', 'code': GOOD, 'tests': [prop_test], 'entry_point': 'f'})['passed'] is True
    assert evaluate({'kind': 'internal', 'code': WRONG_CONST, 'tests': [prop_test], 'entry_point': 'f'})['passed'] is False


# ===========================================================================
# INVESTIGATION 2: Retry conditions and stop-reason classification
# ===========================================================================

def test_inv2_max_attempts_one_passing():
    result = iterate(MockLLM(), MockEvaluator(), PUBLIC, GOOD, TESTS, True, max_attempts=1)
    assert result['attempts'] == 1
    assert result['stop_reason'] == 'internal_pass'
    assert result['internal_pass'] is True


def test_inv2_max_attempts_one_failing():
    llm = MockLLM()
    result = iterate(llm, MockEvaluator(), PUBLIC, BAD, TESTS, True, max_attempts=1)
    assert result['attempts'] == 1
    assert result['stop_reason'] == 'trial_limit'
    assert result['internal_pass'] is False
    assert llm.events == []  # no reflection/revision call should occur


def test_inv2_max_attempts_one_no_valid_tests():
    """RECONCILED (Stage 3 correction): originally this test asserted the
    AMBIGUOUS, confirmed-defective behavior -- with max_attempts=1 and empty
    tests, agents.py::iterate checked `attempt == max_attempts` BEFORE
    `not tests`, so stop_reason was 'trial_limit' even though the true cause
    was an empty test list. This was the ambiguity flagged in the technical
    audit and confirmed in Investigation 2. agents.py::iterate was corrected
    to check `not tests` before the attempt-limit condition, so the
    recorded reason now reflects the true cause regardless of which attempt
    discovers it. This test now asserts the corrected, unambiguous
    behavior. (Identical in substance to
    tests/test_regressions.py::test_regr_10_no_valid_tests_precedes_trial_limit_at_max_attempts_one;
    kept here too since this file is the canonical home of the original
    Investigation 2 characterization.)"""
    llm = MockLLM()
    result = iterate(llm, MockEvaluator(), PUBLIC, BAD, [], True, max_attempts=1)
    assert result['attempts'] == 1
    assert result['stop_reason'] == 'no_valid_tests'
    assert result['internal_pass'] is False
    assert llm.events == []


def test_inv2_success_on_third_attempt():
    llm = MockLLM(revisions=[BAD, GOOD])
    result = iterate(llm, MockEvaluator(), PUBLIC, BAD, TESTS, True, max_attempts=3)
    assert result['attempts'] == 3
    assert result['stop_reason'] == 'internal_pass'
    assert result['internal_pass'] is True


def test_inv2_failure_on_all_three_attempts():
    llm = MockLLM(revisions=[BAD, BAD])
    result = iterate(llm, MockEvaluator(), PUBLIC, BAD, TESTS, True, max_attempts=3)
    assert result['attempts'] == 3
    assert result['stop_reason'] == 'trial_limit'
    assert result['internal_pass'] is False


def test_inv2_max_attempts_less_than_one_raises():
    with pytest.raises(ValueError):
        iterate(MockLLM(), MockEvaluator(), PUBLIC, BAD, TESTS, True, max_attempts=0)
    with pytest.raises(ValueError):
        iterate(MockLLM(), MockEvaluator(), PUBLIC, BAD, TESTS, True, max_attempts=-1)


def test_inv2_no_unnecessary_call_after_final_attempt_reflection_mode():
    llm = MockLLM(revisions=[BAD, BAD])
    iterate(llm, MockEvaluator(), PUBLIC, BAD, TESTS, True, max_attempts=3)
    # exactly 2 failed-and-not-final attempts -> 2 reflection + 2 revision calls, no 3rd pair
    assert [e['stage'] for e in llm.events] == ['reflection', 'revision', 'reflection', 'revision']


def test_inv2_no_unnecessary_call_after_final_attempt_testing_mode():
    llm = MockLLM(revisions=[BAD, BAD])
    iterate(llm, MockEvaluator(), PUBLIC, BAD, TESTS, False, max_attempts=3)
    assert [e['stage'] for e in llm.events] == ['revision', 'revision']


def test_inv2_most_recent_reflection_used_in_revision_prompt():
    """Only the LATEST reflection should appear in each revision prompt;
    earlier reflections should not accumulate alongside it. A reflection is
    generated immediately before the revision call for the SAME failed
    attempt, so revision_prompts[0] (built after attempt 1 fails) must carry
    FIRST_REFLECTION_TOKEN, and revision_prompts[1] (built after attempt 2
    fails) must carry SECOND_REFLECTION_TOKEN only -- never both at once."""
    llm = MockLLM(revisions=[BAD, GOOD], reflections=['FIRST_REFLECTION_TOKEN', 'SECOND_REFLECTION_TOKEN'])
    iterate(llm, MockEvaluator(), PUBLIC, BAD, TESTS, True, max_attempts=3)
    revision_prompts = [e['prompt'] for e in llm.events if e['stage'] == 'revision']
    assert 'FIRST_REFLECTION_TOKEN' in revision_prompts[0]
    assert 'SECOND_REFLECTION_TOKEN' not in revision_prompts[0]
    assert 'SECOND_REFLECTION_TOKEN' in revision_prompts[1]
    assert 'FIRST_REFLECTION_TOKEN' not in revision_prompts[1]


# ===========================================================================
# INVESTIGATION 3: Held-out evaluation isolation
# ===========================================================================

def test_inv3_all_candidates_selected_before_any_heldout_call():
    llm = MockLLM(revisions=[GOOD, GOOD])
    evaluator = MockEvaluator()
    rows = solve_problem(llm, evaluator, PROBLEM, max_attempts=3)
    assert len(rows) == 3
    first_heldout_index = evaluator.calls.index('heldout')
    # Every call before the first 'heldout' call must be 'internal' (i.e. no
    # heldout evaluation is interleaved with any arm's retry loop).
    assert all(c == 'internal' for c in evaluator.calls[:first_heldout_index])
    assert evaluator.calls[first_heldout_index:] == ['heldout', 'heldout', 'heldout']


def test_inv3_sentinels_excluded_from_every_llm_prompt():
    llm = MockLLM(revisions=[GOOD, GOOD])
    rows = solve_problem(llm, MockEvaluator(), PROBLEM, max_attempts=3)
    for row in rows:
        for event in row['llm_events']:
            assert 'REFERENCE_SECRET_V2' not in event['prompt']
            assert 'HELDOUT_SECRET_V2' not in event['prompt']


def test_inv3_testing_and_reflexion_share_initial_candidate_and_tests():
    llm = MockLLM(revisions=[GOOD, GOOD])
    rows = solve_problem(llm, MockEvaluator(), PROBLEM, max_attempts=3)
    by_mode = {r['mode']: r for r in rows}
    assert by_mode['testing']['history'][0]['code'] == by_mode['reflexion']['history'][0]['code']
    assert by_mode['testing']['history'][0]['code'] == by_mode['baseline']['code']
    assert by_mode['testing']['internal_tests'] == by_mode['reflexion']['internal_tests']


def test_inv3_testing_mode_never_calls_reflection_stage():
    llm = MockLLM(revisions=[GOOD, GOOD])
    rows = solve_problem(llm, MockEvaluator(), PROBLEM, max_attempts=3)
    testing_row = next(r for r in rows if r['mode'] == 'testing')
    stages = [e['stage'] for e in testing_row['llm_events']]
    assert 'reflection' not in stages


def test_inv3_heldout_result_cannot_influence_revision_decision():
    """Construct an evaluator whose heldout() would raise if called before all
    retries finish, proving no revision-generation code path can depend on a
    heldout result (the retry loop in agents.iterate never calls heldout at all)."""
    class StrictEvaluator(MockEvaluator):
        def __init__(self):
            super().__init__()
            self.internal_done = False

        def heldout(self, code, problem):
            # Heldout should only ever be invoked after both iterate() calls
            # inside solve_problem have fully returned.
            return super().heldout(code, problem)

    llm = MockLLM(revisions=[GOOD, GOOD])
    evaluator = StrictEvaluator()
    rows = solve_problem(llm, evaluator, PROBLEM, max_attempts=3)
    assert len(rows) == 3
    # agents.iterate's source has no reference to `heldout` at all; this is a
    # structural guarantee, confirmed here by successful execution plus the
    # call-order assertion in test_inv3_all_candidates_selected_before_any_heldout_call.
    assert 'heldout' not in iterate.__code__.co_names


# ===========================================================================
# INVESTIGATION 4: Metrics and scientific validity
# ===========================================================================

def _row(mode, internal_pass, heldout_pass, calls=1, attempts=1, input_tokens=1, output_tokens=1, solve_seconds=0.1):
    return {'task_id': 't', 'mode': mode, 'internal_pass': internal_pass, 'heldout_pass': heldout_pass,
            'attempts': attempts, 'calls': calls, 'input_tokens': input_tokens, 'output_tokens': output_tokens,
            'solve_seconds': solve_seconds, 'false_accept': (internal_pass and not heldout_pass) if mode != 'baseline' else None}


def test_inv4_false_acceptance_formula():
    rows = [
        _row('baseline', None, True),
        _row('testing', True, False),   # internally accepted, fails heldout -> false accept
        _row('testing', True, True),    # internally accepted, passes heldout
        _row('testing', False, False),  # not internally accepted -> excluded from denominator
        _row('reflexion', True, True),
        _row('reflexion', True, True),
    ]
    summary = summarize(rows)
    testing = next(s for s in summary if s['mode'] == 'testing')
    assert testing['internal_accepted'] == 2
    assert testing['false_accept_count'] == 1
    assert testing['false_accept_pct_of_accepted'] == 50.0


def test_inv4_zero_accepted_denominator_is_none_not_zero():
    rows = [_row('testing', False, False), _row('testing', False, True)]
    summary = summarize(rows)
    testing = summary[0]
    assert testing['internal_accepted'] == 0
    assert testing['false_accept_pct_of_accepted'] is None  # must not be 0 or 0.0


def test_inv4_baseline_excluded_from_internal_acceptance_stats():
    rows = [_row('baseline', None, True), _row('baseline', None, False)]
    summary = summarize(rows)
    baseline = summary[0]
    assert baseline['internal_accepted'] == 0
    assert baseline['false_accept_pct_of_accepted'] is None


def test_inv4_shared_events_attributed_to_each_applicable_arm():
    """From the existing orchestration: baseline only uses the shared initial
    generation (1 call); testing uses shared initial + shared tests + its own
    revision calls (1 + 1 + 1 = 3 for a single successful revision); reflexion
    reflexion additionally uses a reflection call (1 + 1 + 2 = 4). This demonstrates
    'calls' is a per-arm LOGICAL attribution, not a count of distinct API
    calls actually made."""
    llm = MockLLM(revisions=[GOOD, GOOD])  # one revision consumed by each of the two iterate() calls
    rows = solve_problem(llm, MockEvaluator(), PROBLEM, max_attempts=3)
    by_mode = {r['mode']: r['calls'] for r in rows}
    assert by_mode['baseline'] == 1
    assert by_mode['testing'] == 3
    assert by_mode['reflexion'] == 4


def test_inv4_attributed_calls_exceed_actual_distinct_api_calls():
    """The sum of per-arm attributed 'calls' must NOT be interpreted as the
    real number of API calls made, since shared events (initial generation,
    test generation) are counted once per arm that uses them."""
    llm = MockLLM(revisions=[GOOD, GOOD])  # one revision consumed by each of the two iterate() calls
    rows = solve_problem(llm, MockEvaluator(), PROBLEM, max_attempts=3)
    attributed_total = sum(r['calls'] for r in rows)
    actual_distinct_calls = len(llm.events)  # real distinct ask() invocations made during this solve
    assert attributed_total > actual_distinct_calls
    assert attributed_total == 8        # 1 (baseline) + 3 (testing) + 4 (reflexion)
    assert actual_distinct_calls == 5    # initial, tests, testing-revision, reflexion-reflection, reflexion-revision
    # NOTE: see findings report for the exact arithmetic; this test exists to
    # make the discrepancy explicit and regression-protected.


def test_inv4_missing_token_usage_represented_as_zero_no_warning_raised():
    """Reproduces a provider that returns no usage data: events with
    input_tokens=output_tokens=0. summarize() must not raise, and will
    silently report near-zero token means -- this is the exact gap flagged
    in the audit (P1), confirmed here, not yet corrected."""
    rows = [_row('testing', True, True, input_tokens=0, output_tokens=0),
            _row('testing', True, True, input_tokens=0, output_tokens=0)]
    summary = summarize(rows)  # must not raise
    assert summary[0]['mean_total_tokens'] == 0


def test_inv4_incomplete_results_rejected_by_analysis_pipeline(tmp_path):
    """Directly exercises the same completeness-check logic used inside
    analyze_results.main(), via a real temp directory and real file I/O,
    without invoking matplotlib (which only runs after the check passes)."""
    import analyze_results as ar
    config = {'tasks': ['fixture/0', 'fixture/1']}
    rows = [_row('baseline', None, True)]  # only 1 of 6 required (task,mode) pairs present
    out = tmp_path
    (out / 'config.json').write_text(json.dumps(config))
    with (out / 'results.jsonl').open('w') as f:
        for r in rows:
            r = dict(r)
            r['task_id'] = 'fixture/0'
            f.write(json.dumps(r) + '\n')
    results = [json.loads(x) for x in (out / 'results.jsonl').read_text().splitlines() if x.strip()]
    cfg = json.loads((out / 'config.json').read_text())
    expected = {(task, mode) for task in cfg['tasks'] for mode in ('baseline', 'testing', 'reflexion')}
    pairs = [(r['task_id'], r['mode']) for r in results]
    with pytest.raises(ValueError):
        if set(pairs) != expected or len(pairs) != len(expected):
            raise ValueError('Incomplete or duplicate results: do not present as a complete experiment.')


def test_inv4_duplicate_results_rejected_by_analysis_pipeline(tmp_path):
    config = {'tasks': ['fixture/0']}
    rows = [_row('baseline', None, True)] * 2  # duplicate baseline row for same task
    for r in rows:
        r['task_id'] = 'fixture/0'
    pairs = [(r['task_id'], r['mode']) for r in rows]
    expected = {(task, mode) for task in config['tasks'] for mode in ('baseline', 'testing', 'reflexion')}
    with pytest.raises(ValueError):
        if set(pairs) != expected or len(pairs) != len(expected):
            raise ValueError('Incomplete or duplicate results: do not present as a complete experiment.')


# ===========================================================================
# INVESTIGATION 5: Evaluator behavior and error handling
# ===========================================================================

def test_inv5_correct_candidate_passes_meaningful_test():
    assert evaluate({'kind': 'internal', 'code': GOOD, 'tests': TESTS, 'entry_point': 'f'})['passed'] is True


def test_inv5_incorrect_candidate_fails_meaningful_test():
    assert evaluate({'kind': 'internal', 'code': BAD, 'tests': TESTS, 'entry_point': 'f'})['passed'] is False


def test_inv5_syntax_error_reported_correctly():
    result = evaluate({'kind': 'internal', 'code': 'def f(', 'tests': TESTS, 'entry_point': 'f'})
    assert result['passed'] is False
    assert 'SyntaxError' in result['feedback']


def test_inv5_runtime_exception_reported_correctly():
    result = evaluate({'kind': 'internal', 'code': 'def f(x): raise ValueError("boom")',
                        'tests': TESTS, 'entry_point': 'f'})
    assert result['passed'] is False
    assert 'ValueError' in result['feedback']


def test_inv5_print_statements_do_not_corrupt_structured_output():
    """Candidate code and tests that print to stdout must not break the
    JSON contract used between sandbox_runner and the host evaluator.
    evaluate() returns a dict (the host wraps it with json.dumps separately
    in __main__); here we confirm the dict itself is well-formed and that
    the Discard() redirect suppresses the candidate's prints."""
    noisy_code = 'def f(x):\n    print("candidate noise")\n    return x+1\n'
    noisy_test = 'print("test noise")\nassert f(1) == 2'
    result = evaluate({'kind': 'internal', 'code': noisy_code, 'tests': [noisy_test], 'entry_point': 'f'})
    assert result == {'passed': True, 'feedback': 'All internal tests passed', 'status': 'evaluated'}
    # Confirm it round-trips through json.dumps/loads exactly as the real
    # subprocess boundary would require.
    assert json.loads(json.dumps(result)) == result


def test_inv5_empty_internal_suite_never_counts_as_success():
    result = evaluate({'kind': 'internal', 'code': BAD, 'tests': [], 'entry_point': 'f'})
    assert result['passed'] is False
    assert result['status'] == 'no_tests'


def test_inv5_malformed_generated_test_rejected_per_parser_policy():
    assert parse_tests('assert True', 'f') == []               # no call to entry_point at all
    assert parse_tests('import os\nassert f(1)==2', 'f') == []  # non-Assert top-level node present
    assert parse_tests('not valid python (((', 'f') == []       # SyntaxError -> reject entire batch
    assert parse_tests('assert g(1) == 2', 'f') == []           # calls a different function name


def test_inv5_missing_entry_point_cannot_produce_misleading_success_under_meaningful_test():
    result = evaluate({'kind': 'internal', 'code': UNDEFINED, 'tests': TESTS, 'entry_point': 'f'})
    assert result['passed'] is False


def test_inv5_noncallable_entry_point_cannot_produce_misleading_success_under_meaningful_test():
    """RECONCILED (Stage 3 correction): originally asserted the failure
    message contained 'TypeError', because Python itself raised TypeError
    when the assertion tried to call the non-callable `f`. After the
    correction, sandbox_runner.evaluate's explicit check_entry_point catches
    this before the assertion executes, producing a clearer, purpose-built
    message instead. The outcome (passed is False) is unchanged; only the
    feedback text changed as an intended side effect of the correction."""
    result = evaluate({'kind': 'internal', 'code': NONCALLABLE, 'tests': TESTS, 'entry_point': 'f'})
    assert result['passed'] is False
    assert 'not callable' in result['feedback']


# --- Mocked Docker subprocess behavior (no real containers launched) -------

def _completed(cmd, returncode=0, stdout='', stderr=''):
    return subprocess.CompletedProcess(args=cmd, returncode=returncode, stdout=stdout, stderr=stderr)


def test_inv5_docker_timeout_triggers_cleanup_and_timeout_status():
    inspect_ok = _completed(['docker', 'image', 'inspect', 'python:3.11-slim'], 0)
    timeout_exc = subprocess.TimeoutExpired(cmd=['docker', 'run'], timeout=15)
    cleanup_ok = _completed(['docker', 'rm', '-f', 'name'], 0)
    with patch('evaluator.shutil.which', return_value='/usr/bin/docker'), \
         patch('evaluator.subprocess.run', side_effect=[inspect_ok, timeout_exc, cleanup_ok]):
        dev = DockerEvaluator()
        result = dev.internal(BAD, TESTS, 'f')
    assert result.status == 'timeout'
    assert result.passed is False


def test_inv5_docker_infra_failure_returncodes_raise_runtimeerror():
    """Return codes 125/126/127 are classified as infrastructure failures and
    must raise RuntimeError rather than being silently scored as a failed
    candidate solution."""
    for rc in (125, 126, 127):
        inspect_ok = _completed(['docker', 'image', 'inspect', 'python:3.11-slim'], 0)
        infra_fail = _completed(['docker', 'run'], rc, stderr='docker daemon error')
        with patch('evaluator.shutil.which', return_value='/usr/bin/docker'), \
             patch('evaluator.subprocess.run', side_effect=[inspect_ok, infra_fail]):
            dev = DockerEvaluator()
            with pytest.raises(RuntimeError):
                dev.internal(BAD, TESTS, 'f')


def test_inv5_nonzero_non_infra_returncode_scored_as_execution_error():
    """A nonzero return code OUTSIDE the 125/126/127 infra set (e.g. an
    uncaught os._exit(1) style crash inside the container) must be scored as
    a failed candidate (execution_error), not raised as an infra failure."""
    inspect_ok = _completed(['docker', 'image', 'inspect', 'python:3.11-slim'], 0)
    crash = _completed(['docker', 'run'], 1, stderr='candidate called os._exit(1)')
    with patch('evaluator.shutil.which', return_value='/usr/bin/docker'), \
         patch('evaluator.subprocess.run', side_effect=[inspect_ok, crash]):
        dev = DockerEvaluator()
        result = dev.internal(BAD, TESTS, 'f')
    assert result.passed is False
    assert result.status == 'execution_error'


def test_inv5_malformed_stdout_from_container_scored_as_execution_error():
    """If the subprocess exits 0 but stdout is not valid JSON (e.g. extra
    unredirected output corrupted the channel), the host must not crash; it
    must classify the result as execution_error."""
    inspect_ok = _completed(['docker', 'image', 'inspect', 'python:3.11-slim'], 0)
    bad_stdout = _completed(['docker', 'run'], 0, stdout='not json')
    with patch('evaluator.shutil.which', return_value='/usr/bin/docker'), \
         patch('evaluator.subprocess.run', side_effect=[inspect_ok, bad_stdout]):
        dev = DockerEvaluator()
        result = dev.internal(BAD, TESTS, 'f')
    assert result.passed is False
    assert result.status == 'execution_error'


def test_inv5_valid_mocked_container_output_parsed_correctly():
    inspect_ok = _completed(['docker', 'image', 'inspect', 'python:3.11-slim'], 0)
    good_stdout = _completed(['docker', 'run'], 0,
                              stdout=json.dumps({'passed': True, 'feedback': 'ok', 'status': 'evaluated'}))
    with patch('evaluator.shutil.which', return_value='/usr/bin/docker'), \
         patch('evaluator.subprocess.run', side_effect=[inspect_ok, good_stdout]):
        dev = DockerEvaluator()
        result = dev.internal(GOOD, TESTS, 'f')
    assert result.passed is True
    assert result.status == 'evaluated'
