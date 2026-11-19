"""Real Docker integration verification (no mocked subprocess calls).

Unlike tests/test_harness.py and tests/test_extended_verification.py,
these tests instantiate the actual `evaluator.DockerEvaluator` and launch
real, disposable `python:3.11-slim` containers via the real `docker`
CLI. No LLM API is called anywhere in this file -- only fixed, trusted
Python source fixtures (never model-generated code) are ever executed,
and only inside the isolated container, never on the host.

Preconditions (all independently verified before this file was written):
  - Docker Desktop running, `docker` on PATH.
  - `python:3.11-slim` image already pulled locally.
  - HumanEval dataset already downloaded (`data/HumanEval.jsonl.gz`).

This file is automatically skipped (not failed) if Docker or the image
is unavailable, so the rest of the suite remains runnable in
environments without Docker. All containers launched here go through
`DockerEvaluator._run`, which names every container `reflexion-<uuid>`
and always passes `--rm`; this file creates no containers of its own and
never touches any pre-existing container on the host (e.g. unrelated
`hadoop`/`ubuntu` containers already running on this machine).
"""
import shutil
import subprocess
import time

import pytest

from dataset import load, public_problem
from evaluator import DockerEvaluator

DOCKER_AVAILABLE = shutil.which('docker') is not None
if DOCKER_AVAILABLE:
    try:
        subprocess.run(['docker', 'image', 'inspect', 'python:3.11-slim'],
                        check=True, capture_output=True, timeout=10)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        DOCKER_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not DOCKER_AVAILABLE,
    reason='Docker (or the python:3.11-slim image) is not available in this environment.')

GOOD = 'def f(x):\n    return x + 1\n'
BAD = 'def f(x):\n    return x\n'
RAISES = 'def f(x):\n    raise ValueError("deliberate fixture failure")\n'
SYNTAX_ERROR = 'def f(x):\n    return x +\n'
MISSING_ENTRY_POINT = 'def not_f(x):\n    return x + 1\n'
NONCALLABLE_ENTRY_POINT = 'f = 42\n'
TESTS = ['assert f(1) == 2', 'assert f(0) == 1']


@pytest.fixture(scope='module')
def evaluator():
    """One real DockerEvaluator shared across this module's tests, with a
    short default timeout so the whole file runs quickly. Individual
    timeout tests construct their own evaluator with an even shorter
    timeout rather than relying on this default."""
    return DockerEvaluator(timeout=20)


def _existing_container_names():
    out = subprocess.run(['docker', 'ps', '-a', '--format', '{{.Names}}'],
                          capture_output=True, text=True, timeout=10)
    return set(out.stdout.split())


# --- 1. Correct and incorrect candidates, against the real container ---

def test_real_docker_correct_candidate_passes_internal(evaluator):
    result = evaluator.internal(GOOD, TESTS, 'f')
    assert result.passed is True
    assert result.status == 'evaluated'


def test_real_docker_incorrect_candidate_fails_internal(evaluator):
    result = evaluator.internal(BAD, TESTS, 'f')
    assert result.passed is False
    assert result.status == 'evaluated'
    assert 'assert' in result.feedback.lower() or 'error' in result.feedback.lower()


def test_real_docker_runtime_exception_reported_not_crashed(evaluator):
    result = evaluator.internal(RAISES, TESTS, 'f')
    assert result.passed is False
    assert 'ValueError' in result.feedback


def test_real_docker_syntax_error_reported_not_crashed(evaluator):
    result = evaluator.internal(SYNTAX_ERROR, TESTS, 'f')
    assert result.passed is False
    assert result.status == 'evaluated'


# --- 2. Entry-point validation against the real container ---

def test_real_docker_missing_entry_point_cannot_pass(evaluator):
    result = evaluator.internal(MISSING_ENTRY_POINT, TESTS, 'f')
    assert result.passed is False
    assert 'not defined' in result.feedback or 'entry point' in result.feedback.lower()


def test_real_docker_noncallable_entry_point_cannot_pass(evaluator):
    result = evaluator.internal(NONCALLABLE_ENTRY_POINT, TESTS, 'f')
    assert result.passed is False
    assert 'not callable' in result.feedback.lower() or 'entry point' in result.feedback.lower()


# --- 3. Empty internal-test suite short-circuits without launching Docker ---

def test_real_docker_empty_test_suite_short_circuits(evaluator):
    before = _existing_container_names()
    result = evaluator.internal(GOOD, [], 'f')
    after = _existing_container_names()
    assert result.passed is False and result.status == 'no_tests'
    # No new container should have been launched for an empty test suite.
    assert after - before == set()


# --- 4. Real timeout handling: container killed, named cleanup, status recorded ---

def test_real_docker_timeout_is_enforced_and_cleaned_up():
    short_timeout_evaluator = DockerEvaluator(timeout=3)
    infinite_loop = 'def f(x):\n    while True:\n        pass\n'
    before = _existing_container_names()
    start = time.monotonic()
    result = short_timeout_evaluator.internal(infinite_loop, TESTS, 'f')
    elapsed = time.monotonic() - start
    assert result.passed is False
    assert result.status == 'timeout'
    # Should not hang far beyond the configured timeout.
    assert elapsed < 15
    # Cleanup (`docker rm -f`) should leave no lingering reflexion-* container.
    time.sleep(1)
    after = _existing_container_names()
    assert all(not name.startswith('reflexion-') for name in (after - before))


# --- 5. Network isolation (`--network none`) is actually enforced ---

def test_real_docker_network_is_actually_isolated(evaluator):
    network_attempt = (
        'import socket\n'
        'def f(x):\n'
        '    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n'
        '    s.settimeout(2)\n'
        '    s.connect(("8.8.8.8", 53))\n'
        '    return x + 1\n'
    )
    result = evaluator.internal(network_attempt, TESTS, 'f')
    assert result.passed is False
    # Expect a network-related failure (unreachable/refused/timed out/no route),
    # proving the candidate could not actually reach the network.
    assert result.status == 'evaluated'


# --- 6. Read-only root filesystem (`--read-only` + tmpfs /tmp) is enforced ---

def test_real_docker_filesystem_outside_tmp_is_read_only(evaluator):
    write_attempt = (
        'def f(x):\n'
        '    with open("/etc/not_allowed.txt", "w") as fh:\n'
        '        fh.write("should not be permitted")\n'
        '    return x + 1\n'
    )
    result = evaluator.internal(write_attempt, TESTS, 'f')
    assert result.passed is False
    assert 'OSError' in result.feedback or 'Errno' in result.feedback or 'read-only' in result.feedback.lower()


def test_real_docker_tmp_is_writable(evaluator):
    tmp_write = (
        'def f(x):\n'
        '    with open("/tmp/scratch.txt", "w") as fh:\n'
        '        fh.write("ok")\n'
        '    return x + 1\n'
    )
    result = evaluator.internal(tmp_write, TESTS, 'f')
    assert result.passed is True


# --- 7. Real held-out evaluation using the actual HumanEval/0 fixture ---

@pytest.fixture(scope='module')
def humaneval_0():
    items = load()
    return next(x for x in items if x['task_id'] == 'HumanEval/0')


def test_real_docker_heldout_official_test_passes_for_canonical_solution(evaluator, humaneval_0):
    public = public_problem(humaneval_0)
    candidate_module = humaneval_0['prompt'] + humaneval_0['canonical_solution']
    result = evaluator.heldout(candidate_module, humaneval_0)
    assert result.passed is True
    assert public['entry_point'] == 'has_close_elements'


def test_real_docker_heldout_official_test_fails_for_wrong_solution(evaluator, humaneval_0):
    wrong_candidate = humaneval_0['prompt'] + '    return False\n'
    result = evaluator.heldout(wrong_candidate, humaneval_0)
    assert result.passed is False


# --- 8. No stray reflexion-* containers left behind after this module runs ---

def test_no_lingering_reflexion_containers_after_suite(evaluator, humaneval_0):
    # Run one more evaluation, then confirm no reflexion-* container
    # remains (DockerEvaluator always uses --rm, this is a sanity check).
    evaluator.internal(GOOD, TESTS, 'f')
    time.sleep(1)
    names = _existing_container_names()
    assert not any(name.startswith('reflexion-') for name in names)
