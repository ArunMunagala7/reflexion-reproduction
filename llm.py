"""OpenAI-compatible chat API adapter. Credentials never enter the evaluator."""
import os
import time
from dataclasses import dataclass


@dataclass
class Response:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    tokens_available: bool = True


class LLM:
    def __init__(self, model, temperature=0.0):
        from openai import OpenAI
        self.model = model
        self.temperature = temperature
        self.client = OpenAI(api_key=os.environ['OPENAI_API_KEY'], timeout=90,
                             max_retries=2, base_url=os.environ.get('OPENAI_BASE_URL') or None)
        self.events = []

    def ask(self, stage, prompt):
        start = time.monotonic()
        result = self.client.chat.completions.create(
            model=self.model, temperature=self.temperature, max_tokens=2000,
            messages=[{'role': 'user', 'content': prompt}])
        u = result.usage
        tokens_available = u is not None
        text = result.choices[0].message.content or ''
        event = Response(text, u.prompt_tokens if u else None, u.completion_tokens if u else None,
                         time.monotonic() - start, tokens_available)
        self.events.append({'stage': stage, 'prompt': prompt, 'response': text,
                            'input_tokens': event.input_tokens, 'output_tokens': event.output_tokens,
                            'tokens_available': tokens_available,
                            'seconds': event.seconds, 'resolved_model': result.model,
                            'finish_reason': result.choices[0].finish_reason})
        if not tokens_available:
            print(f'WARNING: provider returned no token usage data for stage={stage!r}; '
                  'recording as unavailable, not zero.')
        return event
