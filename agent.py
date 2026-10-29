"""The tool-calling loop, backed by OpenRouter.

Flow: send the conversation plus the tool schemas, and while the model keeps
asking for tools, run them and feed the results back. Bounded in steps, tool
calls and wall clock so a confused model can't hold a request open forever.
"""

import json
import os
import time

import requests

import env
import tools

# Pick up OPENROUTER_API_KEY / TRADECHECKER_MODEL from .env if it exists.
# Real environment variables take precedence over the file.
env.load()

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"

DEFAULT_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"

# Free-tier fallbacks. Every entry must end in ":free" or it will silently start
# costing money. Free models are capped at 20 req/min and 50 req/day (1000/day
# once the account has ever bought 10+ credits).
#
# Verified against the live API: gemma-4-31b-it:free returns 429 from the
# provider on every call, and thinkingmachines/inkling:free returns 403
# ("only available on agentic harnesses") - neither can be used here.
FALLBACK_MODELS = (
    "nvidia/nemotron-3.5-lightning:free",
    "dots-studio/dots-3-note-preview:free",
)

MAX_MODEL_FALLBACKS = 3
MAX_ATTEMPTS = 2          # one retry on a rate limit
MAX_RETRY_WAIT = 5.0      # seconds

MAX_STEPS = 5
MAX_TOOL_CALLS = 12
MAX_SECONDS = 90.0     # free reasoning models can take 20-30s per call
MAX_TOKENS = 1200

SYSTEM_PROMPT = """You are TradeChecker, a finance question-and-answer assistant.

Rules:
- Any factual claim about a price, quote or performance MUST come from a tool.
  Never state a price from memory, and never estimate one.
- If you are not certain of a ticker, call search_symbol first. Never guess a symbol.
- If a tool fails, say plainly that the price could not be fetched. Do not
  substitute a remembered or approximate number.
- Market data is delayed or end-of-day. Say so when you quote a price, and
  mention the as-of time the tool returned.
- Be concise. Two or three sentences is usually enough. Plain text only: no
  markdown, no asterisks, no backticks, no tables."""

FALLBACK_REPLY = (
    "I couldn't finish checking that within my step limit. "
    "Try asking about one ticker at a time."
)

TIMEOUT_REPLY = (
    "I fetched the market data, but the model took too long to finish the answer. "
    "Please try again - the data is cached now."
)


class AgentError(Exception):
    """Something went wrong talking to the model. Safe to show the user."""


def _model():
    return os.environ.get("TRADECHECKER_MODEL") or DEFAULT_MODEL


def current_model():
    """The model the app is configured to use."""
    return _model()


def _model_chain():
    """Primary model first, then free fallbacks. Never includes a paid model."""
    chain = [_model()]
    for model in FALLBACK_MODELS:
        if model not in chain:
            chain.append(model)
    return chain[:MAX_MODEL_FALLBACKS]


def _retry_delay(response):
    """How long to wait after a 429: honour Retry-After, else a short backoff."""
    raw = response.headers.get("Retry-After")
    if raw:
        try:
            return max(0.0, min(float(raw), MAX_RETRY_WAIT))
        except (TypeError, ValueError):
            pass
    return 1.5


def _call_openrouter(payload):
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise AgentError("OPENROUTER_API_KEY is not set on the server.")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-OpenRouter-Title": "TradeChecker",
    }

    response = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = requests.post(
                OPENROUTER_URL, json=payload, headers=headers, timeout=(5, 60)
            )
        except requests.Timeout:
            raise AgentError("The model took too long to respond.") from None
        except requests.RequestException:
            raise AgentError("Could not reach the model provider.") from None

        if response.status_code == 429 and attempt < MAX_ATTEMPTS:
            time.sleep(_retry_delay(response))
            continue
        break

    if response.status_code == 402:
        raise AgentError("The model provider account is out of credits.")
    if response.status_code == 429:
        raise AgentError(
            "The free model is rate limited. Free models allow 50 requests per day "
            "(1000 if the account has bought credits). Try again later, or set "
            "TRADECHECKER_MODEL to a paid model."
        )
    if response.status_code >= 500:
        raise AgentError("The model provider is unavailable right now.")
    if response.status_code != 200:
        raise AgentError(f"The model provider returned an error ({response.status_code}).")

    try:
        return response.json()
    except ValueError:
        raise AgentError("The model provider returned an unreadable response.") from None


def validate_model(model=None):
    """Check the model advertises tool support.

    Returns True/False, or None when the model is not in the catalogue (or the
    catalogue cannot be reached). Never raises - this is a startup warning.
    """
    model = model or _model()
    try:
        response = requests.get(OPENROUTER_MODELS_URL, timeout=(5, 20))
        response.raise_for_status()
        catalogue = response.json().get("data", [])
    except (requests.RequestException, ValueError):
        return None

    for entry in catalogue:
        if entry.get("id") == model:
            return "tools" in (entry.get("supported_parameters") or [])
    return None


def _plain_text(text):
    """Strip markdown emphasis the chat UI renders literally.

    The bubble uses textContent + pre-wrap, so `**bold**` would be shown with
    the asterisks intact. Free models ignore the "plain text" instruction often
    enough that cleaning up here is worth it.
    """
    for token in ("**", "__", "`"):
        text = text.replace(token, "")
    return text.strip()


def _parse_arguments(raw):
    if raw in (None, "", "{}"):
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        raise ValueError("arguments were not valid JSON")
    if not isinstance(parsed, dict):
        raise ValueError("arguments were not a JSON object")
    return parsed


def run(messages, max_steps=MAX_STEPS, call_model=None):
    """Run the tool loop. Returns {"reply", "tool_calls", "model"}."""
    call_model = call_model or _call_openrouter
    chain = _model_chain()

    conversation = [{"role": "system", "content": SYSTEM_PROMPT}]
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = message.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            conversation.append({"role": role, "content": content})

    executed = []
    started = time.monotonic()
    used_model = chain[0]
    timed_out = False

    for _ in range(max_steps):
        # Check the budget before starting a call, never after: otherwise a slow
        # first round burns the budget and we discard results we already have.
        if time.monotonic() - started > MAX_SECONDS:
            timed_out = True
            break

        payload = {
            "models": chain,
            "messages": conversation,
            "tools": tools.TOOLS,
            "max_tokens": MAX_TOKENS,
            # Don't let OpenRouter route to a provider that silently drops `tools`.
            # Deliberately no temperature / tool_choice: not every provider accepts
            # them, and require_parameters would then reject every route.
            "provider": {"require_parameters": True},
        }

        data = call_model(payload)
        used_model = data.get("model") or used_model

        choices = data.get("choices") or []
        message = (choices[0].get("message") if choices else None) or {}
        finish_reason = choices[0].get("finish_reason") if choices else None
        tool_calls = message.get("tool_calls") or []

        if not tool_calls:
            reply = _plain_text(message.get("content") or "")
            if not reply and finish_reason == "length":
                return {
                    "reply": "That answer ran out of room. Try a narrower question.",
                    "tool_calls": executed,
                    "model": used_model,
                }
            return {
                "reply": reply or "I don't have an answer for that.",
                "tool_calls": executed,
                "model": used_model,
            }

        conversation.append(
            {
                "role": "assistant",
                "content": message.get("content"),
                "tool_calls": tool_calls,
            }
        )

        for call in tool_calls:
            function = call.get("function") or {}
            name = function.get("name") or ""
            call_id = call.get("id") or f"call_{len(executed)}"

            if len(executed) >= MAX_TOOL_CALLS:
                result = {"ok": False, "error": "Too many tool calls in one request."}
                arguments = {}
            else:
                try:
                    arguments = _parse_arguments(function.get("arguments"))
                    result = tools.execute(name, arguments)
                except ValueError as exc:
                    arguments = {}
                    result = {"ok": False, "error": f"Could not read tool arguments: {exc}."}

            executed.append(
                {"name": name, "arguments": arguments, "ok": bool(result.get("ok"))}
            )
            conversation.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": name,
                    "content": json.dumps(result),
                }
            )

        if time.monotonic() - started > MAX_SECONDS:
            timed_out = True
            break

    return {
        "reply": TIMEOUT_REPLY if timed_out else FALLBACK_REPLY,
        "tool_calls": executed,
        "model": used_model,
    }
