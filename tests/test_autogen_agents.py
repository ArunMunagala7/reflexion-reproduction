"""Executable verification tests for Method D (AutoGen proactive review).

Uses real `autogen_agentchat` agent objects (ProgrammerAgent/ReviewerAgent)
with a scripted mock LLM standing in for the inference layer -- the
AutoGen framework classes themselves (BaseChatAgent, TextMessage,
Response, CancellationToken) are never mocked or bypassed. Only fixed,
trusted fixture code is ever passed to the real DockerEvaluator-shaped
mock; no untrusted generated code is executed on the host or in Docker
by this file.
"""
import asyncio
from types import SimpleNamespace

from autogen_agentchat.agents import BaseChatAgent
from autogen_agentchat.base import Response
from autogen_agentchat.messages import TextMessage
from autogen_core import CancellationToken

from dataset import public_problem
from evaluator import Evaluation
from analyze_results import summarize
from experiment import solve_problem
from autogen_agents import (
    ProgrammerAgent,
    ReviewerAgent,
    parse_review_response,
    run_autogen_review,
)

PROBLEM = {'task_id': 'fixture/0', 'prompt': 'def f(x):\n    """Return x+1."""', 'entry_point': 'f',
           'canonical_solution': 'REFERENCE_SECRET', 'test': 'HELDOUT_SECRET'}
PUBLIC = public_problem(PROBLEM)
BAD = 'def f(x): return x'
GOOD = 'def f(x): return x+1'
TESTS = ['assert f(1) == 2']


class MockLLM:
    """Scripted per-stage responses, consumed in order. Mirrors the
    MockLLM pattern used in tests/test_harness.py so Method D is verified
    with the same style of fixed, trusted test doubles."""

    def __init__(self, script):
        self._queues = {k: list(v) for k, v in script.items()}
        self.events = []

    def ask(self, stage, prompt):
        text = self._queues[stage].pop(0)
        self.events.append({'stage': stage, 'prompt': prompt, 'response': text,
                            'input_tokens': 1, 'output_tokens': 1, 'seconds': 0.01,
                            'tokens_available': True})
        return SimpleNamespace(text=text)


class MockEvaluator:
    """Deterministic, non-LLM, fixture-only stand-in for DockerEvaluator."""

    def __init__(self, internal_results, heldout_result=True):
        self.internal_results = list(internal_results)
        self.heldout_result = heldout_result
        self.calls = []

    def internal(self, code, tests, entry):
        self.calls.append('internal')
        passed = self.internal_results.pop(0)
        return Evaluation(bool(tests) and passed, 'fixture feedback')

    def heldout(self, code, problem):
        self.calls.append('heldout')
        return Evaluation(self.heldout_result, 'fixture heldout')


# --- 1 & 2: real AutoGen agent objects exchanging real AutoGen messages ---

def test_agents_are_real_autogen_chat_agent_instances():
    llm = MockLLM({})
    programmer = ProgrammerAgent(llm)
    reviewer = ReviewerAgent(llm)
    assert isinstance(programmer, BaseChatAgent)
    assert isinstance(reviewer, BaseChatAgent)
    assert TextMessage in programmer.produced_message_types
    assert TextMessage in reviewer.produced_message_types


def test_on_messages_uses_real_textmessage_and_response_api():
    llm = MockLLM({'autogen_programmer': [GOOD], 'autogen_reviewer': ['{"decision": "APPROVE", "feedback": "ok"}']})
    programmer = ProgrammerAgent(llm)
    reviewer = ReviewerAgent(llm)
    msg = TextMessage(content='revise please', source='controller')
    prog_response = asyncio.run(programmer.on_messages([msg], CancellationToken()))
    rev_response = asyncio.run(reviewer.on_messages([msg], CancellationToken()))
    assert isinstance(prog_response, Response) and isinstance(prog_response.chat_message, TextMessage)
    assert isinstance(rev_response, Response) and isinstance(rev_response.chat_message, TextMessage)
    assert prog_response.chat_message.content == GOOD
    assert prog_response.chat_message.source == 'programmer'
    assert rev_response.chat_message.source == 'reviewer'


# --- 3: passing tests + valid approval stops after one candidate attempt ---

def test_accept_after_one_attempt_when_tests_pass_and_reviewer_approves():
    llm = MockLLM({'autogen_reviewer': ['{"decision": "APPROVE", "feedback": "Looks correct."}']})
    evaluator = MockEvaluator([True])
    result = run_autogen_review(llm, evaluator, PUBLIC, GOOD, TESTS, max_attempts=3)
    assert result['attempts'] == 1
    assert result['stop_reason'] == 'accepted'
    assert result['workflow_accept'] is True
    assert result['internal_pass'] is True
    assert result['review_calls'] == 1


# --- 4: passing tests but reviewer criticism triggers a revision ---

def test_reviewer_requests_revision_despite_passing_tests():
    llm = MockLLM({
        'autogen_reviewer': ['{"decision": "REVISE", "feedback": "Misses the negative-input edge case."}',
                              '{"decision": "APPROVE", "feedback": "Now correct."}'],
        'autogen_programmer': [GOOD],
    })
    evaluator = MockEvaluator([True, True])
    result = run_autogen_review(llm, evaluator, PUBLIC, GOOD, TESTS, max_attempts=3)
    assert result['attempts'] == 2
    assert result['stop_reason'] == 'accepted'
    assert result['history'][0]['review_decision'] == 'REVISE'
    programmer_events = [e for e in llm.events if e['stage'] == 'autogen_programmer']
    assert len(programmer_events) == 1
    # --- 6: reviewer criticism is included in the Programmer's revision prompt ---
    assert 'Misses the negative-input edge case.' in programmer_events[0]['prompt']


# --- 5: internal failure cannot be accepted even if reviewer approves ---

def test_internal_failure_cannot_be_accepted_even_if_reviewer_approves():
    llm = MockLLM({'autogen_reviewer': ['{"decision": "APPROVE", "feedback": "Looks fine."}']})
    evaluator = MockEvaluator([False])
    result = run_autogen_review(llm, evaluator, PUBLIC, BAD, TESTS, max_attempts=1)
    assert result['stop_reason'] == 'trial_limit'
    assert result['internal_pass'] is False
    assert result['workflow_accept'] is False


# --- 7: the final candidate attempt cannot trigger another revision ---

def test_final_attempt_does_not_trigger_additional_revision_beyond_budget():
    llm = MockLLM({
        'autogen_reviewer': ['{"decision": "REVISE", "feedback": "fix a"}',
                              '{"decision": "REVISE", "feedback": "fix b"}'],
        'autogen_programmer': [BAD],
    })
    evaluator = MockEvaluator([False, False])
    result = run_autogen_review(llm, evaluator, PUBLIC, BAD, TESTS, max_attempts=2)
    assert result['attempts'] == 2
    assert result['stop_reason'] == 'trial_limit'
    assert result['workflow_accept'] is False
    assert sum(1 for e in llm.events if e['stage'] == 'autogen_programmer') == 1


# --- 8: invalid/malformed reviewer responses cannot produce approval ---

def test_invalid_reviewer_response_is_not_approval():
    llm = MockLLM({
        'autogen_reviewer': ['this is not valid JSON at all', '{"decision": "APPROVE", "feedback": "ok now"}'],
        'autogen_programmer': [GOOD],
    })
    evaluator = MockEvaluator([True, True])
    result = run_autogen_review(llm, evaluator, PUBLIC, GOOD, TESTS, max_attempts=3)
    assert result['history'][0]['review_valid'] is False
    assert result['history'][0]['review_decision'] is None
    assert result['attempts'] == 2
    assert result['stop_reason'] == 'accepted'


def test_parse_review_response_rejects_malformed_and_ambiguous_shapes():
    assert parse_review_response('not json')['valid'] is False
    assert parse_review_response('[1, 2, 3]')['valid'] is False
    assert parse_review_response('{"decision": "MAYBE", "feedback": "x"}')['valid'] is False
    assert parse_review_response('{"decision": "APPROVE"}')['valid'] is False
    assert parse_review_response('{"decision": "APPROVE", "feedback": 5}')['valid'] is False
    ok = parse_review_response('{"decision": "APPROVE", "feedback": "fine"}')
    assert ok == {'valid': True, 'decision': 'APPROVE', 'feedback': 'fine'}


# --- 9: an empty generated-test suite cannot produce workflow acceptance ---

def test_empty_test_suite_cannot_produce_workflow_acceptance():
    llm = MockLLM({})
    evaluator = MockEvaluator([False])  # internal() is still called once before the empty-tests check
    result = run_autogen_review(llm, evaluator, PUBLIC, BAD, [], max_attempts=3)
    assert result['stop_reason'] == 'no_valid_tests'
    assert result['workflow_accept'] is False
    assert llm.events == []


# --- 10: agent state does not leak between benchmark problems ---

def test_fresh_agents_hold_no_cross_problem_state():
    llm = MockLLM({})
    programmer = ProgrammerAgent(llm)
    reviewer = ReviewerAgent(llm)
    # The only state held is the shared LLM reference; no conversation
    # history, candidate cache, or review cache is stored on the agent.
    assert set(programmer.__dict__.keys()) == {'_name', '_description', '_llm'}
    assert set(reviewer.__dict__.keys()) == {'_name', '_description', '_llm'}


def test_two_sequential_problems_do_not_interfere():
    llm1 = MockLLM({'autogen_reviewer': ['{"decision": "APPROVE", "feedback": "ok"}']})
    result1 = run_autogen_review(llm1, MockEvaluator([True]), PUBLIC, GOOD, TESTS, max_attempts=3)
    llm2 = MockLLM({'autogen_reviewer': ['{"decision": "REVISE", "feedback": "bad"}',
                                          '{"decision": "APPROVE", "feedback": "better"}'],
                     'autogen_programmer': [GOOD]})
    result2 = run_autogen_review(llm2, MockEvaluator([False, True]), PUBLIC, BAD, TESTS, max_attempts=3)
    assert result1['stop_reason'] == 'accepted' and result1['attempts'] == 1
    assert result2['attempts'] == 2
    assert 'REFERENCE_SECRET' not in str(result1) and 'HELDOUT_SECRET' not in str(result1)
    assert 'REFERENCE_SECRET' not in str(result2) and 'HELDOUT_SECRET' not in str(result2)


# --- 11, 12, 13, 14, 15: integration via experiment.solve_problem ---

class SharedMockLLM:
    """A single MockLLM used across the whole solve_problem() call, so
    shared-event attribution (initial_code/generate_tests) can be
    verified end-to-end, exactly as test_harness.py does for A/B/C."""

    def __init__(self, revisions_testing, revisions_reflexion, autogen_script):
        self._pool = {'initial_code': [GOOD], 'generate_tests': [TESTS[0]], 'reflection': ['Use x+1']}
        self._revisions_testing = list(revisions_testing)
        self._revisions_reflexion = list(revisions_reflexion)
        self._autogen = {k: list(v) for k, v in autogen_script.items()}
        self.events = []
        self._revision_calls = 0

    def ask(self, stage, prompt):
        if stage == 'revision':
            # testing mode's revisions are consumed first (iterate() runs
            # testing before reflexion in experiment.solve_problem).
            if self._revisions_testing:
                text = self._revisions_testing.pop(0)
            else:
                text = self._revisions_reflexion.pop(0)
        elif stage in self._autogen:
            text = self._autogen[stage].pop(0)
        else:
            text = self._pool[stage]
            if isinstance(text, list):
                text = text[0]
        self.events.append({'stage': stage, 'prompt': prompt, 'response': text,
                            'input_tokens': 1, 'output_tokens': 1, 'seconds': 0.01,
                            'tokens_available': True})
        return SimpleNamespace(text=text)


class FullMockEvaluator:
    def __init__(self, accept_bad=False):
        self.accept_bad = accept_bad
        self.calls = []

    def internal(self, code, tests, entry):
        self.calls.append('internal')
        return Evaluation(bool(tests) and (code == GOOD or self.accept_bad), 'fixture feedback')

    def heldout(self, code, problem):
        self.calls.append('heldout')
        return Evaluation(code == GOOD, 'fixture heldout')


def test_all_four_methods_share_initial_candidate_and_tests():
    llm = SharedMockLLM([GOOD], [GOOD], {'autogen_reviewer': ['{"decision": "APPROVE", "feedback": "ok"}']})
    evaluator = FullMockEvaluator()
    rows = solve_problem(llm, evaluator, PROBLEM, max_attempts=3, autogen_fn=run_autogen_review)
    assert [r['mode'] for r in rows] == ['baseline', 'testing', 'reflexion', 'autogen']
    baseline_code = rows[0]['code']
    # 11: all four arms' first recorded candidate equals the shared initial code.
    assert rows[1]['history'][0]['code'] == baseline_code
    assert rows[2]['history'][0]['code'] == baseline_code
    assert rows[3]['history'][0]['code'] == baseline_code
    # 12: all three iterative methods received the same generated tests.
    assert rows[1]['internal_tests'] == rows[2]['internal_tests'] == rows[3]['internal_tests']
    # 13: official tests/canonical solution never leaked into any prompt.
    for event in llm.events:
        assert 'HELDOUT_SECRET' not in event['prompt']
        assert 'REFERENCE_SECRET' not in event['prompt']
    # 14: every held-out call happens only after all four candidates are frozen.
    assert evaluator.calls[-4:] == ['heldout'] * 4
    # 15: model calls / token accounting / latency are recorded for the autogen row.
    autogen_row = rows[3]
    assert autogen_row['calls'] >= 1
    assert autogen_row['input_tokens'] is not None and autogen_row['output_tokens'] is not None
    assert autogen_row['solve_seconds'] >= 0
    assert autogen_row['review_calls'] == 1


def test_workflow_false_accept_differs_from_test_false_accept_for_autogen():
    # Internal tests pass (so test-level acceptance would be True), but the
    # reviewer never approves, so workflow acceptance is False, and the
    # final candidate still fails held-out scoring due to attempt-budget
    # exhaustion -- this must NOT be reported as a workflow false acceptance,
    # because the candidate was never workflow-accepted in the first place.
    llm = SharedMockLLM([GOOD], [GOOD], {
        'autogen_reviewer': ['{"decision": "REVISE", "feedback": "a"}',
                              '{"decision": "REVISE", "feedback": "b"}',
                              '{"decision": "REVISE", "feedback": "c"}'],
        'autogen_programmer': [GOOD, GOOD],
    })
    evaluator = FullMockEvaluator(accept_bad=False)
    rows = solve_problem(llm, evaluator, PROBLEM, max_attempts=3, autogen_fn=run_autogen_review)
    autogen_row = next(r for r in rows if r['mode'] == 'autogen')
    assert autogen_row['stop_reason'] == 'trial_limit'
    assert autogen_row['workflow_accept'] is False
    assert autogen_row['false_accept'] is False  # internal_pass never True in this script path; see below
    assert autogen_row['workflow_false_accept'] is False


# --- 16 & 17: analysis pipeline validates 3- and 4-method experiments; workflow false-acceptance math ---

def _row(mode, heldout_pass, internal_pass, workflow_accept, false_accept, workflow_false_accept,
         attempts=1, calls=1, tokens_available=True, input_tokens=1, output_tokens=1, solve_seconds=0.1):
    return {'mode': mode, 'heldout_pass': heldout_pass, 'internal_pass': internal_pass,
            'workflow_accept': workflow_accept, 'false_accept': false_accept,
            'workflow_false_accept': workflow_false_accept, 'attempts': attempts, 'calls': calls,
            'tokens_available': tokens_available, 'input_tokens': input_tokens,
            'output_tokens': output_tokens, 'solve_seconds': solve_seconds}


def test_summarize_supports_four_method_experiments_and_workflow_metrics():
    rows = [
        _row('baseline', True, None, None, None, None),
        _row('testing', True, True, True, False, False),
        _row('reflexion', True, True, True, False, False),
        # Internal tests passed (test-level accepted) but reviewer withheld
        # approval: workflow NOT accepted, so it must be excluded from the
        # workflow false-acceptance denominator even though it is included
        # in the test-level false-acceptance denominator.
        _row('autogen', False, True, False, True, False),
    ]
    summary = summarize(rows, methods=['baseline', 'testing', 'reflexion', 'autogen'])
    by_mode = {s['mode']: s for s in summary}
    assert by_mode['autogen']['internal_accepted'] == 1
    assert by_mode['autogen']['false_accept_pct_of_accepted'] == 100.0
    assert by_mode['autogen']['workflow_accepted'] == 0
    assert by_mode['autogen']['workflow_false_accept_pct_of_workflow_accepted'] is None
    assert by_mode['baseline']['internal_accepted'] == 0


def test_summarize_zero_workflow_accepted_denominator_is_none_not_zero():
    rows = [_row('autogen', False, True, False, True, False)]
    summary = summarize(rows, methods=['autogen'])
    assert summary[0]['workflow_accepted'] == 0
    assert summary[0]['workflow_false_accept_pct_of_workflow_accepted'] is None


def test_summarize_backward_compatible_with_three_method_result_files():
    # Older result rows have no 'workflow_accept'/'workflow_false_accept'
    # keys at all; summarize() must fall back to internal_pass/false_accept.
    old_row = {'mode': 'reflexion', 'heldout_pass': True, 'internal_pass': True, 'false_accept': False,
               'attempts': 2, 'calls': 3, 'tokens_available': True, 'input_tokens': 10,
               'output_tokens': 10, 'solve_seconds': 1.0}
    summary = summarize([old_row], methods=['reflexion'])
    assert summary[0]['workflow_accepted'] == 1
    assert summary[0]['workflow_false_accept_count'] == 0
