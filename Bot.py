"""EURUSD 5m bearish market structure shift alert -> Telegram.

Alert only. It never places trades.
Rule (v1): in an uptrend (higher highs + higher lows), alert when a 5m candle
CLOSES below the most recent swing low (the last higher low).
"""
import json
import os
import sys

import pandas as pd
import requests
import yfinance as yf

SYMBOL = "EURUSD=X"
NAME = "EURUSD"
LOOKBACK = 3        # candles each side to confirm a swing high/low
CHECK_LAST = 4      # look back this many closed candles (covers late runs)
STATE_FILE = "state.json"

TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


def send(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    r = requests.post(url, data={"chat_id": CHAT_ID, "text": text}, timeout=20)
    r.raise_for_status()


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def get_candles():
    df = yf.Ticker(SYMBOL).history(period="2d", interval="5m")
    if df is None or df.empty:
        return None
    df.index = pd.to_datetime(df.index).tz_convert("UTC")
    # Drop the candle that is still forming
    now = pd.Timestamp.now(tz="UTC")
    if df.index[-1] + pd.Timedelta(minutes=5) > now:
        df = df.iloc[:-1]
    return df


def pivots(d):
    highs, lows = [], []
    h, l = d["High"].values, d["Low"].values
    for i in range(LOOKBACK, len(d) - LOOKBACK):
        if h[i] == h[i - LOOKBACK:i + LOOKBACK + 1].max():
            highs.append((i, float(h[i])))
        if l[i] == l[i - LOOKBACK:i + LOOKBACK + 1].min():
            lows.append((i, float(l[i])))
    return highs, lows


def shift_at(df, b):
    """Return the broken swing-low level if candle b caused a bearish shift."""
    d = df.iloc[:b + 1]
    highs, lows = pivots(d)
    if len(highs) < 2 or len(lows) < 2:
        return None
    uptrend = highs[-1][1] > highs[-2][1] and lows[-1][1] > lows[-2][1]
    if not uptrend:
        return None
    level = lows[-1][1]
    c = d["Close"].values
    if len(c) >= 2 and c[-1] < level <= c[-2]:
        return level
    return None


def main():
    if not TOKEN or not CHAT_ID:
        sys.exit("Missing TELEGRAM_TOKEN or TELEGRAM_CHAT_ID")

    if os.environ.get("TEST_MESSAGE") == "1":
        send("✅ Test: your EURUSD 5m alert bot is connected.")
        return

    state = load_state()
    save_state(state)  # make sure the file exists

    df = get_candles()
    if df is None or len(df) < 20:
        print("No data (market closed or data gap).")
        return

    last_alert = pd.Timestamp(state["last_signal"]) if "last_signal" in state else None
    newest = None
    n = len(df)
    for b in range(max(n - CHECK_LAST, 0), n):
        level = shift_at(df, b)
        if level is None:
            continue
        t = df.index[b]
        if last_alert is not None and t <= last_alert:
            continue
        newest = (t, float(df["Close"].iloc[b]), level)

    if newest:
        t, price, level = newest
        send(
            f"🔻 Bearish 5m MSS on {NAME} @ {price:.5f}\n"
            f"Broke swing low {level:.5f}\n"
            f"Candle close: {t.strftime('%Y-%m-%d %H:%M')} UTC"
        )
        state["last_signal"] = t.isoformat()
        save_state(state)
        print("Alert sent for", t)
    else:
        print("No new signal.")


if __name__ == "__main__":
    main()
