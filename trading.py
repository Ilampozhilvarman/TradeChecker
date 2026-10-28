import yfinance as yf

def get_ticker_price(ticker):
    t = yf.Ticker(ticker)
    return t.fast_info["last_price"]
