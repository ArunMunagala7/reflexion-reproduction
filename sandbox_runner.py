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

    def check_entry_point(ns, entry_point):
        """Explicitly verify the expected entry point exists and is callable
        before any assertion is executed. This is a defense-in-depth check:
        it catches cases where a generated test's assertion shape happens not
        to actually exercise entry_point at runtime (e.g. a vacuous
        short-circuit), which would otherwise let a missing or non-callable
        entry point go undetected."""
        if entry_point not in ns:
            return f'Expected entry point {entry_point!r} was not defined by the candidate module.'
        if not callable(ns[entry_point]):
            return f'Expected entry point {entry_point!r} is not callable (got {type(ns[entry_point]).__name__}).'
        return None

    failures = []
    with contextlib.redirect_stdout(Discard()), contextlib.redirect_stderr(Discard()):
        if data['kind'] == 'internal':
            if not data['tests']:
                return {'passed': False, 'feedback': 'No valid internal tests', 'status': 'no_tests'}
            for test in data['tests']:
                ns = {'__name__': '__candidate__'}
                try:
                    exec(data['code'], ns)
                    entry_error = check_entry_point(ns, data['entry_point'])
                    if entry_error:
                        failures.append(f'{test}: {entry_error}')
                        continue
                    exec(test, ns)
                except BaseException as exc:
                    failures.append(f'{test}: {fail(exc)}')
            return {'passed': not failures, 'feedback': '\n'.join(failures) or 'All internal tests passed', 'status': 'evaluated'}
        ns = {'__name__': '__candidate__'}
        try:
            exec(data['code'], ns)
            entry_error = check_entry_point(ns, data['entry_point'])
            if entry_error:
                return {'passed': False, 'feedback': entry_error, 'status': 'evaluated'}
            exec(data['tests'], ns)
            ns['check'](ns[data['entry_point']])
            return {'passed': True, 'feedback': 'Passed benchmark tests', 'status': 'evaluated'}
        except BaseException as exc:
            return {'passed': False, 'feedback': fail(exc), 'status': 'evaluated'}


if __name__ == '__main__':
    payload = json.load(sys.stdin)
    print(json.dumps(evaluate(payload)))
