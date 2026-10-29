from flask import Flask, jsonify, render_template, request

import agent
import trading

app = Flask(__name__)

MAX_HISTORY = 20
MAX_MESSAGE_CHARS = 4000


def _clean_history(raw):
    """Accept only well-formed user/assistant turns, bounded in count and size."""
    if not isinstance(raw, list):
        return []

    cleaned = []
    for message in raw[-MAX_HISTORY:]:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = message.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            cleaned.append({"role": role, "content": content[:MAX_MESSAGE_CHARS]})
    return cleaned


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/price/<symbol>")
def price(symbol):
    """Raw quote lookup, kept for debugging the data layer on its own."""
    try:
        return jsonify(trading.get_quote(symbol))
    except trading.InvalidArgument as exc:
        return jsonify(error=str(exc)), 400
    except trading.UnknownSymbol as exc:
        return jsonify(error=str(exc)), 404
    except trading.TradingError as exc:
        return jsonify(error=str(exc)), 502


@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True) or {}
    text = data.get("text")
    text = text.strip() if isinstance(text, str) else ""

    if not text:
        return jsonify(error="No text provided"), 400

    messages = _clean_history(data.get("messages"))
    messages.append({"role": "user", "content": text[:MAX_MESSAGE_CHARS]})

    try:
        result = agent.run(messages)
    except agent.AgentError as exc:
        return jsonify(error=str(exc)), 502

    return jsonify(
        reply=result["reply"],
        tool_calls=result["tool_calls"],
        model=result["model"],
    )


@app.route("/send", methods=["POST"])
def send():
    """Alias for /chat, kept so older callers keep working."""
    return chat()


if __name__ == "__main__":
    supported = agent.validate_model()
    if supported is False:
        print(
            f"WARNING: model {agent.current_model()!r} does not advertise tool support. "
            "Price lookups will not work. Set TRADECHECKER_MODEL to a tool-capable model."
        )
    elif supported is None:
        print(f"WARNING: could not verify tool support for model {agent.current_model()!r}.")

    app.run(debug=True)
