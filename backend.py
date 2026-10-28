from flask import Flask, jsonify, render_template, request
import trading

app = Flask(__name__)

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/price/<symbol>")
def price(symbol):
    test = trading.get_ticker_price(symbol)
    return jsonify({"Price": test})

@app.route("/send", methods=["POST"])
def send():
    data = request.get_json()
    text = data.get("text", "").strip()

    if not text:
        return jsonify(error="No text provided"), 400

    reply = f"You said: {text}"
    return jsonify(reply=reply)

if __name__ == "__main__":
    app.run(debug=True)
