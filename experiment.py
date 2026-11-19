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
    return {'calls': len(events), 'input_tokens': sum(e['input_tokens'] for e in events),
            'output_tokens': sum(e['output_tokens'] for e in events),
            'api_seconds': sum(e['seconds'] for e in events)}


def solve_problem(llm, evaluator, full, max_attempts=3):
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
    # All arms have stopped; no held-out outcomes feed into any LLM request.
    rows = []
    for mode, (result, events, elapsed) in groups.items():
        final = evaluator.heldout(result['code'], full)
        rows.append({'task_id': problem['task_id'], 'mode': mode, **result, **stats(events),
                     'solve_seconds': elapsed, 'heldout_pass': final.passed,
                     'heldout_status': final.status, 'heldout_feedback': final.feedback,
                     'false_accept': None if mode == 'baseline' else bool(result['internal_pass'] and not final.passed),
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
    llm = LLM(args.model)
    args.out.mkdir(parents=True, exist_ok=False)
    config = {'model': args.model, 'temperature': 0.0, 'seed': args.seed, 'max_attempts': args.max_attempts,
              'pilot': args.pilot, 'tasks': [x['task_id'] for x in sample],
              'dataset_sha256': hashlib.sha256(args.data.read_bytes()).hexdigest(), 'docker_image': evaluator.image,
              'memory': 'latest reflection only', 'shared_initial_and_tests': True}
    (args.out/'config.json').write_text(json.dumps(config, indent=2))
    with (args.out/'results.jsonl').open('w', encoding='utf-8') as f:
        for i, problem in enumerate(sample, 1):
            rows = solve_problem(llm, evaluator, problem, args.max_attempts)
            for row in rows:
                f.write(json.dumps(row) + '\n')
            f.flush()
            print(f"[{i}/{len(sample)}] {problem['task_id']} " + ' '.join(f"{r['mode']}={r['heldout_pass']}" for r in rows), flush=True)
            llm.events.clear()
    print(f'Results saved to {args.out}')

if __name__ == '__main__':
    main()
