"""Read official HumanEval data; sampling is fixed before inference."""
import gzip
import hashlib
import json
import random
import urllib.request
from pathlib import Path

URL = 'https://raw.githubusercontent.com/openai/human-eval/master/data/HumanEval.jsonl.gz'
DEFAULT = Path('data/HumanEval.jsonl.gz')


def load(path=DEFAULT):
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f'{path} missing. Run python dataset.py first.')
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rt', encoding='utf-8') as f:
        items = [json.loads(line) for line in f if line.strip()]
    if len({x['task_id'] for x in items}) != len(items):
        raise ValueError('Duplicate task IDs')
    return sorted(items, key=lambda x: x['task_id'])


def select(items, count, seed=42, exclude=()):
    pool = [x for x in items if x['task_id'] not in exclude]
    if not 1 <= count <= len(pool):
        raise ValueError('Invalid sample size')
    return random.Random(seed).sample(pool, count)


def public_problem(problem):
    # Whitelist; never pass the reference implementation or benchmark tests.
    return {k: problem[k] for k in ('task_id', 'prompt', 'entry_point')}


if __name__ == '__main__':
    DEFAULT.parent.mkdir(parents=True, exist_ok=True)
    if not DEFAULT.exists():
        with urllib.request.urlopen(URL, timeout=30) as response:
            DEFAULT.write_bytes(response.read())
    items = load()
    print(f'Loaded {len(items)} problems')
    print('Dataset SHA256:', hashlib.sha256(DEFAULT.read_bytes()).hexdigest())
