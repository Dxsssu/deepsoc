# SOC Long-Term Memory Demo

This directory contains a fully isolated demo prototype for validating whether Long-Term Memory can support SOC question answering as a knowledge base.

## Demo scope

- Interactive SOC Q&A only
- Four memory routes
  - `Semantic Memory`
  - `Factual Memory`
  - `Procedural Memory`
  - `Episodic Memory`
- LLM-based intent routing only
- Optional LLM-based answer generation
- All code, data, pages, and notes remain isolated inside `demo/`

## Directory structure

```text
demo/
  app.py
  README.md
  data/
  src/
  static/
  templates/
```

## Run

From the project root:

```powershell
.venv\Scripts\python.exe demo\app.py
```

Then open:

```text
http://127.0.0.1:5050
```

## LLM configuration

The demo supports two separate LLM paths:

1. Intent routing
2. Answer generation

If the intent-routing key is not configured, intent routing will fail and the request will not continue.

### Intent routing

Set these variables in `demo/.env` or your shell:

- `SOC_DEMO_INTENT_API_KEY`
- `SOC_DEMO_INTENT_API_URL` (optional, default: `https://api.xty.app/v1/chat/completions`)
- `SOC_DEMO_INTENT_MODEL` (optional, default: `gpt-4o`)
- `SOC_DEMO_INTENT_TIMEOUT` (optional, default: `20`)

### Answer generation

If either of the following is present, the demo will try to use an LLM to generate a more natural answer:

- `OPENAI_API_KEY`
- `LLM_API_KEY`

Optional variables:

- `OPENAI_BASE_URL`
- `LLM_BASE_URL`
- `SOC_DEMO_CHAT_MODEL`

If answer-generation configuration is missing, the demo falls back to local templated answers.
