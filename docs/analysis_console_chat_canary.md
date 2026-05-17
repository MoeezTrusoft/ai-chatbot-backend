# Chat Lab LLM Source Canary

This canary verifies that normal BookCraft sales/service responses are written by Claude/Sonnet, not templates or RAG fast-path output.

## Run

Start backend first:

```bash
set -a
source .env.production.local
set +a

uv run uvicorn bookcraft.api.main:app --host 0.0.0.0 --port 8000 --reload

Then run:

set -a
source .env.production.local
set +a

uv run python scripts/data/run_chat_lab_llm_source_canary.py \
  --base-url "http://localhost:8000" \
  --poll-seconds 30
Pass criteria
valid=True
10/10 source_ok=True
10/10 text_ok=True
10/10 service_ok=True
source is claude_sonnet or claude_sonnet_reduced
no rag_fast_path
no template_no_adapter
no Source:
no quote engine / deterministic engine / backend wording
