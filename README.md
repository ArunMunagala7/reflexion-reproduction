# Reflexion programming reproduction (Presentation 3)

A small-scale **adaptation** of the programming experiment in Shinn et al., *Reflexion: Language Agents with Verbal Reinforcement Learning* (NeurIPS 2023). This project has three paired arms: one-shot generation, test-driven revision without explicit reflection, and test-driven revision with an explicit verbal lesson. It is not an exact replication of the paper's GPT-4 or Rust experiments.

## Quick start (macOS / Linux)

Install Python 3.10+ and Docker Desktop / Docker Engine. Start Docker and run these commands from the project directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pytest -q
python dataset.py
docker pull python:3.11-slim
```

Use an OpenAI-compatible chat completion endpoint that supports `temperature=0` and `max_tokens`. Set a valid model ID available to your API account (e.g., `gpt-4.1-mini` if available). Copilot Chat alone is not a programmatic inference endpoint. Do not put your key in the repository, prompt, slides or logs.

```bash
# On macOS/zsh or Linux/bash, enter your key without displaying it.
read -rs 'OPENAI_API_KEY?API key: ' # zsh only; bash users: read -rs OPENAI_API_KEY
export OPENAI_API_KEY
export LLM_MODEL=gpt-4.1-mini # replace if needed
# Optional for an OpenAI-compatible provider: export OPENAI_BASE_URL=...
python experiment.py --pilot --out results/pilot
python analyze_results.py results/pilot
```

For bash, use `read -rs OPENAI_API_KEY` instead of the zsh prompt syntax. Alternatively set the key in your terminal using your provider's recommended secret-management workflow. No API key is needed for pytest.

**Inspect the pilot before proceeding:** check API charges, prompt logs, valid generated tests, Docker execution, test failures and token usage. Pilot tasks HumanEval/0 and /1 are excluded from the main experiment. Fix genuine issues, record what changed and rerun pilot in a NEW output directory. Output directories must not exist; this protects previous results from overwriting.

```bash
python experiment.py --count 20 --seed 42 --max-attempts 3 --out results/final
python analyze_results.py results/final
```

For a smaller complete experiment, decide the sample size before reviewing results, e.g. `--count 10`. `config.json` records task IDs, dataset SHA256, model, and settings. The runner fails if an API/infrastructure error interrupts it; partial output is retained, but the analysis refuses incomplete runs. For a fresh run choose a different directory. Record any changes and exclusions explicitly. Do not select a subset based on which arm wins.

## What is actually compared

- Baseline submits the shared initial solution once.
- Testing uses the shared initial solution and 1–6 specification-derived model-generated asserts. A failed test leads to a direct revision.
- Reflexion uses the same initial solution and internal tests. Each failed attempt (before the last attempt) produces a separate natural-language reflection, included in the next revision prompt. Keep the latest lesson, not an unlimited history.
- At most three total candidate solutions for each iterative arm. Stop on internal pass, no valid test suite, or exhausted attempts. All three solutions are fixed before any held-out evaluation. Invalid tests do not count as passing.
- Tests are generated from the public specification only, not the initial code. Test generation is shared by both iterative arms; generated tests are never repaired or changed within an arm. Test parsing requires top-level asserts containing calls to the entry point; it cannot ensure correct expected outputs or rule out every vacuous test. Candidate code is generated as a complete module, not a HumanEval body completion.
- The benchmark reference solution and official tests are excluded from LLM prompts. Evaluation uses the official `check(candidate)` function. HumanEval is public; exclusion from prompts is not proof that the model has never seen the data during pretraining.
- Model, task IDs, initial code, internal tests, maximum attempts, generation temperature and final benchmark scoring are shared where applicable. Explicit reflection adds an LLM call: **equal candidate-attempt budgets are not equal inference budgets**. No claim of exact causal isolation under equal token budgets. The comparison isolates the added explicit reflection stage in this harness, not all internal reasoning.

## Safety

The experiment uses a disposable Docker container to execute candidate code. The container has no network, host mounts or credentials, runs as nonroot, and has resource and time limits. Docker is a risk reduction measure, not a guarantee of security. Run in a dedicated disposable VM or otherwise hardened environment if possible, especially for unknown/adversarial code. Do not disable isolation or execute model-generated code directly on your host. The runner executes candidate and test code within the same process and is not designed to resist malicious manipulation of the score. The official HumanEval repository also warns about untrusted code execution.

## Results and interpretation

- `results.jsonl`: final answers, internal test and reflection histories, held-out outcomes, prompts and responses, token counts, stop reasons.
- `summary.csv`: counts, final pass rates, mean attempts, mean logical API calls/tokens, solve latency, false acceptances.
- `accuracy.png`, `tokens.png`: charts from the actual run.
- False acceptance in this project: number of final solutions that passed internal tests but failed held-out tests, divided by the number that passed internal tests. This is an explicitly defined project metric, not a claim to match the paper's denominator. A zero denominator is reported as missing.
- Token/call totals are attributed per arm (the shared initial generation and shared tests are counted for each arm that logically uses them, even though they are called once). Solve latency is similarly attributed; excludes held-out scoring. Provider usage may be missing; tokens are then recorded as zero, so check usage data before interpreting cost. No dollar costs are computed automatically. Small samples and a single run cannot establish general superiority; report paired task outcomes and model differences.

## Verification and assignment evidence

`python -m pytest -q` runs deterministic mock-agent tests and trusted static code fixtures. These tests validate the harness, not model accuracy. The initial evaluator test suite found that directly calling the internal runner with zero tests returned a pass. The outer evaluator already rejected empty tests, but the runner lacked its own guard. We added a guard and reran the suite. See `verification/initial_tests.txt`, `verification/revised_tests.txt` and `verification/NOTES.md`. Keep the real conversation about any additional issue you identify and ask the LLM to verify; do not fabricate a prompt or claim authorship of LLM-generated verification decisions. The current checks are generated by ChatGPT; the student still needs to inspect, direct a verification task, and decide whether the evidence is adequate.

## References

- Shinn et al. (2023), [Reflexion](https://papers.nips.cc/paper_files/paper/2023/file/1b44b878bb782e6954cd888628510e90-Paper-Conference.pdf), especially Section 4.3 and Tables 1–3.
- [Reflexion original repository](https://github.com/noahshinn/reflexion).
- Chen et al. (2021), [HumanEval official repository](https://github.com/openai/human-eval) (dataset downloaded separately; MIT license).

## Current status

Starter implementation and local unit tests completed in ChatGPT's environment. Actual Docker-based smoke test, dataset download and real API benchmark runs remain to be performed on the user's machine. No empirical LLM performance results are included.
