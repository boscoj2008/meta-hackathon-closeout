# closeout-hackathon — Experiment 01: API smoke test

Minimal independent probe of Meta Model API connectivity and one
synthetic financial-reasoning check. No app, no database, no integrations.

## Prereqs

- Python 3.9+
- `MODEL_API_KEY` in the environment (never in code)

## Setup (project-local env)

```sh
cd closeout-hackathon
python3 -m venv .venv
source .venv/bin/activate
```

No third-party packages required (standard library only).

## Run

```sh
# Offline tests (no key, no network, no credits):
python3 experiments/hypothesis_selection/test_evaluation.py -v

# Live runs (each scenario costs one API call; default runs the first only):
export MODEL_API_KEY="your-key-here"  # MUSE_API_KEY also accepted
python3 experiments/hypothesis_selection/run.py
python3 experiments/hypothesis_selection/run.py --scenario synthetic-void-002
python3 experiments/hypothesis_selection/run.py --all   # all five, prints results table

# Offline evaluation of a pasted model answer (no key, no credits):
python3 experiments/hypothesis_selection/run.py --scenario synthetic-drop-001 \
  --eval-text '{"uncertainty": "...", "clarification_question": "...?"}'
```

## What it does

1. Confirms the key exists (presence only, never printed).
2. Loads the first synthetic scenario from `scenarios.json`.
3. Sends one Responses API request (`POST https://api.meta.ai/v1/responses`,
   model `muse-spark-1.3`).
4. Prints the model text and deterministically validates that it contains
   `uncertainty` and exactly one `clarification_question` ending in `?`.
5. Reports missing/malformed fields; exits non-zero on failure.

Model output is advisory only — no financial conclusion is drawn from it.
