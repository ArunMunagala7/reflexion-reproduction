"""
Regression tests expressing the INTENDED corrected behavior (Stage 3).

These tests are written BEFORE the corresponding production corrections and
are first run against the unchanged implementation (commit 9050f0c) to
produce honest "red" evidence in verification/copilot_regression_before.txt.
After the approved corrections are implemented, the same file is re-run
(unmodified) to produce "green" evidence in
verification/copilot_regression_after.txt.

Only fixed, trusted fixture strings and a locally-faked OpenAI client are
used. No real network/API calls are made, no credentials are read from a
real account (OPENAI_API_KEY is set to a dummy placeholder string purely to
satisfy the constructor's `os.environ[...]` lookup), and no untrusted
model-generated code is executed outside of the existing
`sandbox_runner.evaluate` function.
"""
import os
import sys
import types
from unittest.mock import patch

import pytest

from evaluator import parse_tests
from sandbox_runner import evaluate
from agents import iterate
from types import SimpleNamespace


GOOD = 'def f(x): return x+1'
BAD = 'def f(x): return x'
UNDEFINED = 'x = 1\n'
NONCALLABLE = 'f = 5\n'
TESTS = ['assert f(1) == 2']
VACUOUS_SHORTCIRCUIT = 'assert True or f(1) == 2'
VACUOUS_SELFCOMPARE = 'assert f(1) == f(1)'

PUBLIC = {'task_id': 'fixture/0', 'prompt': 'def f(x):\n    """Return x+1."""', 'entry_point': 'f'}


class MockEvaluator:
    def internal(self, code, tests, entry):
        from evaluator import Evaluation
        return Evaluation(bool(tests) and code == GOOD, 'fixture feedback')

    def heldout(self, code, problem):
        from evaluator import Evaluation
        return Evaluation(code == GOOD, 'fixture heldout')


class MockLLM:
    def __init__(self):
        self.events = []

    def ask(self, stage, prompt):
        text = {'reflection': 'use x+1', 'revision': BAD}.get(stage, BAD)
        self.events.append({'stage': stage, 'prompt': prompt, 'response': text,
                             'input_tokens': 1, 'output_tokens': 1, 'seconds': 0})
        return SimpleNamespace(text=text)


# ===========================================================================
# 1-5: parse_tests validation policy
# ===========================================================================

def test_regr_01_vacuous_constant_shortcircuit_rejected():
    """Generated-test policy must reject an assertion that trivially succeeds
    via a constant short-circuit operand (e.g. `True or ...`), since the
    right-hand call to entry_point is never evaluated."""
    assert parse_tests(VACUOUS_SHORTCIRCUIT, 'f') == []


def test_regr_02_identical_expression_selfcompare_rejected():
    """Generated-test policy must reject an assertion comparing two
    syntactically identical calls to entry_point (`f(1) == f(1)`), since it
    passes for any deterministic function regardless of correctness."""
    assert parse_tests(VACUOUS_SELFCOMPARE, 'f') == []


def test_regr_03_ordinary_meaningful_assertion_still_accepted():
    assert parse_tests('assert f(1) == 2', 'f') == ['assert f(1) == 2']


def test_regr_04_property_based_different_inputs_not_rejected():
    """A legitimate property test calling entry_point twice with DIFFERENT
    inputs/expressions (not syntactically identical) must remain accepted."""
    prop_test = 'assert f(f(1)) == 3'
    assert parse_tests(prop_test, 'f') == [prop_test]


def test_regr_05_valid_nested_comparison_not_accidentally_rejected():
    """A nested/compound expression combining two distinct calls to
    entry_point must remain accepted -- only syntactically IDENTICAL
    comparison sides should be rejected, not every multi-call expression.
    Note: parse_tests round-trips source through ast.unparse, which drops
    redundant parentheses; the expected string reflects that normalization,
    not a behavioral defect."""
    nested_test = 'assert (f(1) + f(2)) == 5'
    assert parse_tests(nested_test, 'f') == ['assert f(1) + f(2) == 5']


# ===========================================================================
# 6-9: sandbox_runner entry-point validation
# ===========================================================================

def test_regr_06_missing_entry_point_cannot_pass_internal_evaluation():
    """Even if a vacuous-shaped test string reaches evaluate() directly
    (bypassing parse_tests, as a defense-in-depth check), a candidate that
    never defines entry_point must not be scored as passed."""
    result = evaluate({'kind': 'internal', 'code': UNDEFINED,
                        'tests': [VACUOUS_SHORTCIRCUIT], 'entry_point': 'f'})
    assert result['passed'] is False


def test_regr_07_noncallable_entry_point_cannot_pass_internal_evaluation():
    result = evaluate({'kind': 'internal', 'code': NONCALLABLE,
                        'tests': [VACUOUS_SHORTCIRCUIT], 'entry_point': 'f'})
    assert result['passed'] is False


def test_regr_08_valid_callable_entry_point_remains_supported():
    result = evaluate({'kind': 'internal', 'code': GOOD, 'tests': TESTS, 'entry_point': 'f'})
    assert result['passed'] is True


def test_regr_09_empty_suite_cannot_establish_success():
    result = evaluate({'kind': 'internal', 'code': BAD, 'tests': [], 'entry_point': 'f'})
    assert result['passed'] is False
    assert result['status'] == 'no_tests'


# ===========================================================================
# 10: stop-reason precedence
# ===========================================================================

def test_regr_10_no_valid_tests_precedes_trial_limit_at_max_attempts_one():
    """With max_attempts=1 and an empty test list, the intended stop_reason
    is 'no_valid_tests' (the true cause), not 'trial_limit'."""
    llm = MockLLM()
    result = iterate(llm, MockEvaluator(), PUBLIC, BAD, [], True, max_attempts=1)
    assert result['attempts'] == 1
    assert result['stop_reason'] == 'no_valid_tests'
    assert llm.events == []  # no reflection/revision call should occur


# ===========================================================================
# 11: missing token usage must be distinguishable from measured zero
# ===========================================================================

class _FakeUsage:
    """Simulates a provider response that returns NO usage block at all."""
    pass


class _FakeChoice:
    def __init__(self, content):
        self.message = SimpleNamespace(content=content)
        self.finish_reason = 'stop'


class _FakeCompletionResultNoUsage:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]
        self.usage = None  # provider did not return usage data
        self.model = 'fixture-model'


class _FakeCompletions:
    def create(self, **kwargs):
        return _FakeCompletionResultNoUsage('fixture response')


class _FakeChat:
    def __init__(self):
        self.completions = _FakeCompletions()


class _FakeOpenAIClient:
    def __init__(self, *a, **k):
        self.chat = _FakeChat()


def test_regr_11_missing_token_usage_marked_unavailable_not_zero():
    """When the provider returns no usage block, the logged event must
    explicitly distinguish 'usage unavailable' from 'measured zero tokens'.
    Pre-correction, llm.py unconditionally records 0 for both input and
    output tokens with no distinguishing flag; this test is expected to FAIL
    against the unmodified implementation and PASS after the correction."""
    with patch.dict(os.environ, {'OPENAI_API_KEY': 'dummy-not-a-real-key'}, clear=False), \
         patch('openai.OpenAI', _FakeOpenAIClient):
        import importlib
        import llm as llm_module
        importlib.reload(llm_module)  # ensure patched OpenAI is picked up
        instance = llm_module.LLM('fixture-model')
        instance.ask('initial_code', 'irrelevant prompt')
        event = instance.events[-1]
        # The correction must expose an explicit availability indicator
        # (e.g. event['tokens_available'] is False, or tokens are None)
        # rather than silently recording measured 0 usage.
        tokens_available = event.get('tokens_available', 'MISSING_KEY')
        assert tokens_available is False or (
            event['input_tokens'] is None and event['output_tokens'] is None
        ), (
            "Expected an explicit unavailable-usage indicator (tokens_available=False, "
            "or input/output tokens set to None) when the provider returns no usage block; "
            f"got event={event}"
        )
