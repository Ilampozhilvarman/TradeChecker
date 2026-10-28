from flask import Flask, jsonify, render_template
import trading

app = Flask(__name__)

@app.route("/")
def home():
    return jsonify({"Hello": "Hi"})

@app.route("/price/<symbol>")
def price(symbol):
    test = trading.get_ticker_price(symbol)
    return jsonify({"Price": test})

if __name__ == "__main__":
    app.run(debug=True)
