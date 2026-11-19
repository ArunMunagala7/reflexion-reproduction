"""Scripted end-to-end integration for the four-method comparison.

Real components: experiment.solve_problem, autogen_agents.run_autogen_review
(real AutoGen agents), evaluator.DockerEvaluator (real disposable containers),
agents.iterate, analyze_results.py. Mocked: ONLY the LLM responses, which are
scripted per stage. No network, no paid API, no benchmark.

Skipped (not failed) if Docker or python:3.11-slim is unavailable.
"""
import csv
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import experiment
from autogen_agents import run_autogen_review
from evaluator import DockerEvaluator

ROOT = Path(__file__).resolve().parents[1]

DOCKER_AVAILABLE = shutil.which('docker') is not None
if DOCKER_AVAILABLE:
    try:
        subprocess.run(['docker', 'image', 'inspect', 'python:3.11-slim'],
                       check=True, capture_output=True, timeout=10)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        DOCKER_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not DOCKER_AVAILABLE, reason='Docker or python:3.11-slim not available')

# Synthetic balanced-parentheses problem (not from HumanEval).
BAD = ("def balanced(s):\n"
       "    return s.count('(') == s.count(')')\n")
GOOD = ("def balanced(s):\n"
        "    depth = 0\n"
        "    for ch in s:\n"
        "        if ch == '(':\n"
        "            depth += 1\n"
        "        elif ch == ')':\n"
        "            depth -= 1\n"
        "            if depth < 0:\n"
        "                return False\n"
        "    return depth == 0\n")
SHARED_TESTS_TEXT = 'assert balanced("()") == True\nassert balanced("(())") == True\n'
REVIEW_REVISE = '{"decision": "REVISE", "feedback": "Counting does not check order; \')(\' must fail."}'
REVIEW_APPROVE = '{"decision": "APPROVE", "feedback": "Looks correct."}'

PROBLEM = {
    'task_id': 'synthetic/balanced',
    'prompt': 'def balanced(s):\n    """Return True if parentheses in s are balanced."""\n',
    'entry_point': 'balanced',
    'canonical_solution': GOOD,
    'test': ('def check(candidate):\n'
             '    assert candidate("()") == True\n'
             '    assert candidate("(())") == True\n'
             '    assert candidate(")(") == False\n'
             '    assert candidate("(()") == False\n'),
}


class ScriptedLLM:
    """Mock of llm.LLM: same ask(stage, prompt) -> .text and .events contract."""

    def __init__(self, script):
        self.script = {k: list(v) for k, v in script.items()}
        self.events = []
        self.stages = []

    def ask(self, stage, prompt):
        self.stages.append(stage)
        queue = self.script.get(stage)
        if not queue:
            raise AssertionError(f'unscripted LLM stage called: {stage}')
        text = queue.pop(0)
        self.events.append({'stage': stage, 'input_tokens': 10, 'output_tokens': 5,
                            'seconds': 0.0, 'tokens_available': True})
        return SimpleNamespace(text=text, input_tokens=10, output_tokens=5,
                               seconds=0.0, tokens_available=True)


class RecordingEvaluator:
    """Wraps the real DockerEvaluator to record the order of checks."""

    def __init__(self, inner):
        self.inner = inner
        self.calls = []

    def internal(self, code, tests, entry_point):
        result = self.inner.internal(code, tests, entry_point)
        self.calls.append(('internal', code, result.passed))
        return result

    def heldout(self, code, problem):
        result = self.inner.heldout(code, problem)
        self.calls.append(('heldout', code, result.passed))
        return result


@pytest.fixture(scope='module')
def real_evaluator():
    return DockerEvaluator(timeout=20)


def _script():
    return {
        'initial_code': [BAD],
        'generate_tests': [SHARED_TESTS_TEXT],
        'autogen_reviewer': [REVIEW_REVISE, REVIEW_APPROVE],
        'autogen_programmer': [GOOD],
    }


def test_four_method_scripted_run(real_evaluator, tmp_path):
    llm = ScriptedLLM(_script())
    ev = RecordingEvaluator(real_evaluator)
    rows = experiment.solve_problem(llm, ev, PROBLEM, max_attempts=3,
                                    autogen_fn=run_autogen_review)
    by_mode = {r['mode']: r for r in rows}
    assert set(by_mode) == {'baseline', 'testing', 'reflexion', 'autogen'}

    # 1-2: shared initial and shared tests generated exactly once.
    assert llm.stages.count('initial_code') == 1
    assert llm.stages.count('generate_tests') == 1

    # 3: baseline is the shared initial candidate, no tests, no held-out bias.
    assert by_mode['baseline']['code'].strip() == BAD.strip()
    assert by_mode['baseline']['internal_tests'] == []

    # 4: testing accepts BAD (passes its internal tests) and is a false accept.
    assert by_mode['testing']['code'].strip() == BAD.strip()
    assert by_mode['testing']['internal_pass'] is True
    assert by_mode['testing']['heldout_pass'] is False
    assert by_mode['testing']['false_accept'] is True

    # 5: reflexion stops at the same BAD candidate; no reflection needed.
    assert by_mode['reflexion']['code'].strip() == BAD.strip()
    assert by_mode['reflexion']['false_accept'] is True
    assert 'reflection' not in llm.stages and 'revision' not in llm.stages

    # 6-9: AutoGen: reviewer REVISE on BAD, programmer GOOD, reviewer APPROVE.
    auto = by_mode['autogen']
    assert llm.stages.count('autogen_reviewer') == 2
    assert llm.stages.count('autogen_programmer') == 1
    assert auto['review_calls'] == 2
    assert auto['history'][0]['review_decision'] == 'REVISE'
    assert auto['history'][1]['review_decision'] == 'APPROVE'
    assert auto['code'].strip() == GOOD.strip()
    assert auto['workflow_accept'] is True
    assert auto['heldout_pass'] is True
    assert auto['workflow_false_accept'] is False

    # 10: every held-out check happened only after all internal checks.
    kinds = [c[0] for c in ev.calls]
    last_internal = max(i for i, k in enumerate(kinds) if k == 'internal')
    first_heldout = min(i for i, k in enumerate(kinds) if k == 'heldout')
    assert last_internal < first_heldout

    # 11: the held-out test text never appears in any recorded LLM-facing history.
    assert 'candidate(")(")' not in json.dumps(auto['history'], default=str)

    # 12: persisted rows keep the schema analyze_results.py consumes.
    out = tmp_path / 'results.jsonl'
    with out.open('w') as f:
        for r in rows:
            f.write(json.dumps(r, default=str) + '\n')
    assert out.read_text().count('\n') == 4


def test_humaneval_1_canonical_pass_and_wrong_fail(real_evaluator):
    from dataset import load, public_problem
    data = ROOT / 'data' / 'HumanEval.jsonl.gz'
    if not data.exists():
        pytest.skip('HumanEval dataset not downloaded')
    problem = next(p for p in load(data) if p['task_id'] == 'HumanEval/1')
    assert public_problem(problem).keys() == {'task_id', 'prompt', 'entry_point'}
    full_code = problem['prompt'] + problem['canonical_solution']
    assert real_evaluator.heldout(full_code, problem).passed is True
    wrong = 'def separate_paren_groups(paren_string):\n    return [paren_string]\n'
    assert real_evaluator.heldout(wrong, problem).passed is False


def test_analysis_pipeline_on_synthetic_rows(tmp_path):
    methods = ['baseline', 'testing', 'reflexion', 'autogen']
    rows = []
    for mode in methods:
        for tid in ('synthetic/a', 'synthetic/b'):
            ok = mode != 'testing' and mode != 'reflexion'
            rows.append({
                'task_id': tid, 'mode': mode, 'code': '', 'attempts': 1, 'history': [],
                'internal_pass': mode != 'baseline', 'stop_reason': 'accepted',
                'calls': 1, 'input_tokens': 10, 'output_tokens': 5, 'tokens_available': True,
                'api_seconds': 0.0, 'solve_seconds': 0.0,
                'heldout_pass': ok, 'heldout_status': 'passed' if ok else 'failed',
                'heldout_feedback': '', 'false_accept': None if mode == 'baseline' else not ok,
                'test_count': 0 if mode == 'baseline' else 2, 'internal_tests': [],
                'llm_events': [],
                'workflow_accept': None if mode == 'baseline' else mode == 'autogen',
                'workflow_false_accept': None if mode == 'baseline' else False,
                'review_calls': 2 if mode == 'autogen' else None,
            })
    (tmp_path / 'results.jsonl').write_text('\n'.join(json.dumps(r) for r in rows) + '\n')
    (tmp_path / 'config.json').write_text(json.dumps(
        {'methods': methods, 'tasks': ['synthetic/a', 'synthetic/b']}))
    res = subprocess.run([sys.executable, str(ROOT / 'analyze_results.py'), str(tmp_path)],
                         capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, res.stderr
    with (tmp_path / 'summary.csv').open() as f:
        summary = {r['mode']: r for r in csv.DictReader(f)}
    assert set(summary) == set(methods)
    assert 'accuracy_pct' in summary['autogen']
    assert 'false_accept_pct_of_accepted' in summary['testing']
    assert 'workflow_false_accept_pct_of_workflow_accepted' in summary['autogen']
    assert float(summary['autogen']['accuracy_pct']) == 100.0
    assert float(summary['testing']['accuracy_pct']) == 0.0
