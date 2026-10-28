import yfinance as yf
from flask import Flask, jsonify, render_template

app = Flask(__name__)

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/price/<symbol>")
def price(symbol):
    try:
        p = yf.Ticker(symbol.upper()).fast_info["last_price"]
        return jsonify({"symbol": symbol.upper(), "price": p})
    except Exception:
        return jsonify({"error": "Could not fetch price"}), 502

if __name__ == "__main__":
    app.run(debug=True)
