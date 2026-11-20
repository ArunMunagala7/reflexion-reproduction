# Proactive AutoGen Review vs. Failure-Triggered Reflexion (HumanEval)

**Authors:** Arun Munagala and Anuj Prakash
**Course:** Introduction to Computer Engineering, Indiana University
**Branch:** `path2-autogen` (this work is not on `main`)

## 1. What this is

A Path 2 investigation that follows on from our Presentation 2 (Reflexion).
Reflexion revises code only after its own generated tests fail. We added a
proactive **AutoGen reviewer agent** that reviews every candidate, including
candidates whose tests already pass, and compared four methods on 20
HumanEval tasks:

| Method | Revision trigger | Stops when |
|---|---|---|
| A. Baseline | none (one generation) | after 1 generation |
| B. Testing | generated tests fail | internal tests pass, or 3 attempts |
| C. Reflexion | generated tests fail, then a written reflection | internal tests pass, or 3 attempts |
| D. AutoGen | reviewer consulted on every attempt | internal tests pass **and** reviewer says `APPROVE`, or 3 attempts |

All arms share one initial candidate per task; B, C and D share one generated
test suite. The official HumanEval tests are run only after every arm has
stopped and are never shown to any agent. Arm D also differs from C in its
stop rule and call budget, so this is a whole-workflow comparison.

The full original README, with design rationale and detailed sections, is
preserved at [`docs/README_full.md`](docs/README_full.md).

## 2. Headline results

From [`verification/final_summary.csv`](verification/final_summary.csv)
(`gpt-4o-mini`, temperature 0, seed 42, max 3 candidate attempts, 20 tasks):

| Method | Official pass | Mean LLM calls | Mean total tokens | Mean time |
|---|---|---|---|---|
| Baseline | 17/20 (85%) | 1.0 | 392.2 | 3.18 s |
| Testing | 17/20 (85%) | 2.8 | 1432.4 | 8.40 s |
| Reflexion | 19/20 (95%) | 3.5 | 1996.75 | 9.62 s |
| AutoGen | 17/20 (85%) | 7.0 | 5163.75 | 19.52 s |

- **Reviewer:** 60 of 60 reviewer decisions were `REVISE`, 0 were `APPROVE`
  (all 60 were valid JSON). Every AutoGen run stopped at the 3-attempt limit,
  and 0/20 candidates were workflow-accepted.
- **Paired difference:** only HumanEval/105 and /163 differ (Reflexion solved
  both, AutoGen neither). Exact two-sided McNemar p = 0.5, so the run does not
  establish a difference.
- **Confound:** the sandbox image lacks `numpy`. On /105 the shared initial
  candidate's `numpy` import decides the outcome; on /163 AutoGen removed the
  import and still failed on logic.
- **Accounting:** shared initial-code and test-generation calls are counted
  in every arm that uses them. Do not sum token totals across methods to
  estimate API cost.

Full analysis: [`verification/final_experiment_report.md`](verification/final_experiment_report.md).

## 3. Repository layout

```
experiment.py          Paired runner: shared code/tests, the four arms, held-out scoring
agents.py              Methods A-C: initial code, test generation, Testing and Reflexion loops
autogen_agents.py      Method D: AutoGen AgentChat Programmer and Reviewer agents
evaluator.py           Docker evaluator and generated-test validation
sandbox_runner.py      Code executed inside the container
llm.py                 OpenAI-compatible client with token/latency logging
dataset.py             Downloads and loads HumanEval
analyze_results.py     summary.csv, accuracy/token plots, paired comparisons
tests/                 100 software tests (harness, regressions, AutoGen, Docker)
verification/          Committed evidence: reports, summaries, test logs
docs/                  Design document, implementation guide, full original README
```

`data/` and `results/` are git-ignored. The raw `results.jsonl` files are not
committed; the compact evidence in `verification/` is.

## 4. Reproducing the run

Requirements: Docker, Python (the recorded test runs used 3.11), and an
OpenAI API key. A fresh benchmark run makes paid API calls.

```bash
git clone https://github.com/ArunMunagala7/reflexion-reproduction.git
cd reflexion-reproduction
git checkout path2-autogen
pip install -r requirements.txt
docker pull python:3.11-slim        # required; the evaluator refuses to start without it
export OPENAI_API_KEY=...           # see .env.example

python dataset.py                   # downloads data/HumanEval.jsonl.gz
python experiment.py --count 20 --seed 42 --max-attempts 3 \
  --model gpt-4o-mini --include-autogen --out results/final_autogen
python analyze_results.py results/final_autogen
```

- `--out` must name a directory that does **not** already exist.
- Without `--include-autogen`, only Methods A-C run.
- LLM API outputs are not guaranteed to be identical on a later run, even at
  temperature 0. The configuration of the recorded run is in
  [`verification/final_run_config.json`](verification/final_run_config.json).
- The two-task pilot (HumanEval/0 and /1) used
  `python experiment.py --pilot --include-autogen --max-attempts 3 --model gpt-4o-mini --out results/pilot_autogen`.

Software tests (no API key needed):

```bash
python -m pytest -q
```

The recorded full run gave `100 passed`
([`verification/copilot_full_suite_e2e.txt`](verification/copilot_full_suite_e2e.txt)).
The 17 Docker-backed tests are skipped, not failed, if Docker or
`python:3.11-slim` is unavailable.

## 5. LLMs and tools

- **gpt-4o-mini** (temperature 0): every LLM role, including both AutoGen agents
- **autogen-agentchat 0.7.5** and **autogen-core 0.7.5** (pinned): `BaseChatAgent`, `TextMessage`, `Response`
- **ChatGPT** and **GitHub Copilot**: starter implementation, development assistance and the verification cycle
- **Docker** `python:3.11-slim`: sandboxed execution (no network, read-only filesystem)
- **pytest**: software test suite

## 6. Where the evidence lives

| File in `verification/` | Contents |
|---|---|
| `final_experiment_report.md` | Full analysis of the 20-task run, reviewer classification, limitations |
| `final_summary.csv` | Per-method accuracy, acceptance, calls, tokens, time |
| `final_task_outcomes.csv` | Per-task official and internal pass/fail for all four methods |
| `final_run_config.json` | Model, seed, task list, dataset SHA-256, Docker image |
| `pilot_findings_report.md`, `pilot_summary.csv` | Two-task pilot and the incorrect generated tests it exposed |
| `copilot_verification_findings.md` | Student-directed verification of five areas |
| `copilot_corrections_report.md` | The four corrections and their before/after evidence |
| `copilot_regression_before.txt` / `_after.txt` | 6 failed, 5 passed before corrections; 67 passed after |
| `copilot_full_suite_e2e.txt` | Final suite: 100 passed |
| `docker_integration_report.md` | Real Docker integration tests |
| `NOTES.md` | Early verification notes from the starter implementation |

## References

- Schick et al., *Toolformer*, arXiv:2302.04761
- Shinn et al., *Reflexion*, arXiv:2303.11366
- Wu et al., *AutoGen*, arXiv:2308.08155
