# Implementation Guide

A function-by-function reference for every production module in this
repository. Read the [main README](../README.md) first for the overall
narrative, research motivation, and experimental design — this guide
assumes that context and focuses purely on "what does this function do,
what are its inputs/outputs, and how does it connect to the rest of the
system."

> **Path 2 note:** Method D (AutoGen proactive review) is covered in its
> own section below (`autogen_agents.py`) and in full design detail in
> [`docs/AUTOGEN_DESIGN.md`](AUTOGEN_DESIGN.md). Methods A/B/C
> (`dataset.py`, `llm.py`, `agents.py`, `evaluator.py`,
> `sandbox_runner.py`) are unchanged from Path 1.

---

## `dataset.py`

**Purpose:** load the official HumanEval benchmark, select a reproducible
subset, and enforce the public/secret field split that the rest of the
system depends on.

### `load(path=DEFAULT) -> list[dict]`
- Opens `path` (gzip or plain text, detected by `.gz` suffix).
- Parses every non-blank line as a JSON object.
- Raises `ValueError` if any `task_id` is duplicated.
- Returns the list **sorted by `task_id`** (not file order), so
  downstream sampling is deterministic given the same file.

### `select(items, count, seed=42, exclude=()) -> list[dict]`
- Filters out any `task_id` present in `exclude`.
- Validates `1 <= count <= len(pool)`, raising `ValueError` otherwise.
- Uses `random.Random(seed).sample(pool, count)` — a **locally seeded**
  RNG instance, not `random.sample` on the global module-level RNG — so
  calling this function does not disturb any other code's use of
  `random`, and the same `(items, count, seed, exclude)` always produces
  the same subset.

### `public_problem(problem) -> dict`
- A single-line whitelist: `{k: problem[k] for k in ('task_id', 'prompt',
  'entry_point')}`.
- This is the **only** function in the codebase that is allowed to
  produce the dict that gets passed into `agents.py`. Everything
  downstream (prompts, revisions, reflections) only ever sees the output
  of this function, never the raw `problem` dict with
  `canonical_solution`/`test` still attached.

### `if __name__ == '__main__':` block
- Downloads `data/HumanEval.jsonl.gz` from the official HumanEval GitHub
  raw URL if the file does not already exist locally.
- Prints the number of problems loaded and the file's SHA256 hash (the
  same hash that later gets written into every experiment run's
  `config.json`, for reproducibility auditing).

---

## `llm.py`

**Purpose:** the only module that talks to a real chat-completion API.
Wraps every call with timing and token-usage logging.

### `Response` (dataclass)
Fields: `text`, `input_tokens`, `output_tokens`, `seconds`,
`tokens_available` (added in Stage 3; defaults to `True`).

### `LLM.__init__(self, model, temperature=0.0)`
- Reads `OPENAI_API_KEY` from the environment (raises `KeyError` if
  unset — intentionally, so a missing key fails loudly before any
  experiment logic runs).
- Reads `OPENAI_BASE_URL` optionally, for OpenAI-API-compatible
  alternative providers.
- Sets `timeout=90`, `max_retries=2` on the underlying `openai.OpenAI`
  client — these are SDK-level retry/timeout settings, not something
  `llm.py` itself re-implements.
- Initializes `self.events = []`, which accumulates every call made
  through this instance for the lifetime of the object (cleared
  externally by `experiment.py`'s main loop after each problem).

### `LLM.ask(self, stage, prompt) -> Response`
- `stage` is a free-form label (`'initial_code'`, `'generate_tests'`,
  `'reflection'`, `'revision'`) used purely for logging/analysis — it has
  no effect on the actual API request.
- Makes one `chat.completions.create` call with `temperature=self.temperature`
  and `max_tokens=2000`.
- `tokens_available = u is not None` where `u = result.usage` — this is
  the Stage 3 correction: if the provider's response has no `usage`
  block, token counts are recorded as `None`, not `0`, and
  `tokens_available` is `False`.
- Appends a full event dict to `self.events`, including the raw `prompt`
  and `response` text (this is what later lets you inspect exact prompts
  in `results.jsonl`'s `llm_events` field).
- Prints a `WARNING: ...` line immediately if `tokens_available` is
  `False`, so a live run surfaces the gap in real time rather than only
  on later inspection.

---

## `agents.py`

**Purpose:** implements the actual Method A/B/C logic. This is the file
most directly tied to the research question.

### `initial_code(llm, problem) -> str`
- Builds one prompt asking for a complete runnable module (not a
  HumanEval-style body completion) implementing `problem['prompt']`'s
  specification.
- Calls `llm.ask('initial_code', prompt)` and returns
  `strip_fence(result.text)` (markdown code-fence stripping, from
  `evaluator.py`).
- Called exactly once per problem, shared by all three methods.

### `generate_tests(llm, problem) -> list[str]`
- Builds one prompt asking for 1–6 standalone `assert` statements
  testing `problem['entry_point']`, using only the public specification.
- Calls `llm.ask('generate_tests', prompt)`, then immediately validates
  the raw text through `evaluator.parse_tests(text, problem['entry_point'])`.
- Returns the **validated** list (possibly empty if validation fails) —
  callers never see raw, unvalidated LLM test output.
- Called exactly once per problem, shared by Methods B and C.

### `iterate(llm, evaluator, problem, initial, tests, use_reflection, max_attempts=3) -> dict`
This is the single most important function in the repository. See the
main README's [Section 10](../README.md#10-main-algorithm-and-stopping-conditions)
for the full pseudocode and stop-reason table. Implementation notes not
covered there:
- Raises `ValueError` immediately if `max_attempts < 1`, before any LLM
  or evaluator call.
- `history` accumulates one dict per attempt:
  `{'attempt': int, 'code': str, 'internal': asdict(Evaluation), 'reflection': str|None}`.
  This is exactly what ends up in `results.jsonl`'s `history` field.
- `last_reflection` is a **plain local variable**, overwritten (not
  appended to a list) each time a reflection is generated — this is the
  literal implementation of "store only the latest reflection."
- `use_reflection` is the **only** parameter that differs between how
  `experiment.py` calls this function for Method B (`False`) vs. Method C
  (`True`); every other argument is identical between the two calls for a
  given problem.
- The returned dict's `internal_pass` is read from
  `history[-1]['internal']['passed']` — i.e., always reflects the
  *final* attempt's evaluation, regardless of which stop reason applies.

---

## `evaluator.py`

**Purpose:** host-side orchestration of Docker-isolated code execution,
plus the generated-test validation policy.

### `Evaluation` (dataclass)
Fields: `passed: bool`, `feedback: str`, `status: str = 'evaluated'`.
`status` values seen in practice: `'evaluated'`, `'no_tests'`,
`'timeout'`, `'execution_error'`.

### `strip_fence(text) -> str`
Removes a single leading/trailing ` ``` ` markdown code fence if present,
otherwise returns the text unchanged. Used on all LLM-generated code
before it is treated as a Python module.

### `_is_constant_shortcircuit(node) -> bool` *(Stage 3 addition)*
True if `node` is an `ast.BoolOp` (`or`/`and`) where at least one operand
is a literal `ast.Constant`. Deliberately narrow: never fires on names,
calls, or non-literal expressions.

### `_is_identical_expression_selfcompare(node) -> bool` *(Stage 3 addition)*
True if `node` is a single-operator `ast.Compare` using `Eq`, where
`ast.dump(left) == ast.dump(comparator)` — i.e., both sides are
syntactically identical. Does not fire on expressions that merely call
the same function twice with different arguments.

### `_contains_vacuous_pattern(node) -> bool` *(Stage 3 addition)*
Walks the full expression subtree (`ast.walk`) looking for either of the
two patterns above, so a vacuous sub-clause nested inside a larger
expression is still caught.

### `parse_tests(text, entry_point) -> list[str]`
1. Parses `strip_fence(text)` as Python; returns `[]` on `SyntaxError`.
2. Requires 1–6 top-level statements, all `ast.Assert`; returns `[]`
   otherwise.
3. For each assertion, requires at least one `ast.Call` to a `ast.Name`
   matching `entry_point` anywhere in the test expression; returns `[]`
   if any assertion fails this.
4. **(Stage 3)** For each assertion, rejects the **entire batch** if
   `_contains_vacuous_pattern` matches.
5. Returns `[ast.unparse(node) for node in tree.body]` — note this
   re-serializes the AST, which normalizes formatting (e.g., drops
   redundant parentheses) relative to the original LLM text.

### `class DockerEvaluator`
- `__init__(self, image=IMAGE, timeout=15)`: requires `docker` to be on
  `PATH` (`shutil.which`), then runs `docker image inspect` to fail fast
  if the image is not pulled. Raises `RuntimeError` otherwise.
- `_run(self, payload) -> Evaluation`: builds the full `docker run`
  command (see the main README's [Docker section](../README.md#15-docker-setup-and-security)
  for the exact flags), pipes `json.dumps(payload)` to the container's
  stdin, and interprets the result:
  - `subprocess.TimeoutExpired` → best-effort `docker rm -f` cleanup,
    returns `Evaluation(False, 'Execution timed out', 'timeout')`.
  - Return code `125`/`126`/`127` → raises `RuntimeError` (infrastructure
    failure, not a candidate failure — this should halt the experiment,
    not be silently scored).
  - Any other nonzero return code → `Evaluation(False, ..., 'execution_error')`.
  - Zero return code but non-JSON or malformed stdout → caught
    `(ValueError, KeyError, TypeError)`, returns
    `Evaluation(False, 'Invalid evaluator output', 'execution_error')`.
  - Otherwise, parses the JSON result into an `Evaluation`.
- `internal(self, code, tests, entry_point) -> Evaluation`: short-circuits
  to `Evaluation(False, ..., 'no_tests')` **without launching Docker** if
  `tests` is empty; otherwise delegates to `_run` with `kind='internal'`.
- `heldout(self, code, problem) -> Evaluation`: delegates to `_run` with
  `kind='heldout'`, passing `problem['test']` (the official hidden test)
  and `problem['entry_point']`. This is the **only** function in the
  entire codebase that is given access to `problem['test']` —
  `agents.py` never sees it.

---

## `sandbox_runner.py`

**Purpose:** the code that actually executes inside the Docker
container. **Never imported by the host process** — `evaluator.py` reads
this file's source as plain text (`RUNNER = Path(__file__).with_name('sandbox_runner.py').read_text()`)
and ships it into the container as a `python -I -c <source>` argument.
This means any change to this file requires no image rebuild, but also
means you cannot set a breakpoint in it from the host process — debug it
by calling `evaluate()` directly in a host-side test instead (as
`tests/test_extended_verification.py` and `tests/test_regressions.py`
already do).

### `class Discard(io.TextIOBase)`
A no-op writable stream used to redirect candidate/test
`print()` output so it cannot corrupt the single JSON line this script
writes to its own real stdout at the very end.

### `check_entry_point(ns, entry_point) -> str | None` *(Stage 3 addition, nested inside `evaluate`)*
- Returns a descriptive error string if `entry_point not in ns`.
- Returns a descriptive error string if `not callable(ns[entry_point])`.
- Returns `None` if both checks pass.
- Called **immediately after** `exec(data['code'], ns)` and **before**
  any assertion or the held-out `check(candidate)` call executes, for
  both `kind='internal'` and `kind='heldout'`.

### `evaluate(data: dict) -> dict`
- `data['kind'] == 'internal'`:
  - Empty `data['tests']` → immediate `{'passed': False, ...,
    'status': 'no_tests'}` (defense-in-depth; `DockerEvaluator.internal`
    already short-circuits this on the host side too).
  - For each test string: fresh namespace, `exec(code)`, then
    `check_entry_point`; on failure, append a failure string and
    `continue` to the next test **without** attempting to execute the
    assertion itself. Otherwise `exec(test, ns)`, catching
    `BaseException` and recording the exception type/message.
  - Passes only if **zero** failures were recorded across all tests.
- `data['kind'] == 'heldout'`: same structure, single test string
  containing the official `check(candidate)` function definition; calls
  `ns['check'](ns[data['entry_point']])` only after `check_entry_point`
  passes.
- All candidate/test execution happens inside
  `contextlib.redirect_stdout(Discard())` / `redirect_stderr(Discard())`,
  so candidate `print()` calls cannot interfere with the final
  `print(json.dumps(...))` in `__main__`, which is the only thing that
  reaches the real stdout stream the host process reads.

---

## `experiment.py`

**Purpose:** the CLI entry point that turns one problem into three
scored result rows, with correct shared-event attribution.

### `stats(events) -> dict` *(rewritten in Stage 3)*
- `measured = [e for e in events if e.get('tokens_available', True)]` —
  treats a missing key as available, for backward compatibility with any
  pre-Stage-3 event shape.
- If `measured` is non-empty, sums `input_tokens`/`output_tokens` over
  only those events; if `measured` is empty, both are `None`.
- Returns a `tokens_available` flag that is `True` only if **every**
  event in this arm had usage data (so a partial sum is still flagged as
  incomplete).

### `solve_problem(llm, evaluator, full, max_attempts=3) -> list[dict]`
1. `problem = public_problem(full)` — the public-only view used for
   everything LLM-facing in this function.
2. Calls `initial_code` once, calls `generate_tests` once — these two
   events lists (`shared_initial`, `shared_tests`) are each `.copy()`'d
   out of `llm.events` immediately, so they survive being logically
   reused by multiple arms even though `llm.events` keeps growing.
3. Builds `groups['baseline']` directly (no evaluator call at all).
4. For `mode, use_reflection in [('testing', False), ('reflexion', True)]`:
   calls `agents.iterate(...)`, and attributes
   `shared_initial + shared_tests + llm.events[before:]` as that arm's
   event list — this is the literal implementation of "shared events
   counted once per arm that uses them."
5. **Only after all three groups are fully populated** does the function
   loop over `groups` calling `evaluator.heldout(result['code'], full)`
   for each — this is the code-level guarantee that no held-out result
   can exist before every arm's final candidate has already been chosen.
6. Builds and returns one result-row dict per mode, including
   `false_accept` (computed here, not in `analyze_results.py`) and the
   full `llm_events` list for that arm.

### `main()`
- Parses CLI flags (see the main README's
  [full flag reference](../README.md#17-running-the-experiment)).
- Constructs `DockerEvaluator()` **before** constructing `LLM(...)`, so a
  missing/misconfigured Docker setup is caught before any API call is
  made (and therefore before any API cost is incurred).
- `args.out.mkdir(parents=True, exist_ok=False)` — deliberately fails if
  the output directory already exists.
- Writes `config.json` once, then streams one line per result row into
  `results.jsonl`, flushing after each problem (`f.flush()`), so a crash
  partway through a run still leaves prior problems' results intact and
  readable.
- Calls `llm.events.clear()` after each problem, relying on the `.copy()`
  calls inside `solve_problem` to have already preserved that problem's
  events inside its result rows before clearing.

---

## `analyze_results.py`

**Purpose:** turns `results.jsonl` + `config.json` into human-readable
summary statistics and plots.

### `summarize(rows) -> list[dict]` *(rewritten in Stage 3)*
- Iterates modes in the fixed order `('baseline', 'testing', 'reflexion')`.
- `accepted = [x for x in r if x['internal_pass'] is True]` — baseline
  rows have `internal_pass=None`, so they are automatically excluded
  from this and from `false_accept_count`/`false_accept_pct_of_accepted`.
- `false_accept_pct_of_accepted` is `None` (not `0`/`0.0`) when
  `accepted` is empty — a zero denominator is reported as unavailable.
- `token_rows` filters to rows where `tokens_available` is true (or
  absent, for legacy rows) **and** both token fields are not `None`;
  `mean_total_tokens` is computed only over these, and is `None` if
  `token_rows` is empty for that mode.
- `rows_missing_token_usage` reports the count of excluded rows per mode,
  and a warning is printed immediately if that count is nonzero.

### `main()`
- Loads `results.jsonl` and `config.json` from the given directory.
- Builds the `expected` set of every `(task_id, mode)` pair that *should*
  exist given `config['tasks']`, and raises `ValueError` if the actual
  set of pairs in `results.jsonl` doesn't match exactly (catches both
  incomplete and duplicated runs).
- Writes `summary.csv` via `csv.DictWriter` using the first summary row's
  keys as the fieldnames.
- Plots `accuracy.png` (bar chart with `solved/n (pct%)` labels) and
  `tokens.png` (bar chart that labels any mode with `mean_total_tokens is
  None` as `"unavailable"` instead of plotting a zero-height bar).
- Prints a paired comparison dict: counts of problems solved by
  Reflexion-but-not-Testing and vice versa, computed directly from
  `results.jsonl`, not from `summary.csv`.

---

## `autogen_agents.py` *(Path 2 addition)*

**Purpose:** Method D (AutoGen proactive review). See
[`docs/AUTOGEN_DESIGN.md`](AUTOGEN_DESIGN.md) for the full research
rationale and algorithm walkthrough; this section covers only the code
structure.

### `ProgrammerAgent` / `ReviewerAgent` (both `BaseChatAgent` subclasses)
- Hold exactly three attributes each: `_name`, `_description` (set by
  `BaseChatAgent.__init__`), and `_llm` (the shared `LLM` instance). No
  conversation history or per-problem state is ever cached on the
  instance itself.
- `on_messages(messages, cancellation_token)` reads only
  `messages[-1].content` (the single prompt string built by the
  controller for that turn), calls `self._llm.ask(stage, prompt)`
  synchronously, and wraps the result in a `Response(chat_message=
  TextMessage(...))`. `ProgrammerAgent` additionally applies
  `evaluator.strip_fence` to the result before returning it.
- `on_reset` is a required `BaseChatAgent` abstract method; implemented
  as a no-op since these agents hold no resettable state.

### `parse_review_response(text) -> dict`
- Strict: returns `{'valid': False, 'decision': None, 'feedback': ''}`
  for anything that is not valid JSON, not a JSON object, has a
  `'decision'` other than exactly `'APPROVE'`/`'REVISE'`, or has a
  non-string `'feedback'`.
- Never infers or defaults to approval under any malformed-input path.

### `build_reviewer_prompt` / `build_programmer_prompt`
- Pure string-building functions (no I/O). Both receive only the public
  problem view (`problem['prompt']`, `problem['entry_point']`), the
  current candidate, the shared generated tests, and the current
  `Evaluation` — never `problem['test']` or
  `problem['canonical_solution']`.

### `_run_autogen_review_async` / `run_autogen_review`
- `run_autogen_review` is the synchronous entry point used by
  `experiment.py` (matching `agents.iterate`'s call shape); it wraps the
  `async def _run_autogen_review_async` coroutine in `asyncio.run(...)`.
- Constructs a brand-new `ProgrammerAgent`/`ReviewerAgent` pair on every
  call (i.e., once per HumanEval problem) — this is the literal
  implementation of "no cross-problem agent state."
- `history[i]['messages']` records the literal `TextMessage` contents
  exchanged that attempt (controller→reviewer, reviewer→controller,
  controller→programmer, programmer→controller), in addition to the
  standard `llm.events` log (stages `'autogen_reviewer'` /
  `'autogen_programmer'`).
- Returns `workflow_accept` (`True` only when `evaluation.passed and
  parsed['valid'] and parsed['decision'] == 'APPROVE'`) and
  `review_calls` (count of attempts where the reviewer was actually
  invoked — can be less than `attempts` only in the `no_valid_tests`
  case, since every other attempt triggers exactly one reviewer call).

## `experiment.py` — Path 2 additions

- `solve_problem` gained an optional `autogen_fn` parameter (defaults to
  `None`). When provided, it is called with the exact same shared
  `code`/`tests` as Methods B/C, after they have both completed, and its
  result is added as a fourth `'autogen'` group before held-out scoring.
- `main()` gained `--include-autogen`; `autogen_agents` is imported
  **only** inside `main()` and only when this flag is passed, so running
  the original three-method experiment never requires
  `autogen-agentchat` to be installed.
- `config.json` gained a `'methods'` list (e.g. `['baseline', 'testing',
  'reflexion']` or with `'autogen'` appended) and an `'include_autogen'`
  boolean, so `analyze_results.py` can validate the correct set of
  `(task_id, mode)` pairs for either a three- or four-method run.
- Row construction now computes `workflow_accept` (identical to
  `internal_pass` for baseline/testing/reflexion; read from the AutoGen
  result's own `workflow_accept` for that mode) and
  `workflow_false_accept` (`workflow_accept and not heldout_pass`),
  alongside the pre-existing `false_accept` (`internal_pass and not
  heldout_pass`), so test-level and workflow-level false acceptance are
  never conflated (see `docs/AUTOGEN_DESIGN.md` Section 11).

## `analyze_results.py` — Path 2 additions

- `summarize(rows, methods=(...))` now takes an explicit `methods`
  sequence (defaulting to the original three, for backward
  compatibility) instead of a hard-coded tuple, and additionally reports
  `workflow_accepted`, `workflow_false_accept_count`, and
  `workflow_false_accept_pct_of_workflow_accepted` per mode, using
  `.get(..., internal_pass)` / `.get(..., false_accept)` fallbacks so
  older three-method result files (which predate the `workflow_accept`
  field) are still summarized correctly.
- `main()` reads `config.get('methods', ['baseline', 'testing',
  'reflexion'])` to build the expected `(task_id, mode)` validation set,
  so it transparently supports both pre-Path-2 and Path-2 result
  directories.
- An additional AutoGen-vs-Reflexion paired comparison is printed only
  when `'autogen'` is present in `methods`.

---

## Cross-cutting notes

- **No module in this repository trains or fine-tunes a model.** Every
  call into `llm.py` is an inference-only chat completion request.
- **The only two places that ever see `problem['test']` or
  `problem['canonical_solution']`** are `evaluator.DockerEvaluator.heldout`
  (host side) and the `kind='heldout'` branch of
  `sandbox_runner.evaluate` (container side). No other function in the
  repository accepts these fields as a parameter.
- **`sandbox_runner.py` is read as text, not imported**, by
  `evaluator.py`. If you add a new dependency to `sandbox_runner.py`, it
  must be available inside the `python:3.11-slim` container image, not
  just in your local `.venv`.
