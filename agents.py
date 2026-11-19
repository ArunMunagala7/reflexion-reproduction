"""Three paired experimental modes; no benchmark tests in the retry loop."""
from dataclasses import asdict
from evaluator import parse_tests, strip_fence


def initial_code(llm, problem):
    prompt = (f"Implement the Python function in the following specification. Return a complete runnable Python module "
              f"including imports and the function definition, not just the body. Do not include tests or Markdown.\n\n{problem['prompt']}")
    return strip_fence(llm.ask('initial_code', prompt).text)


def generate_tests(llm, problem):
    prompt = (f"Create 1 to 6 standalone Python assert statements testing {problem['entry_point']} using only its specification. "
              "Include edge cases and correct expected values. Output ONLY top-level asserts, no imports, definitions, or prose. "
              f"The function already exists in scope.\n\n{problem['prompt']}")
    text = llm.ask('generate_tests', prompt).text
    return parse_tests(text, problem['entry_point'])


def iterate(llm, evaluator, problem, initial, tests, use_reflection, max_attempts=3):
    if max_attempts < 1:
        raise ValueError('max_attempts must be positive')
    code = initial
    history = []
    last_reflection = None
    for attempt in range(1, max_attempts + 1):
        evaluation = evaluator.internal(code, tests, problem['entry_point'])
        row = {'attempt': attempt, 'code': code, 'internal': asdict(evaluation), 'reflection': None}
        history.append(row)
        if evaluation.passed:
            reason = 'internal_pass'
            break
        if not tests:
            # Checked before the attempt-limit condition so the recorded
            # stop_reason reflects the true cause (no valid generated tests)
            # even when emptiness is only discovered on the final attempt.
            reason = 'no_valid_tests'
            break
        if attempt == max_attempts:
            reason = 'trial_limit'
            break
        context = (f"Problem specification:\n{problem['prompt']}\n\nPrevious code:\n{code}\n\n"
                   f"Internal tests:\n" + '\n'.join(tests) + f"\n\nInternal failure feedback:\n{evaluation.feedback}")
        reflection_context = ''
        if use_reflection:
            last_reflection = llm.ask('reflection', context + '\n\nExplain the likely mistake and a concrete correction in 1–3 sentences. Do not write code.').text
            row['reflection'] = last_reflection
            reflection_context = f'\n\nMost recent reflection:\n{last_reflection}'
        code = strip_fence(llm.ask('revision', context + reflection_context + '\n\nReturn a complete revised runnable Python module, including imports and function definition. No Markdown or tests.').text)
    return {'code': code, 'attempts': len(history), 'history': history,
            'internal_pass': history[-1]['internal']['passed'], 'stop_reason': reason}
