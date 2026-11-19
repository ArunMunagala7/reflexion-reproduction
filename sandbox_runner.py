"""Entry point executed inside Docker, not imported into the host evaluator."""
import contextlib
import io
import json
import sys


class Discard(io.TextIOBase):
    def write(self, s):
        return len(s)


def evaluate(data):
    def fail(exc):
        return f'{type(exc).__name__}: {str(exc)[:400]}'
    failures = []
    with contextlib.redirect_stdout(Discard()), contextlib.redirect_stderr(Discard()):
        if data['kind'] == 'internal':
            if not data['tests']:
                return {'passed': False, 'feedback': 'No valid internal tests', 'status': 'no_tests'}
            for test in data['tests']:
                ns = {'__name__': '__candidate__'}
                try:
                    exec(data['code'], ns)
                    exec(test, ns)
                except BaseException as exc:
                    failures.append(f'{test}: {fail(exc)}')
            return {'passed': not failures, 'feedback': '\n'.join(failures) or 'All internal tests passed', 'status': 'evaluated'}
        ns = {'__name__': '__candidate__'}
        try:
            exec(data['code'], ns)
            exec(data['tests'], ns)
            ns['check'](ns[data['entry_point']])
            return {'passed': True, 'feedback': 'Passed benchmark tests', 'status': 'evaluated'}
        except BaseException as exc:
            return {'passed': False, 'feedback': fail(exc), 'status': 'evaluated'}


if __name__ == '__main__':
    payload = json.load(sys.stdin)
    print(json.dumps(evaluate(payload)))
