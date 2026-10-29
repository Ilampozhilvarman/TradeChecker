"""Market data access for TradeChecker.

Everything that talks to yfinance lives here. The rest of the app only sees
plain dicts and the exception types below, so a yfinance failure (or a swap to
a different data source) never leaks into the agent or the web layer.
"""

import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime, timezone

import yfinance as yf

# yfinance has no usable per-call timeout, so calls run in a bounded pool and
# the caller gives up after TIMEOUT seconds. A hung upstream can occupy at most
# POOL_SIZE workers; anything beyond that queues instead of piling up threads.
TIMEOUT = 8.0
POOL_SIZE = 8

QUOTE_TTL = 60.0        # seconds
HISTORY_TTL = 900.0     # 15 minutes
SEARCH_TTL = 3600.0     # 1 hour

MAX_SYMBOLS = 10
MAX_SEARCH_RESULTS = 5

PERIODS = ("1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "ytd", "max")
INTERVALS = ("1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h", "1d", "5d", "1wk", "1mo", "3mo")

_SYMBOL_RE = re.compile(r"^[A-Z0-9.\-=^]{1,15}$")


class TradingError(Exception):
    """Base class for every failure this module reports."""


class InvalidArgument(TradingError):
    """The caller passed something we refuse to send upstream."""


class UnknownSymbol(TradingError):
    """The symbol resolved to no data at all."""


class QuoteUnavailable(TradingError):
    """Upstream answered, but without a usable price."""


class UpstreamTimeout(TradingError):
    """Upstream did not answer in time."""


_pool = ThreadPoolExecutor(max_workers=POOL_SIZE, thread_name_prefix="yfinance")
_cache = {}
_cache_lock = threading.Lock()


# ---------------------------------------------------------------- helpers


def normalize_symbol(symbol):
    """Upper-case and validate a ticker, before any network call happens."""
    if not isinstance(symbol, str):
        raise InvalidArgument("Symbol must be a string.")
    cleaned = symbol.strip().upper()
    if not cleaned:
        raise InvalidArgument("Symbol is required.")
    if not _SYMBOL_RE.match(cleaned):
        raise InvalidArgument(
            f"{symbol!r} is not a valid ticker symbol. "
            "Use letters, digits and . - = ^ only (e.g. AAPL, BRK-B, BTC-USD, ^GSPC)."
        )
    return cleaned


def _num(value):
    """Coerce to a rounded float, or None if it is missing / not a number."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return round(number, 4)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _timestamp(value):
    """Format a pandas Timestamp (or anything similar) as ISO 8601."""
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    return str(value)


def _change(price, previous_close):
    if price is None or previous_close in (None, 0):
        return None, None
    delta = round(price - previous_close, 4)
    return delta, round(delta / previous_close * 100, 4)


def _call(fn, *args):
    """Run fn in the pool, translating every failure into a TradingError."""
    future = _pool.submit(fn, *args)
    try:
        return future.result(timeout=TIMEOUT)
    except FuturesTimeout:
        raise UpstreamTimeout("Market data did not respond in time.") from None
    except TradingError:
        raise
    except Exception as exc:
        raise QuoteUnavailable("Market data is unavailable right now.") from exc


def _cache_get(key):
    with _cache_lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at < time.monotonic():
            _cache.pop(key, None)
            return None
        return value


def _cache_set(key, value, ttl):
    with _cache_lock:
        _cache[key] = (time.monotonic() + ttl, value)


def clear_cache():
    """Drop every cached entry. Used by the tests."""
    with _cache_lock:
        _cache.clear()


# ---------------------------------------------------------------- fetching


def _fetch_quote(symbol):
    ticker = yf.Ticker(symbol)

    price = previous_close = currency = exchange = None

    # fast_info can raise outright for unknown symbols, and can return None for
    # a perfectly good one, so nothing here is allowed to be fatal.
    try:
        info = ticker.fast_info
        price = _num(info.get("last_price"))
        previous_close = _num(info.get("previous_close"))
        if previous_close is None:
            previous_close = _num(info.get("regular_market_previous_close"))
        currency = info.get("currency")
        exchange = info.get("exchange")
    except Exception:
        pass

    if price is not None:
        change, change_pct = _change(price, previous_close)
        return {
            "symbol": symbol,
            "price": price,
            "previous_close": previous_close,
            "change": change,
            "change_pct": change_pct,
            "currency": currency,
            "exchange": exchange,
            "as_of": _now(),
            "source": "live",
        }

    # Fallback: the last daily close. Slower, but it is the reliable path and
    # it is also what makes an invalid ticker detectable (empty frame).
    history = ticker.history(period="5d", interval="1d")
    if history is None or len(history) == 0 or "Close" not in history:
        raise UnknownSymbol(f"No data found for {symbol}. It may not be a valid ticker.")

    closes = history["Close"].dropna()
    if len(closes) == 0:
        raise UnknownSymbol(f"No price data found for {symbol}.")

    price = _num(closes.iloc[-1])
    if price is None:
        raise QuoteUnavailable(f"No usable price for {symbol}.")
    if previous_close is None and len(closes) > 1:
        previous_close = _num(closes.iloc[-2])

    change, change_pct = _change(price, previous_close)
    return {
        "symbol": symbol,
        "price": price,
        "previous_close": previous_close,
        "change": change,
        "change_pct": change_pct,
        "currency": currency,
        "exchange": exchange,
        "as_of": _timestamp(closes.index[-1]),
        "source": "daily_close",
    }


def _fetch_history(symbol, period, interval):
    history = yf.Ticker(symbol).history(period=period, interval=interval)
    if history is None or len(history) == 0 or "Close" not in history:
        raise UnknownSymbol(f"No price history found for {symbol}.")

    closes = history["Close"].dropna()
    if len(closes) == 0:
        raise UnknownSymbol(f"No price history found for {symbol}.")

    first = _num(closes.iloc[0])
    last = _num(closes.iloc[-1])
    change, change_pct = _change(last, first)

    return {
        "symbol": symbol,
        "period": period,
        "interval": interval,
        "rows": int(len(closes)),
        "first_date": _timestamp(closes.index[0]),
        "last_date": _timestamp(closes.index[-1]),
        "start": first,
        "end": last,
        "high": _num(closes.max()),
        "low": _num(closes.min()),
        "change": change,
        "change_pct": change_pct,
    }


def _fetch_search(query, limit):
    results = yf.Search(query, max_results=limit).quotes or []
    matches = []
    for item in results:
        symbol = item.get("symbol")
        if not symbol:
            continue
        matches.append(
            {
                "symbol": symbol,
                "name": item.get("shortname") or item.get("longname"),
                "type": item.get("quoteType"),
                "exchange": item.get("exchange"),
            }
        )
    if not matches:
        raise UnknownSymbol(f"No ticker found matching {query!r}.")
    return matches


# ---------------------------------------------------------------- public API


def get_quote(symbol):
    """A single quote. Never returns a None price."""
    symbol = normalize_symbol(symbol)
    key = ("quote", symbol)

    cached = _cache_get(key)
    if cached is not None:
        return {**cached, "cached": True}

    data = _call(_fetch_quote, symbol)
    _cache_set(key, data, QUOTE_TTL)
    return {**data, "cached": False}


def get_quotes(symbols):
    """Several quotes in one go. Failures are reported per symbol, not raised."""
    if not isinstance(symbols, (list, tuple)):
        raise InvalidArgument("Symbols must be a list.")

    quotes = []
    errors = []
    for symbol in list(symbols)[:MAX_SYMBOLS]:
        try:
            quotes.append(get_quote(symbol))
        except TradingError as exc:
            errors.append({"symbol": str(symbol).upper(), "error": str(exc)})

    if not quotes and not errors:
        raise InvalidArgument("No symbols provided.")
    return {"quotes": quotes, "errors": errors}


def get_price_history(symbol, period="1mo", interval="1d"):
    """Summary statistics over a window - not raw candles."""
    symbol = normalize_symbol(symbol)

    if period not in PERIODS:
        raise InvalidArgument(f"Unsupported period {period!r}. Use one of: {', '.join(PERIODS)}.")
    if interval not in INTERVALS:
        raise InvalidArgument(
            f"Unsupported interval {interval!r}. Use one of: {', '.join(INTERVALS)}."
        )

    key = ("history", symbol, period, interval)
    cached = _cache_get(key)
    if cached is not None:
        return {**cached, "cached": True}

    data = _call(_fetch_history, symbol, period, interval)
    _cache_set(key, data, HISTORY_TTL)
    return {**data, "cached": False}


def search_symbol(query, limit=MAX_SEARCH_RESULTS):
    """Resolve a company name to tickers."""
    if not isinstance(query, str) or not query.strip():
        raise InvalidArgument("Query is required.")

    cleaned = query.strip()
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = MAX_SEARCH_RESULTS
    limit = max(1, min(limit, MAX_SEARCH_RESULTS))

    key = ("search", cleaned.lower(), limit)
    cached = _cache_get(key)
    if cached is not None:
        return {"query": cleaned, "matches": [dict(m) for m in cached], "cached": True}

    matches = _call(_fetch_search, cleaned, limit)
    _cache_set(key, matches, SEARCH_TTL)
    return {"query": cleaned, "matches": [dict(m) for m in matches], "cached": False}
