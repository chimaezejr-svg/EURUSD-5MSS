"""EURUSD 5m bearish market structure shift alert -> Telegram.

Alert only. It never places trades.

Rule (v2, built from the trader's screenshots):
1. A recent swing high (H) exists.
2. Price later SWEEPS H: a candle trades above it (liquidity taken).
3. L = the lowest low after H's candle, up to the sweeping candle (base of the sweep leg).
4. Alert when a 5m candle trades below L for the first time (a wick is enough).
"""
import json
import os
import sys

import pandas as pd
import requests

SYMBOL = "EURUSD=X"
NAME = "EURUSD"
LOOKBACK = 1          # candles each side to confirm a swing high (1 = local peak)
MAX_SWING_AGE = 72    # a swing high stays "recent" for this many 5m bars (6h)
MAX_SWEEP_AGE = 48    # max bars from the sweep to the break (4h)
INCLUDE_FORMING = True  # True = alert the moment price wicks through, without waiting for the candle to close
CHECK_LAST = 4        # re-check this many latest closed candles (covers late runs)
HISTORY = 300         # bars used for the calculation
STATE_FILE = "state.json"

# Session filter in UTC hours, e.g. [(7, 11), (13, 17)] for London + New York.
# None = alert at any time. We will set this once your chart time zone is known.
SESSIONS_UTC = None

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
    import yfinance as yf

    df = yf.Ticker(SYMBOL).history(period="2d", interval="5m")
    if df is None or df.empty:
        return None
    df.index = pd.to_datetime(df.index).tz_convert("UTC")
    now = pd.Timestamp.now(tz="UTC")
    if not INCLUDE_FORMING and df.index[-1] + pd.Timedelta(minutes=5) > now:
        df = df.iloc[:-1]  # drop the candle still forming
    return df


def swing_highs(d):
    h = d["High"].values
    return [
        i
        for i in range(LOOKBACK, len(d) - LOOKBACK)
        if h[i] == h[i - LOOKBACK:i + LOOKBACK + 1].max()
    ]


def shift_at(df, b):
    """If candle b is the first to trade below the base of a swept high, return
    (swept_high, broken_low). Otherwise None."""
    d = df.iloc[max(0, b + 1 - HISTORY):b + 1].reset_index(drop=True)
    n = len(d)
    last = n - 1
    h, l, c = d["High"].values, d["Low"].values, d["Close"].values

    for i in reversed(swing_highs(d)):
        if last - i > MAX_SWING_AGE:
            break
        peak = h[i]
        # first candle after the swing high that trades above it = the sweep
        s = next((k for k in range(i + 1, n) if h[k] > peak), None)
        if s is None or s >= last or last - s > MAX_SWEEP_AGE:
            continue
        level = l[i + 1:s + 1].min()  # lows AFTER the swing-high candle, up to the sweep
        if (l[s + 1:last] < level).any():
            continue  # already broken earlier, not a fresh shift
        if l[last] < level:
            return float(peak), float(level)
    return None


def in_session(ts):
    if not SESSIONS_UTC:
        return True
    return any(a <= ts.hour < z for a, z in SESSIONS_UTC)


def main():
    if not TOKEN or not CHAT_ID:
        sys.exit("Missing TELEGRAM_TOKEN or TELEGRAM_CHAT_ID")

    if os.environ.get("TEST_MESSAGE") == "1":
        send("✅ Test: your EURUSD 5m alert bot is connected.")
        return

    state = load_state()
    save_state(state)  # make sure the file exists

    df = get_candles()
    if df is None or len(df) < 30:
        print("No data (market closed or data gap).")
        return

    last_alert = pd.Timestamp(state["last_signal"]) if "last_signal" in state else None
    newest = None
    n = len(df)
    for b in range(max(n - CHECK_LAST, 0), n):
        t = df.index[b]
        if last_alert is not None and t <= last_alert:
            continue
        if not in_session(t):
            continue
        res = shift_at(df, b)
        if res:
            newest = (t, float(df["Close"].iloc[b]), res[0], res[1])

    if newest:
        t, price, peak, level = newest
        send(
            f"🔻 Bearish 5m MSS on {NAME} @ {price:.5f}\n"
            f"Swept high: {peak:.5f}\n"
            f"Broke low: {level:.5f}\n"
            f"Candle: {t.strftime('%Y-%m-%d %H:%M')} UTC"
        )
        state["last_signal"] = t.isoformat()
        save_state(state)
        print("Alert sent for", t)
    else:
        print("No new signal.")


if __name__ == "__main__":
    main()
