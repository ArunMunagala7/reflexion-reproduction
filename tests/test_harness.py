from types import SimpleNamespace
from dataset import select, public_problem
from evaluator import Evaluation, parse_tests
from agents import iterate
from experiment import solve_problem
from analyze_results import summarize
from sandbox_runner import evaluate

PROBLEM = {'task_id':'fixture/0','prompt':'def f(x):\n    """Return x+1."""','entry_point':'f',
           'canonical_solution':'REFERENCE_SECRET', 'test':'HELDOUT_SECRET'}
PUBLIC = public_problem(PROBLEM)
BAD = 'def f(x): return x'
GOOD = 'def f(x): return x+1'
TESTS = ['assert f(1) == 2']

class MockLLM:
    def __init__(self, revisions=()):
        self.revisions = iter(revisions)
        self.events = []
    def ask(self, stage, prompt):
        text = {'initial_code': BAD, 'generate_tests': TESTS[0], 'reflection': 'Use x+1'}.get(stage)
        if stage == 'revision':
            text = next(self.revisions)
        self.events.append({'stage':stage,'prompt':prompt,'response':text,'input_tokens':1,'output_tokens':1,'seconds':0})
        return SimpleNamespace(text=text)

class MockEvaluator:
    def __init__(self, accept_bad=False):
        self.accept_bad = accept_bad
        self.calls = []
    def internal(self, code, tests, entry):
        self.calls.append('internal')
        return Evaluation(bool(tests) and (code == GOOD or self.accept_bad),'fixture feedback')
    def heldout(self, code, problem):
        self.calls.append('heldout')
        return Evaluation(code == GOOD,'fixture heldout')


def test_early_success_no_calls():
    llm = MockLLM()
    result = iterate(llm,MockEvaluator(),PUBLIC,GOOD,TESTS,True)
    assert result['attempts'] == 1 and result['stop_reason'] == 'internal_pass'
    assert llm.events == []


def test_second_attempt_stops():
    llm = MockLLM([GOOD])
    result = iterate(llm,MockEvaluator(),PUBLIC,BAD,TESTS,True)
    assert result['attempts'] == 2
    assert [e['stage'] for e in llm.events] == ['reflection','revision']


def test_all_fail_stops_at_three():
    llm = MockLLM([BAD,BAD])
    result = iterate(llm,MockEvaluator(),PUBLIC,BAD,TESTS,True)
    assert result['attempts'] == 3 and result['stop_reason'] == 'trial_limit'
    assert [e['stage'] for e in llm.events] == ['reflection','revision']*2


def test_no_reflection_calls_in_testing_mode():
    llm = MockLLM([GOOD])
    iterate(llm,MockEvaluator(),PUBLIC,BAD,TESTS,False)
    assert [e['stage'] for e in llm.events] == ['revision']


def test_empty_tests_not_passed():
    result = iterate(MockLLM(),MockEvaluator(),PUBLIC,BAD,[],True)
    assert not result['internal_pass'] and result['stop_reason'] == 'no_valid_tests'


def test_shared_initial_tests_and_no_leakage():
    llm = MockLLM([GOOD,GOOD]); evaluator=MockEvaluator()
    rows = solve_problem(llm,evaluator,PROBLEM)
    assert [r['heldout_pass'] for r in rows] == [False,True,True]
    assert rows[1]['history'][0]['code'] == rows[2]['history'][0]['code'] == rows[0]['code']
    assert rows[1]['internal_tests'] == rows[2]['internal_tests']
    assert evaluator.calls[-3:] == ['heldout']*3
    for event in llm.events:
        assert 'HELDOUT_SECRET' not in event['prompt'] and 'REFERENCE_SECRET' not in event['prompt']
    assert [r['calls'] for r in rows] == [1,3,4]


def test_false_acceptance_recorded():
    rows = solve_problem(MockLLM(),MockEvaluator(True),PROBLEM)
    assert rows[0]['false_accept'] is None
    assert rows[1]['false_accept'] and rows[2]['false_accept']
    assert summarize(rows)[1]['false_accept_pct_of_accepted'] == 100


def test_sample_reproducible():
    data = [{'task_id':str(x)} for x in range(10)]
    assert select(data,4,42,['0']) == select(data,4,42,['0'])
    assert all(x['task_id'] != '0' for x in select(data,9,42,['0']))


def test_test_parser():
    assert parse_tests('```python\nassert f(1) == 2\n```','f') == TESTS
    assert not parse_tests('assert True','f')
    assert not parse_tests('import os\nassert f(1)==2','f')


def test_runner_known_correct_and_incorrect():
    # These are fixed, trusted fixtures; no model-generated code is executed on host.
    payload = {'kind':'internal','code':GOOD,'tests':TESTS,'entry_point':'f'}
    assert evaluate(payload)['passed']
    assert not evaluate({**payload,'code':BAD})['passed']
    assert not evaluate({**payload,'code':'def f(x): raise ValueError("oops")'})['passed']
    assert not evaluate({**payload,'code':'def f('})['passed']
    heldout = {'kind':'heldout','code':GOOD,'tests':'def check(candidate):\n    assert candidate(8) == 9','entry_point':'f'}
    assert evaluate(heldout)['passed']
    assert not evaluate({**heldout,'code':BAD})['passed']


def test_runner_rejects_empty_suite_even_if_called_directly():
    payload = {'kind':'internal','code':BAD,'tests':[],'entry_point':'f'}
    assert not evaluate(payload)['passed']
