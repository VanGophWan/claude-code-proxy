# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A FastAPI proxy server that translates Claude's `/v1/messages` API to OpenAI-compatible API calls. Enables Claude Code CLI to work with any OpenAI-compatible provider (OpenAI, Azure OpenAI, Ollama, etc.).

## Architecture

```
src/
├── main.py                   # FastAPI app entry point, uvicorn runner
├── api/
│   └── endpoints.py          # API routes: /v1/messages, /health, /test-connection
├── conversion/
│   ├── request_converter.py  # Claude request → OpenAI request format
│   └── response_converter.py # OpenAI response → Claude response format (streaming + non-streaming)
├── core/
│   ├── config.py             # Config singleton (env vars, model mappings, custom headers)
│   ├── client.py             # AsyncOpenAI / AsyncAzureOpenAI client wrapper with cancellation
│   ├── constants.py          # Shared string constants (roles, content types, event types)
│   ├── logging.py            # Logging configuration
│   └── model_manager.py      # Claude model name → OpenAI model name mapping
├── models/
│   ├── claude.py             # Pydantic models for Claude API request/response schemas
│   └── openai.py             # OpenAI pydantic models (currently empty)
tests/
└── test_main.py              # Integration tests (requires running server)
```

### Request Flow

1. Claude Code CLI sends a Claude-format request to `/v1/messages`
2. `endpoints.py` validates the API key, generates a request ID
3. `request_converter.py` converts the Claude request into OpenAI chat completion format
4. `client.py` sends the request to the configured OpenAI-compatible API
5. `response_converter.py` converts the OpenAI response back into Claude SSE streaming format

### Key Design Decisions

- **Model mapping**: Claude model names containing "haiku"/"sonnet"/"opus" are mapped to configurable SMALL/MIDDLE/BIG_MODEL env vars. Models starting with `gpt-`/`o1-`/`ep-`/`doubao-`/`deepseek-` pass through as-is.
- **Streaming**: SSE events are converted incrementally — OpenAI streaming chunks → Claude `content_block_delta`/`message_delta` events. Two converter functions exist: `convert_openai_streaming_to_claude` (basic) and `convert_openai_streaming_to_claude_with_cancellation` (adds client disconnect detection + usage tracking from final chunk). The cancellation variant is the one actually used in production.
- **Tool call streaming**: OpenAI sends tool calls as delta chunks per `index`. The converter buffers `id`/`name`/`arguments` per index, emits `content_block_start` only when both `id` and `name` are available, and sends `input_json_delta` events with the first valid JSON parse of the accumulated arguments buffer (sent once, not per-chunk).
- **Cancellation**: Tracks active requests by `request_id` (UUID) in `OpenAIclient.active_requests` dict of `asyncio.Event`s. Client disconnect sets the event; the stream loop checks `is_disconnected()` before each chunk and calls `cancel_request()` to abort the OpenAI stream.
- **API key validation**: Optional `ANTHROPIC_API_KEY` env var for client authentication (checked via `X-API-Key` header or `Authorization: Bearer`). If unset, any API key is accepted. Validation is implemented as a FastAPI dependency (`validate_api_key`) on message endpoints.
- **Custom headers**: `CUSTOM_HEADER_*` env vars are injected into all upstream API requests. The `_` in the env var name is converted to `-` for the HTTP header name.
- **Azure support**: If `AZURE_API_VERSION` env var is set, `AsyncAzureOpenAI` client is used instead of `AsyncOpenAI`, with `azure_endpoint` from `OPENAI_BASE_URL`.
- **Error classification**: `OpenAIclient.classify_openai_error()` provides human-readable guidance for common errors (region restrictions, invalid keys, rate limits, model not found, billing issues).
- **Thinking mode**: The `thinking` field in the Claude request model is accepted but not currently converted — the converter ignores it, so the upstream model receives a standard chat completion request without thinking/ reasoning effort parameters.

## Common Commands

```bash
# Install dependencies
uv sync
# or: pip install -r requirements.txt

# Run with dev deps (formatters, type checker)
uv sync --group dev

# Start the proxy server
uv run claude-code-proxy
# or: python start_proxy.py
# or: python src/main.py

# Format code
uv run black src/
uv run isort src/

# Type check
uv run mypy src/

# Run integration tests (server must be running first)
uv run pytest tests/ -v
# Run a single test:
uv run pytest tests/test_main.py::<test_name> -v
# Run standalone test script:
uv run python src/test_claude_to_openai.py

# Build standalone binary (see BINARY_PACKAGING.md)
pyinstaller claude-code-proxy.spec

# Docker
docker compose up -d
```

## Configuration

Copy `.env.example` to `.env` and set at minimum `OPENAI_API_KEY`. See `.env.example` for all options including model mapping, custom headers, and provider-specific setup.

Windows batch scripts are provided in the project root:
- `start_proxy.bat` — starts the proxy
- `stop_proxy.bat` — stops it (kills the python process running `src/main.py`)

## Model Mapping Behavior

| Claude Request Contains | Uses Env Var       | Default       |
|------------------------|--------------------|---------------|
| "haiku"                | `SMALL_MODEL`      | gpt-4o-mini   |
| "sonnet"               | `MIDDLE_MODEL`     | =BIG_MODEL    |
| "opus"                 | `BIG_MODEL`        | gpt-4o        |
| anything else          | `BIG_MODEL`        | gpt-4o        |
| gpt-/o1-/ep-/doubao-/deepseek- prefix | returned as-is (passthrough) | |

## Key Implementation Details

### Request Conversion (`src/conversion/request_converter.py`)
- Claude `system` (string or list of text blocks) → single OpenAI system message
- Assistant messages with `tool_use` blocks → OpenAI `tool_calls` array
- Messages after assistant containing `tool_result` blocks → OpenAI `tool` role messages (paired by `tool_use_id`)
- `max_tokens` is clamped between `MIN_TOKENS_LIMIT` (100) and `MAX_TOKENS_LIMIT` (4096)
- Image blocks are converted: Claude base64 image → OpenAI `image_url` with `data:` URI

### Response Conversion: Two Streaming Functions

**`convert_openai_streaming_to_claude`** (src/conversion/response_converter:83) — Basic version, not used in production routes.

**`convert_openai_streaming_to_claude_with_cancellation`** (src/conversion/response_converter:216) — Production version. Differences from basic:
- Checks `http_request.is_disconnected()` per chunk to detect client disconnect
- Calls `openai_client.cancel_request(request_id)` on disconnect and breaks out
- Tracks `usage` from the final chunk's `usage` field (includes `cache_read_input_tokens` from `prompt_tokens_details.cached_tokens`)
- Catches HTTPException with status 499 (cancellation) to emit a clean error event

### Cancellation Pattern (`src/core/client.py`)
- `OpenAIclient.active_requests` is a `Dict[str, asyncio.Event]`
- `create_chat_completion` uses `asyncio.wait()` on both the completion task and a cancel-event waiter — whichever finishes first wins
- `create_chat_completion_stream` checks `active_requests[id].is_set()` between chunks
- `cancel_request(request_id)` sets the event; returns `True` if the request was tracked

### Config (`src/core/config.py`)
- Singleton pattern: module imports `config = Config()` at load time; all other modules import from `src.core.config import config`
- Validates `OPENAI_API_KEY` is present at import time (exits with error if missing)
- `get_custom_headers()` extracts `CUSTOM_HEADER_*` env vars and converts `_` to `-` for HTTP header names