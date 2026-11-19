"""Run untrusted candidate code ONLY in a disposable, restricted Docker container."""
import ast
import json
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

IMAGE = 'python:3.11-slim'
RUNNER = Path(__file__).with_name('sandbox_runner.py').read_text()


@dataclass
class Evaluation:
    passed: bool
    feedback: str
    status: str = 'evaluated'


def strip_fence(text):
    text = text.strip()
    if text.startswith('```'):
        lines = text.splitlines()
        if lines[-1].strip() == '```':
            return '\n'.join(lines[1:-1]).strip()
    return text


def _is_constant_shortcircuit(node):
    """Detect `<constant> or <expr>` / `<expr> or <constant-true>` and
    `<constant> and <expr>` shapes where a literal boolean/constant operand
    makes the other operand's evaluation unnecessary (e.g. `True or f(1)==2`,
    `False and f(1)==2`). Conservative: only fires on ast.Constant operands,
    never on names, calls, or other expressions, so legitimate boolean
    combinations of real conditions are never rejected by this check."""
    if not isinstance(node, ast.BoolOp):
        return False
    return any(isinstance(v, ast.Constant) for v in node.values)


def _is_identical_expression_selfcompare(node):
    """Detect `<expr> == <expr>` (or chained comparisons) where the
    left and right sides are syntactically identical (via ast.dump),
    e.g. `f(1) == f(1)`. This passes for any deterministic function
    regardless of correctness and is rejected conservatively: only exact
    structural duplicates are flagged, never merely "the same function
    called twice" with different arguments (e.g. `f(f(1)) == 3` or
    `f(1) + f(2) == 5` are NOT rejected by this check)."""
    if not isinstance(node, ast.Compare):
        return False
    if len(node.ops) != 1 or not isinstance(node.ops[0], ast.Eq):
        return False
    left_dump = ast.dump(node.left)
    return any(ast.dump(comp) == left_dump for comp in node.comparators)


def _contains_vacuous_pattern(node):
    """Walk the assertion's test expression looking for either obvious
    vacuous shape, anywhere in the expression tree (so e.g. a vacuous
    sub-clause nested inside a larger boolean expression is still caught)."""
    for sub in ast.walk(node):
        if _is_constant_shortcircuit(sub) or _is_identical_expression_selfcompare(sub):
            return True
    return False


def parse_tests(text, entry_point):
    """Require 1–6 top-level asserts that call the target function.

    Additionally rejects two specific, obvious vacuous-assertion shapes
    that can pass regardless of candidate correctness:

      1. Constant short-circuit: a boolean `or`/`and` expression with a
         literal constant operand, e.g. `True or f(1) == 2`, where the
         call to entry_point is never actually evaluated.
      2. Identical-expression self-comparison: `<expr> == <expr>` where
         both sides are syntactically identical, e.g. `f(1) == f(1)`,
         which passes for any deterministic function.

    This is a conservative, AST-based heuristic. It does not attempt to
    verify semantic correctness of expected values, and it does not reject
    every assertion with multiple calls to entry_point or every assertion
    using boolean operators -- only the two obvious shapes above. Legitimate
    property-based or compound assertions (e.g. `f(f(1)) == 3`,
    `f(1) + f(2) == 5`) remain accepted. Incomplete or semantically
    incorrect generated tests that do not match these specific shapes
    remain a known, unaddressed limitation of LLM-generated testing.
    """
    try:
        tree = ast.parse(strip_fence(text))
    except SyntaxError:
        return []
    if not tree.body or len(tree.body) > 6:
        return []
    if any(not isinstance(node, ast.Assert) for node in tree.body):
        return []
    for node in tree.body:
        if not any(isinstance(x, ast.Call) and isinstance(x.func, ast.Name)
                   and x.func.id == entry_point for x in ast.walk(node.test)):
            return []
        if _contains_vacuous_pattern(node.test):
            return []
    return [ast.unparse(node) for node in tree.body]


class DockerEvaluator:
    def __init__(self, image=IMAGE, timeout=15):
        if not shutil.which('docker'):
            raise RuntimeError('Docker required; refusing to execute generated code on host.')
        self.image = image
        self.timeout = timeout
        subprocess.run(['docker', 'image', 'inspect', image], check=True, capture_output=True)

    def _run(self, payload):
        name = 'reflexion-' + uuid.uuid4().hex
        command = ['docker', 'run', '--rm', '-i', '--name', name, '--network', 'none',
                   '--read-only', '--tmpfs', '/tmp:rw,noexec,nosuid,size=16m',
                   '--memory', '256m', '--cpus', '1', '--pids-limit', '64',
                   '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                   '--user', '65534:65534', self.image, 'python', '-I', '-c', RUNNER]
        try:
            p = subprocess.run(command, input=json.dumps(payload), text=True,
                               capture_output=True, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=10)
            return Evaluation(False, 'Execution timed out', 'timeout')
        if p.returncode:
            # Docker's own failure codes are infrastructure errors, not model failures.
            if p.returncode in (125, 126, 127):
                raise RuntimeError(f'Docker failed: {p.stderr[:500]}')
            return Evaluation(False, f'Execution exited with code {p.returncode}', 'execution_error')
        try:
            result = json.loads(p.stdout)
            return Evaluation(bool(result['passed']), str(result['feedback'])[:4000], result['status'])
        except (ValueError, KeyError, TypeError):
            return Evaluation(False, 'Invalid evaluator output', 'execution_error')

    def internal(self, code, tests, entry_point):
        if not tests:
            return Evaluation(False, 'No valid internal tests; cannot establish a pass.', 'no_tests')
        return self._run({'code': code, 'tests': tests, 'entry_point': entry_point, 'kind': 'internal'})

    def heldout(self, code, problem):
        return self._run({'code': code, 'tests': problem['test'], 'entry_point': problem['entry_point'], 'kind': 'heldout'})
