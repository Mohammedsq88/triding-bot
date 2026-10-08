Import os
import time
import requests
import pandas as pd
import numpy as np

# ============================================================
# SMC ENGINE v4 - XAU/USD SPOT
# ============================================================

TWELVE_DATA_API_KEY = "7bf250b4b655456c805478936ebed10a"

SYMBOL = "XAU/USD"

TD_URL = "https://api.twelvedata.com/time_series"

TIMEFRAME = "5min"
HTF_TIMEFRAME = "1h"

LOOKBACK_5M = 500
LOOKBACK_1H = 300

SWING_W = 3
ATR_LEN = 14

ATR_SL_MULT = 1.5
ATR_TP_MULT = 2.0

RR_MIN = 1.5


# ============================================================
# GET XAU/USD LIVE PRICE
# ============================================================

def get_live_gold():

    try:

        params = {
            "symbol": SYMBOL,
            "apikey": TWELVE_DATA_API_KEY
        }

        r = requests.get(
            "https://api.twelvedata.com/price",
            params=params,
            timeout=10
        )

        r.raise_for_status()

        data = r.json()

        if "price" not in data:

            raise ValueError(
                f"خطأ من Twelve Data: {data}"
            )

        price = float(data["price"])

        return price

    except Exception as e:

        print("LIVE PRICE ERROR:", e)

        return None


# ============================================================
# GET OHLC DATA
# ============================================================

def get_candles(interval, outputsize=500):

    params = {
        "symbol": SYMBOL,
        "interval": interval,
        "outputsize": outputsize,
        "apikey": TWELVE_DATA_API_KEY,
        "format": "JSON"
    }

    r = requests.get(
        TD_URL,
        params=params,
        timeout=20
    )

    r.raise_for_status()

    data = r.json()

    if "status" in data:

        if data["status"] == "error":

            raise ValueError(
                data.get(
                    "message",
                    "Twelve Data API Error"
                )
            )

    if "values" not in data:

        raise ValueError(
            f"لم تصل بيانات الشموع:\n{data}"
        )

    df = pd.DataFrame(
        data["values"]
    )

    df["datetime"] = pd.to_datetime(
        df["datetime"],
        utc=True
    )

    for col in [
        "open",
        "high",
        "low",
        "close"
    ]:

        df[col] = pd.to_numeric(
            df[col],
            errors="coerce"
        )

    df = df.dropna(
        subset=[
            "open",
            "high",
            "low",
            "close"
        ]
    )

    df = df.sort_values(
        "datetime"
    )

    df = df.set_index(
        "datetime"
    )

    return df


# ============================================================
# REMOVE CURRENT INCOMPLETE CANDLE
# ============================================================

def remove_incomplete_candle(
    df,
    minutes
):

    if df.empty:

        return df

    now = pd.Timestamp.now(
        tz="UTC"
    )

    last_time = df.index[-1]

    age = (
        now - last_time
    ).total_seconds() / 60

    if age < minutes:

        df = df.iloc[:-1]

    return df


# ============================================================
# ATR
# ============================================================

def calculate_atr(
    df,
    length=14
):

    high = df["high"]

    low = df["low"]

    close = df["close"]

    previous_close = close.shift(1)

    tr1 = high - low

    tr2 = (
        high - previous_close
    ).abs()

    tr3 = (
        low - previous_close
    ).abs()

    tr = pd.concat(
        [
            tr1,
            tr2,
            tr3
        ],
        axis=1
    ).max(axis=1)

    return tr.rolling(
        length
    ).mean()


# ============================================================
# CONFIRMED SWINGS
# ============================================================

def confirmed_swings(
    df,
    w=3
):

    highs = []

    lows = []

    h = df["high"].values

    l = df["low"].values

    for i in range(
        w,
        len(df) - w
    ):

        left_high = h[i-w:i]

        right_high = h[
            i+1:i+w+1
        ]

        left_low = l[i-w:i]

        right_low = l[
            i+1:i+w+1
        ]

        if (
            h[i] > np.max(left_high)
            and
            h[i] > np.max(right_high)
        ):

            highs.append({
                "index": i,
                "time": df.index[i],
                "price": h[i]
            })

        if (
            l[i] < np.min(left_low)
            and
            l[i] < np.min(right_low)
        ):

            lows.append({
                "index": i,
                "time": df.index[i],
                "price": l[i]
            })

    return highs, lows


# ============================================================
# FVG
# ============================================================

def detect_fvg(df):

    if len(df) < 3:

        return None

    a = df.iloc[-3]

    b = df.iloc[-2]

    c = df.iloc[-1]

    # Bullish FVG

    if c["low"] > a["high"]:

        return {
            "type": "BULLISH",
            "low": a["high"],
            "high": c["low"]
        }

    # Bearish FVG

    if c["high"] < a["low"]:

        return {
            "type": "BEARISH",
            "low": c["high"],
            "high": a["low"]
        }

    return None


# ============================================================
# DISPLACEMENT
# ============================================================

def has_displacement(df):

    if len(df) < 20:

        return False

    atr_value = calculate_atr(
        df,
        ATR_LEN
    ).iloc[-1]

    if pd.isna(atr_value):

        return False

    candle = df.iloc[-1]

    body = abs(
        candle["close"]
        -
        candle["open"]
    )

    return body >= (
        atr_value * 1.2
    )


# ============================================================
# HTF BIAS
# ============================================================

def get_htf_bias(df):

    if len(df) < 30:

        return "NEUTRAL"

    highs, lows = confirmed_swings(
        df,
        SWING_W
    )

    if (
        len(highs) < 2
        or
        len(lows) < 2
    ):

        return "NEUTRAL"

    last_high = highs[-1]["price"]

    previous_high = highs[-2]["price"]

    last_low = lows[-1]["price"]

    previous_low = lows[-2]["price"]

    # Higher High + Higher Low

    if (
        last_high > previous_high
        and
        last_low > previous_low
    ):

        return "BULLISH"

    # Lower High + Lower Low

    if (
        last_high < previous_high
        and
        last_low < previous_low
    ):

        return "BEARISH"

    return "NEUTRAL"


# ============================================================
# LIQUIDITY SWEEP
# ============================================================

def detect_sweep(
    df,
    highs,
    lows
):

    if len(df) < 2:

        return None

    candle = df.iloc[-1]

    high = candle["high"]

    low = candle["low"]

    close = candle["close"]

    recent_high = None

    recent_low = None

    if highs:

        recent_high = highs[-1]["price"]

    if lows:

        recent_low = lows[-1]["price"]

    # SELL SIDE LIQUIDITY
    # Sweep low then close back above

    if recent_low is not None:

        if (
            low < recent_low
            and
            close > recent_low
        ):

            return {
                "type": "SELL_SIDE",
                "level": recent_low
            }

    # BUY SIDE LIQUIDITY
    # Sweep high then close back below

    if recent_high is not None:

        if (
            high > recent_high
            and
            close < recent_high
        ):

            return {
                "type": "BUY_SIDE",
                "level": recent_high
            }

    return None


# ============================================================
# BREAK OF STRUCTURE
# ============================================================

def detect_bos(
    df,
    highs,
    lows,
    sweep
):

    if sweep is None:

        return None

    close = df.iloc[-1]["close"]

    # SELL SIDE SWEEP
    # Expect bullish BOS

    if sweep["type"] == "SELL_SIDE":

        if highs:

            structure_high = highs[-1]["price"]

            if close > structure_high:

                return {
                    "direction": "LONG",
                    "level": structure_high
                }

    # BUY SIDE SWEEP
    # Expect bearish BOS

    if sweep["type"] == "BUY_SIDE":

        if lows:

            structure_low = lows[-1]["price"]

            if close < structure_low:

                return {
                    "direction": "SHORT",
                    "level": structure_low
                }

    return None


# ============================================================
# GENERATE SIGNAL
# ============================================================

def generate_signal(
    df,
    htf_bias
):

    if len(df) < 50:

        return None

    highs, lows = confirmed_swings(
        df,
        SWING_W
    )

    sweep = detect_sweep(
        df,
        highs,
        lows
    )

    if sweep is None:

        return None

    bos = detect_bos(
        df,
        highs,
        lows,
        sweep
    )

    if bos is None:

        return None

    if not has_displacement(df):

        return None

    direction = bos["direction"]

    # HTF FILTER

    if (
        direction == "LONG"
        and
        htf_bias != "BULLISH"
    ):

        return None

    if (
        direction == "SHORT"
        and
        htf_bias != "BEARISH"
    ):

        return None

    entry = float(
        df.iloc[-1]["close"]
    )

    atr_value = float(
        calculate_atr(
            df,
            ATR_LEN
        ).iloc[-1]
    )

    if pd.isna(atr_value):

        return None

    # LONG

    if direction == "LONG":

        sl = (
            entry
            -
            atr_value * ATR_SL_MULT
        )

        tp1 = (
            entry
            +
            atr_value * ATR_TP_MULT
        )

        tp2 = (
            entry
            +
            atr_value * ATR_TP_MULT * 2
        )

    # SHORT

    else:

        sl = (
            entry
            +
            atr_value * ATR_SL_MULT
        )

        tp1 = (
            entry
            -
            atr_value * ATR_TP_MULT
        )

        tp2 = (
            entry
            -
            atr_value * ATR_TP_MULT * 2
        )

    risk = abs(
        entry - sl
    )

    reward = abs(
        tp2 - entry
    )

    rr = (
        reward / risk
        if risk > 0
        else 0
    )

    if rr < RR_MIN:

        return None

    fvg = detect_fvg(df)

    return {

        "direction": direction,

        "entry": entry,

        "sl": sl,

        "tp1": tp1,

        "tp2": tp2,

        "rr": rr,

        "atr": atr_value,

        "sweep": sweep["type"],

        "sweep_level": sweep["level"],

        "bos": bos["level"],

        "fvg": fvg

    }


# ============================================================
# PRINT LIVE PRICE
# ============================================================

def print_live_price():

    price = get_live_gold()

    print()

    print(
        "=" * 60
    )

    print(
        "XAU/USD SPOT"
    )

    print(
        "=" * 60
    )

    if price is None:

        print(
            "❌ فشل الحصول على السعر"
        )

        return None

    print(
        f"PRICE : {price:.2f}"
    )

    print(
        "SOURCE: Twelve Data"
    )

    print(
        "=" * 60
    )

    return price


# ============================================================
# FULL ANALYSIS
# ============================================================

def analyze():

    print()

    print(
        "تحميل بيانات XAU/USD 5M..."
    )

    df5 = get_candles(
        TIMEFRAME,
        LOOKBACK_5M
    )

    df5 = remove_incomplete_candle(
        df5,
        5
    )

    print(
        f"5M candles = {len(df5)}"
    )

    print()

    print(
        "تحميل بيانات XAU/USD 1H..."
    )

    df1h = get_candles(
        HTF_TIMEFRAME,
        LOOKBACK_1H
    )

    df1h = remove_incomplete_candle(
        df1h,
        60
    )

    print(
        f"1H candles = {len(df1h)}"
    )

    # HTF BIAS

    htf_bias = get_htf_bias(
        df1h
    )

    print()

    print(
        f"HTF BIAS = {htf_bias}"
    )

    # SIGNAL

    signal = generate_signal(
        df5,
        htf_bias
    )

    if signal is None:

        print()

        print(
            "لا توجد إشارة SMC كاملة حالياً."
        )

        return None

    print()

    print(
        "=" * 60
    )

    print(
        "🔥 SMC SIGNAL"
    )

    print(
        "=" * 60
    )

    print(
        f"Direction : {signal['direction']}"
    )

    print(
        f"Entry     : {signal['entry']:.2f}"
    )

    print(
        f"SL        : {signal['sl']:.2f}"
    )

    print(
        f"TP1       : {signal['tp1']:.2f}"
    )

    print(
        f"TP2       : {signal['tp2']:.2f}"
    )

    print(
        f"RR        : 1:{signal['rr']:.2f}"
    )

    print(
        f"ATR       : {signal['atr']:.2f}"
    )

    print(
        f"Sweep     : {signal['sweep']}"
    )

    print(
        f"Sweep Lvl : {signal['sweep_level']:.2f}"
    )

    print(
        f"BOS       : {signal['bos']:.2f}"
    )

    if signal["fvg"]:

        print(
            f"FVG       : {signal['fvg']['type']}"
        )

        print(
            f"FVG Low   : {signal['fvg']['low']:.2f}"
        )

        print(
            f"FVG High  : {signal['fvg']['high']:.2f}"
        )

    else:

        print(
            "FVG       : NONE"
        )

    print(
        "=" * 60
    )

    return signal


# ============================================================
# TEST API
# ============================================================

def test_api():

    print()

    print(
        "اختبار Twelve Data..."
    )

    price = get_live_gold()

    if price is None:

        print(
            "❌ فشل اختبار السعر اللحظي"
        )

        return False

    print(
        f"✅ XAU/USD = {price:.2f}"
    )

    try:

        df = get_candles(
            "5min",
            10
        )

        print(
            f"✅ تم تحميل {len(df)} شموع 5M"
        )

    except Exception as e:

        print(
            "❌ خطأ في بيانات 5M:"
        )

        print(e)

        return False

    try:

        df = get_candles(
            "1h",
            10
        )

        print(
            f"✅ تم تحميل {len(df)} شموع 1H"
        )

    except Exception as e:

        print(
            "❌ خطأ في بيانات 1H:"
        )

        print(e)

        return False

    print()

    print(
        "✅ API يعمل بشكل صحيح."
    )

    return True


# ============================================================
# RUN ONCE
# ============================================================

def run_once():

    if not test_api():

        return

    print_live_price()

    try:

        analyze()

    except Exception as e:

        print()

        print(
            "❌ ANALYSIS ERROR:"
        )

        print(e)


# ============================================================
# CONTINUOUS LIVE
# ============================================================

def run_live():

    last_signal = None

    while True:

        try:

            price = get_live_gold()

            if price is not None:

                print()

                print(
                    f"XAU/USD LIVE = {price:.2f}"
                )

            signal = analyze()

            if signal:

                key = (
                    signal["direction"],
                    round(
                        signal["entry"],
                        2
                    )
                )

                if key != last_signal:

                    print()

                    print(
                        "🚨 NEW SMC SIGNAL"
                    )

                    last_signal = key

            time.sleep(60)

        except KeyboardInterrupt:

            print(
                "\nتم إيقاف المحرك."
            )

            break

        except Exception as e:

            print(
                "ERROR:",
                e
            )

            time.sleep(10)


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    run_once()
