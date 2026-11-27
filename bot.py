"""ORYN 5m bearish market structure shift alerts -> Telegram.

Alert only. It never places trades.
Mirrors the engine in ORYN Indicator Pro1 candle by candle:
- swing high  = candle whose high is >= the candle before it and the candle after it
                (a neighbour may be higher by up to TOL: the equal-high tolerance)
- sweep       = a later candle trades above that high (wick is enough)
- base low    = lowest low after the swing-high candle, up to the sweep candle
- shift       = first candle that trades below the base low (wick is enough)
"""
import json
import os
import sys

import pandas as pd
import requests

SYMBOL = "EURUSD=X"
NAME = "EURUSD"
MAX_SWING_AGE = 72      # candles a swing high stays recent (same as indicator)
MAX_SWEEP_AGE = 48      # max candles from sweep to break (same as indicator)
TOL_PIPS = 0.3          # equal-high tolerance (same as indicator)
TOL = TOL_PIPS * 0.0001
HISTORY = "5d"          # warm-up so the engine has the same context as the chart
MAX_AGE_MIN = 180       # still alert on shifts up to this old (covers late/skipped runs)
INCLUDE_FORMING = True  # alert as soon as a wick breaks, without waiting for the close
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
    import yfinance as yf

    df = yf.Ticker(SYMBOL).history(period=HISTORY, interval="5m")
    if df is None or df.empty:
        return None
    df.index = pd.to_datetime(df.index).tz_convert("UTC")
    if not INCLUDE_FORMING:
        now = pd.Timestamp.now(tz="UTC")
        if df.index[-1] + pd.Timedelta(minutes=5) > now:
            df = df.iloc[:-1]
    return df


class Engine:
    """Python twin of the Pine engine (advance())."""

    def __init__(self, tol, max_swing, max_sweep):
        self.tol, self.max_swing, self.max_sweep = tol, max_swing, max_sweep
        self.setups = []
        self.n = 0
        self.h1 = None
        self.h2 = None

    def advance(self, h, l, t):
        hit = None
        self.n += 1
        cnt = self.n
        for k in range(len(self.setups) - 1, -1, -1):
            s = self.setups[k]
            drop = False
            hm_old = s["hm"]
            if cnt - s["pn"] > self.max_swing:
                drop = True
            elif s["sn"] == -1:
                if l < s["mn"]:
                    s["mn"] = l
                if h > s["pk"]:
                    s["sn"] = cnt
            elif cnt - s["sn"] > self.max_sweep:
                drop = True
            elif l < s["mn"]:
                drop = True
                if hit is None:
                    hit = {"t": t, "swept": s["pk"], "low": s["mn"], "top": hm_old}
            if drop:
                self.setups.pop(k)
            elif h > hm_old:
                s["hm"] = h
        if (
            cnt >= 3
            and self.h1 is not None
            and self.h2 is not None
            and self.h1 + self.tol >= self.h2
            and self.h1 + self.tol >= h
        ):
            self.setups.append(
                {
                    "pk": self.h1,
                    "mn": l,
                    "pn": cnt - 1,
                    "sn": cnt if h > self.h1 else -1,
                    "hm": h if h > self.h1 else self.h1,
                }
            )
        self.h2 = self.h1
        self.h1 = h
        return hit


def find_shifts(df):
    eng = Engine(TOL, MAX_SWING_AGE, MAX_SWEEP_AGE)
    hits = []
    for t, row in df.iterrows():
        r = eng.advance(float(row["High"]), float(row["Low"]), t)
        if r:
            hits.append(r)
    return hits


def main():
    if not TOKEN or not CHAT_ID:
        sys.exit("Missing TELEGRAM_TOKEN or TELEGRAM_CHAT_ID")

    if os.environ.get("TEST_MESSAGE") == "1":
        send("✅ Test: your EURUSD 5m alert bot is connected.")
        return

    state = load_state()
    first_run = "sent" not in state
    sent = set(state.get("sent", []))
    if "last_signal" in state:
        sent.add(pd.Timestamp(state["last_signal"]).isoformat())

    df = get_candles()
    if df is None or len(df) < 30:
        print("No data (market closed or data gap).")
        save_state({"sent": sorted(sent)[-100:]})
        return

    now = pd.Timestamp.now(tz="UTC")
    recent = [
        h for h in find_shifts(df)
        if now - h["t"] <= pd.Timedelta(minutes=MAX_AGE_MIN)
    ]

    if first_run:
        for h in recent:  # first run: do not spam old shifts
            sent.add(h["t"].isoformat())
        print(f"First run: marked {len(recent)} recent shifts as already seen.")
    else:
        for h in sorted(recent, key=lambda x: x["t"]):
            key = h["t"].isoformat()
            if key in sent:
                continue
            send(
                f"🔻 Bearish 5m MSS on {NAME}\n"
                f"Swept high: {h['swept']:.5f}\n"
                f"Broke low: {h['low']:.5f}\n"
                f"Top of the move: {h['top']:.5f}\n"
                f"Candle: {h['t'].strftime('%Y-%m-%d %H:%M')} UTC"
            )
            sent.add(key)
            print("Alert sent for", key)

    save_state({"sent": sorted(sent)[-100:]})


if __name__ == "__main__":
    main()
