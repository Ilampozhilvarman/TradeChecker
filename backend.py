from flask import Flask, jsonify, render_template

app = Flask(__name__)

@app.route("/")
def home():
    return jsonify({"Hello": "Hi"})

@app.route("/price/<symbol>")
def price(symbol):
    pass

if __name__ == "__main__":
    app.run(debug=True)
