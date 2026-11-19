"""Paired experiment runner. All candidates are frozen before held-out scoring."""
import argparse
import csv
import hashlib
import json
import os
import time
from pathlib import Path
from dataclasses import asdict
from dataset import load, select, public_problem, DEFAULT
from evaluator import DockerEvaluator
from agents import initial_code, generate_tests, iterate
from llm import LLM


def stats(events):
    """Sums token usage only over events where the provider actually
    returned usage data. 'input_tokens'/'output_tokens' are None (not 0)
    if NO event in this arm had usage data available, so downstream
    analysis cannot silently misread "no data" as "zero cost". When some
    but not all events are missing usage, the sum covers only the
    available events and 'tokens_available' is still reported False to
    flag that the total is partial, not complete."""
    measured = [e for e in events if e.get('tokens_available', True)]
    all_available = len(measured) == len(events)
    input_tokens = sum(e['input_tokens'] for e in measured) if measured else None
    output_tokens = sum(e['output_tokens'] for e in measured) if measured else None
    return {'calls': len(events), 'input_tokens': input_tokens,
            'output_tokens': output_tokens, 'tokens_available': all_available,
            'api_seconds': sum(e['seconds'] for e in events)}


def solve_problem(llm, evaluator, full, max_attempts=3, autogen_fn=None):
    """`autogen_fn`, when provided, must have the same call shape as
    `autogen_agents.run_autogen_review` (llm, evaluator, problem, initial,
    tests, max_attempts). It is injected rather than imported
    unconditionally so that the original three-method experiment never
    requires the autogen-agentchat dependency to be installed."""
    problem = public_problem(full)
    start = time.monotonic()
    before = len(llm.events)
    code = initial_code(llm, problem)
    baseline_time = time.monotonic() - start
    shared_initial = llm.events[before:].copy()
    before = len(llm.events)
    start = time.monotonic()
    tests = generate_tests(llm, problem)
    tests_time = time.monotonic() - start
    shared_tests = llm.events[before:].copy()
    groups = {'baseline': ({'code': code, 'attempts': 1, 'history': [], 'internal_pass': None,
                            'stop_reason': 'single_generation'}, shared_initial, baseline_time)}
    for mode, use_reflection in [('testing', False), ('reflexion', True)]:
        before = len(llm.events)
        start = time.monotonic()
        result = iterate(llm, evaluator, problem, code, tests, use_reflection, max_attempts)
        elapsed = baseline_time + tests_time + time.monotonic() - start
        events = shared_initial + shared_tests + llm.events[before:]
        groups[mode] = (result, events, elapsed)
    if autogen_fn is not None:
        before = len(llm.events)
        start = time.monotonic()
        result = autogen_fn(llm, evaluator, problem, code, tests, max_attempts)
        elapsed = baseline_time + tests_time + time.monotonic() - start
        events = shared_initial + shared_tests + llm.events[before:]
        groups['autogen'] = (result, events, elapsed)
    # All arms have stopped; no held-out outcomes feed into any LLM request.
    rows = []
    for mode, (result, events, elapsed) in groups.items():
        final = evaluator.heldout(result['code'], full)
        internal_pass = result['internal_pass']
        if mode == 'baseline':
            workflow_accept = None
        elif mode == 'autogen':
            workflow_accept = result['workflow_accept']
        else:
            # Testing/Reflexion accept a candidate exactly when its
            # internal tests pass; AutoGen additionally requires valid
            # reviewer approval (see autogen_agents.run_autogen_review).
            workflow_accept = internal_pass
        rows.append({'task_id': problem['task_id'], 'mode': mode, **result, **stats(events),
                     'solve_seconds': elapsed, 'heldout_pass': final.passed,
                     'heldout_status': final.status, 'heldout_feedback': final.feedback,
                     'workflow_accept': workflow_accept,
                     'false_accept': None if mode == 'baseline' else bool(internal_pass and not final.passed),
                     'workflow_false_accept': None if mode == 'baseline' else bool(workflow_accept and not final.passed),
                     'review_calls': result.get('review_calls'),
                     'test_count': len(tests) if mode != 'baseline' else 0,
                     'internal_tests': tests if mode != 'baseline' else [], 'llm_events': events})
    return rows


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--model', default=os.getenv('LLM_MODEL'))
    p.add_argument('--data', type=Path, default=DEFAULT)
    p.add_argument('--count', type=int, default=20)
    p.add_argument('--seed', type=int, default=42)
    p.add_argument('--max-attempts', type=int, default=3)
    p.add_argument('--pilot', action='store_true', help='Use 2 fixed disjoint development tasks')
    p.add_argument('--include-autogen', action='store_true',
                    help='Also run Method D (AutoGen proactive review) alongside baseline/testing/reflexion')
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args()
    if not args.model:
        p.error('Specify --model or LLM_MODEL')
    if args.max_attempts < 1:
        p.error('max-attempts must be positive')
    items = load(args.data)
    pilot = [x for x in items if x['task_id'] in ('HumanEval/0', 'HumanEval/1')]
    sample = pilot if args.pilot else select(items, args.count, args.seed, exclude=('HumanEval/0','HumanEval/1'))
    if not sample:
        p.error('No tasks selected')
    # Fail before paying for inference if Docker is not ready.
    evaluator = DockerEvaluator()
    autogen_fn = None
    if args.include_autogen:
        # Imported only when requested: the original three-method
        # experiment never requires the autogen-agentchat dependency.
        from autogen_agents import run_autogen_review
        autogen_fn = run_autogen_review
    llm = LLM(args.model)
    args.out.mkdir(parents=True, exist_ok=False)
    methods = ['baseline', 'testing', 'reflexion'] + (['autogen'] if args.include_autogen else [])
    config = {'model': args.model, 'temperature': 0.0, 'seed': args.seed, 'max_attempts': args.max_attempts,
              'pilot': args.pilot, 'tasks': [x['task_id'] for x in sample], 'methods': methods,
              'include_autogen': args.include_autogen,
              'dataset_sha256': hashlib.sha256(args.data.read_bytes()).hexdigest(), 'docker_image': evaluator.image,
              'memory': 'latest reflection only', 'shared_initial_and_tests': True}
    (args.out/'config.json').write_text(json.dumps(config, indent=2))
    with (args.out/'results.jsonl').open('w', encoding='utf-8') as f:
        for i, problem in enumerate(sample, 1):
            rows = solve_problem(llm, evaluator, problem, args.max_attempts, autogen_fn)
            for row in rows:
                f.write(json.dumps(row) + '\n')
            f.flush()
            print(f"[{i}/{len(sample)}] {problem['task_id']} " + ' '.join(f"{r['mode']}={r['heldout_pass']}" for r in rows), flush=True)
            llm.events.clear()
    print(f'Results saved to {args.out}')

if __name__ == '__main__':
    main()
