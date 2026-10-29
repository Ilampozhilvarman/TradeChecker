"""The tool contract exposed to the model.

Two things live here: the JSON Schema the model sees, and the dispatcher that
turns a tool call into a result. The dispatcher never raises - every failure
becomes {"ok": false, "error": ...} so the model can explain it to the user
instead of the request blowing up.
"""

import trading

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_quote",
            "description": (
                "Get the latest price for one ticker symbol, plus the previous close "
                "and the change. Prices are delayed or end-of-day, never real-time. "
                "Prefer get_quotes when asking about more than one ticker."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Ticker symbol, e.g. AAPL, MSFT, BRK-B, BTC-USD, ^GSPC.",
                    }
                },
                "required": ["symbol"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_quotes",
            "description": (
                "Get the latest price for up to 10 ticker symbols in a single call. "
                "Use this for comparisons instead of calling get_quote repeatedly."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "symbols": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Ticker symbols, up to 10.",
                        "minItems": 1,
                        "maxItems": 10,
                    }
                },
                "required": ["symbols"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_price_history",
            "description": (
                "Summarise how a ticker moved over a period: start, end, high, low and "
                "percentage change. Returns a summary, not a list of daily prices. "
                "Prices are delayed or end-of-day."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Ticker symbol, e.g. AAPL."},
                    "period": {
                        "type": "string",
                        "enum": list(trading.PERIODS),
                        "description": "How far back to look. Defaults to 1mo.",
                    },
                    "interval": {
                        "type": "string",
                        "enum": list(trading.INTERVALS),
                        "description": "Bar size. Defaults to 1d.",
                    },
                },
                "required": ["symbol"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_symbol",
            "description": (
                "Find ticker symbols by company or asset name, e.g. 'apple' -> AAPL. "
                "Call this whenever you are not certain of the exact ticker. Never "
                "guess a symbol."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Company or asset name, e.g. 'apple', 'tesla', 'bitcoin'.",
                    }
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
]

TOOL_NAMES = tuple(tool["function"]["name"] for tool in TOOLS)


class _ArgumentError(Exception):
    pass


def _require_symbol(arguments):
    symbol = arguments.get("symbol")
    if not isinstance(symbol, str) or not symbol.strip():
        raise _ArgumentError("`symbol` is required and must be a non-empty string.")
    return symbol


def _validate(name, arguments):
    """Coerce and bound-check arguments before they reach trading.py."""
    if name == "get_quote":
        return {"symbol": _require_symbol(arguments)}

    if name == "get_quotes":
        symbols = arguments.get("symbols")
        if isinstance(symbols, str):
            symbols = [symbols]
        if not isinstance(symbols, list) or not symbols:
            raise _ArgumentError("`symbols` is required and must be a non-empty list.")
        cleaned = [s for s in symbols if isinstance(s, str) and s.strip()]
        if not cleaned:
            raise _ArgumentError("`symbols` must contain at least one ticker string.")
        if len(cleaned) > trading.MAX_SYMBOLS:
            cleaned = cleaned[: trading.MAX_SYMBOLS]
        return {"symbols": cleaned}

    if name == "get_price_history":
        args = {"symbol": _require_symbol(arguments)}
        period = arguments.get("period")
        interval = arguments.get("interval")
        if period is not None:
            args["period"] = period
        if interval is not None:
            args["interval"] = interval
        return args

    if name == "search_symbol":
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            raise _ArgumentError("`query` is required and must be a non-empty string.")
        return {"query": query}

    raise _ArgumentError(f"Unknown tool {name!r}.")


_DISPATCH = {
    "get_quote": trading.get_quote,
    "get_quotes": trading.get_quotes,
    "get_price_history": trading.get_price_history,
    "search_symbol": trading.search_symbol,
}


def execute(name, arguments):
    """Run one tool call. Always returns a dict, never raises."""
    handler = _DISPATCH.get(name)
    if handler is None:
        return {"ok": False, "error": f"Unknown tool {name!r}."}

    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        return {"ok": False, "error": "Tool arguments must be a JSON object."}

    try:
        kwargs = _validate(name, arguments)
    except _ArgumentError as exc:
        return {"ok": False, "error": str(exc)}

    try:
        result = handler(**kwargs)
    except trading.TradingError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception:
        return {"ok": False, "error": "Unexpected error while fetching market data."}

    return {"ok": True, "data": result}
