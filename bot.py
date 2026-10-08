#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SMC Engine v3 — Gold Intraday
=============================

Architecture:
    1H HTF Bias
        ↓
    Confirmed Swing Liquidity
        ↓
    Liquidity Sweep
        ↓
    BOS
        ↓
    Displacement
        ↓
    FVG
        ↓
    ATR Risk
        ↓
    TP1 + TP2
        ↓
    TP1 -> SL to Breakeven
        ↓
    TP2 / SL

Important:
- No lookahead in structure decisions.
- A swing is usable only after swing_w candles confirm it.
- Signal is calculated on a closed 5M candle.
- Entry is next candle OPEN.
- TP levels use information available at signal time only.
"""

import argparse
from dataclasses import dataclass, field
from typing import Optional, List, Dict

import numpy as np
import pandas as pd
import requests

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

try:
    import yfinance as yf
except ImportError:
    yf = None


# ============================================================
# 1) DATA
# ============================================================

def _flatten(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    required = ["Open", "High", "Low", "Close", "Volume"]

    missing = [x for x in required if x not in df.columns]

    if missing:
        raise RuntimeError(
            f"Missing columns: {missing}"
        )

    return df[required].dropna(subset=["Open", "High", "Low", "Close"])


def load_data(
    symbol: str,
    period_5m: str = "60d",
    period_1h: str = "1y"
):
    """
    Load 5M + 1H data.
    """

    if yf is None:
        raise RuntimeError(
            "Install yfinance first: pip install yfinance"
        )

    session = requests.Session()
    session.headers.update(
        {"User-Agent": "Mozilla/5.0"}
    )

    df5 = _flatten(
        yf.download(
            symbol,
            period=period_5m,
            interval="5m",
            progress=False,
            session=session,
            auto_adjust=False
        )
    )

    df1 = _flatten(
        yf.download(
            symbol,
            period=period_1h,
            interval="1h",
            progress=False,
            session=session,
            auto_adjust=False
        )
    )

    if df5.empty or df1.empty:
        raise RuntimeError(
            "Failed to download market data."
        )

    for df in (df5, df1):

        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")

    return (
        df5.sort_index(),
        df1.sort_index()
    )


# ============================================================
# 2) HTF CLOSED CANDLES
# ============================================================

def htf_completed(
    df1h: pd.DataFrame,
    t: pd.Timestamp
) -> pd.DataFrame:
    """
    Return ONLY completed 1H candles before time t.

    A candle beginning at 10:00 is completed at 11:00.
    """

    return df1h[
        df1h.index + pd.Timedelta(hours=1) <= t
    ]


# ============================================================
# 3) ATR
# ============================================================

def atr(
    df: pd.DataFrame,
    n: int = 14
) -> pd.Series:

    high = df["High"]
    low = df["Low"]
    close = df["Close"]

    prev_close = close.shift(1)

    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs()
        ],
        axis=1
    ).max(axis=1)

    return tr.ewm(
        alpha=1 / n,
        min_periods=n,
        adjust=False
    ).mean()


# ============================================================
# 4) CONFIRMED SWINGS
# ============================================================

def raw_swing_points(
    df: pd.DataFrame,
    w: int = 2
):
    """
    Detect raw fractal pivots.

    IMPORTANT:
    These pivots are NOT considered available immediately.

    A pivot at candle k becomes confirmed only at:

        k + w

    Therefore every consumer must respect confirmation.
    """

    high = df["High"].values
    low = df["Low"].values

    sh = np.zeros(len(df), dtype=bool)
    sl = np.zeros(len(df), dtype=bool)

    for i in range(w, len(df) - w):

        h_window = high[i - w:i + w + 1]
        l_window = low[i - w:i + w + 1]

        if high[i] == np.max(h_window):
            sh[i] = True

        if low[i] == np.min(l_window):
            sl[i] = True

    return (
        pd.Series(sh, index=df.index),
        pd.Series(sl, index=df.index)
    )


def confirmed_swing_indices(
    df: pd.DataFrame,
    w: int = 2,
    end_i: Optional[int] = None
):
    """
    Return swing indexes that are confirmed by end_i.

    Pivot at k is usable only if:

        k + w <= end_i
    """

    sh, sl = raw_swing_points(df, w)

    if end_i is None:
        end_i = len(df) - 1

    max_pivot = end_i - w

    high_idx = [
        i for i in range(
            0,
            max_pivot + 1
        )
        if bool(sh.iloc[i])
    ]

    low_idx = [
        i for i in range(
            0,
            max_pivot + 1
        )
        if bool(sl.iloc[i])
    ]

    return high_idx, low_idx


# ============================================================
# 5) FVG
# ============================================================

def bullish_fvg(
    df: pd.DataFrame,
    i: int
) -> bool:

    if i < 2:
        return False

    return (
        df["Low"].iloc[i]
        >
        df["High"].iloc[i - 2]
    )


def bearish_fvg(
    df: pd.DataFrame,
    i: int
) -> bool:

    if i < 2:
        return False

    return (
        df["High"].iloc[i]
        <
        df["Low"].iloc[i - 2]
    )


# ============================================================
# 6) PARAMETERS
# ============================================================

@dataclass
class EngineParams:

    # Structure
    swing_w: int = 2

    # ATR
    atr_n: int = 14

    # Sweep
    sweep_arm: int = 12

    # Displacement
    disp_mult: float = 1.2

    # Stop
    sl_atr_mult: float = 0.30

    # RR
    rr_min: float = 1.50

    # HTF
    bias_lookback: int = 5
    htf_swing_w: int = 3

    # TP
    tp1_atr_fallback: float = 1.5
    tp2_atr_fallback: float = 1.0

    # Position
    tp1_fraction: float = 0.50

    # Contract size
    contract_size: float = 100.0


# ============================================================
# 7) SIGNAL
# ============================================================

@dataclass
class Signal:

    side: str

    signal_time: pd.Timestamp

    entry_ref: float

    sl: float

    tp1: float

    tp2: float

    rr_tp1: float

    rr_tp2: float

    atr_value: float

    sweep_price: float

    bos_level: float

    reason: str


# ============================================================
# 8) SMC ENGINE
# ============================================================

class SMCEngineV3:

    def __init__(
        self,
        params: Optional[EngineParams] = None
    ):

        self.p = params or EngineParams()


    # --------------------------------------------------------
    # HTF BIAS
    # --------------------------------------------------------

    def _htf_bias(
        self,
        df1h_c: pd.DataFrame
    ) -> Optional[str]:

        p = self.p

        if len(df1h_c) < p.bias_lookback + 2:
            return None

        close = df1h_c["Close"]

        current = close.iloc[-1]

        reference = close.iloc[
            -1 - p.bias_lookback
        ]

        if current > reference:
            return "BULL"

        if current < reference:
            return "BEAR"

        return None


    # --------------------------------------------------------
    # LAST CONFIRMED SWING LOW
    # --------------------------------------------------------

    def _last_confirmed_low(
        self,
        df5: pd.DataFrame,
        before_i: int
    ) -> Optional[tuple]:

        _, lows = confirmed_swing_indices(
            df5,
            self.p.swing_w,
            before_i
        )

        lows = [
            x for x in lows
            if x < before_i
        ]

        if not lows:
            return None

        k = lows[-1]

        return (
            k,
            float(df5["Low"].iloc[k])
        )


    # --------------------------------------------------------
    # LAST CONFIRMED SWING HIGH
    # --------------------------------------------------------

    def _last_confirmed_high(
        self,
        df5: pd.DataFrame,
        before_i: int
    ) -> Optional[tuple]:

        highs, _ = confirmed_swing_indices(
            df5,
            self.p.swing_w,
            before_i
        )

        highs = [
            x for x in highs
            if x < before_i
        ]

        if not highs:
            return None

        k = highs[-1]

        return (
            k,
            float(df5["High"].iloc[k])
        )


    # --------------------------------------------------------
    # FIND SWEEP LOW
    # --------------------------------------------------------

    def _find_sweep_low(
        self,
        df5: pd.DataFrame,
        i: int
    ) -> Optional[tuple]:

        start = max(
            10,
            i - self.p.sweep_arm
        )

        for j in range(i, start - 1, -1):

            swing = self._last_confirmed_low(
                df5,
                j
            )

            if swing is None:
                continue

            k, liquidity = swing

            candle_low = float(
                df5["Low"].iloc[j]
            )

            candle_close = float(
                df5["Close"].iloc[j]
            )

            swept = candle_low < liquidity

            reclaimed = candle_close > liquidity

            if swept and reclaimed:

                return (
                    j,
                    k,
                    liquidity,
                    candle_low
                )

        return None


    # --------------------------------------------------------
    # FIND SWEEP HIGH
    # --------------------------------------------------------

    def _find_sweep_high(
        self,
        df5: pd.DataFrame,
        i: int
    ) -> Optional[tuple]:

        start = max(
            10,
            i - self.p.sweep_arm
        )

        for j in range(i, start - 1, -1):

            swing = self._last_confirmed_high(
                df5,
                j
            )

            if swing is None:
                continue

            k, liquidity = swing

            candle_high = float(
                df5["High"].iloc[j]
            )

            candle_close = float(
                df5["Close"].iloc[j]
            )

            swept = candle_high > liquidity

            reclaimed = candle_close < liquidity

            if swept and reclaimed:

                return (
                    j,
                    k,
                    liquidity,
                    candle_high
                )

        return None


    # --------------------------------------------------------
    # BULLISH BOS
    # --------------------------------------------------------

    def _bullish_bos(
        self,
        df5: pd.DataFrame,
        sweep_index: int,
        i: int,
        atr_value: float
    ) -> Optional[float]:

        p = self.p

        highs, _ = confirmed_swing_indices(
            df5,
            p.swing_w,
            i
        )

        candidates = [
            x for x in highs
            if sweep_index < x < i
        ]

        if not candidates:
            return None

        bos_index = candidates[-1]

        bos_level = float(
            df5["High"].iloc[bos_index]
        )

        close = float(
            df5["Close"].iloc[i]
        )

        open_price = float(
            df5["Open"].iloc[i]
        )

        body = abs(
            close - open_price
        )

        # Must actually break structure
        if close <= bos_level:
            return None

        # Must be bullish candle
        if close <= open_price:
            return None

        # Displacement
        if body < p.disp_mult * atr_value:
            return None

        # FVG
        if not (
            bullish_fvg(df5, i)
            or
            bullish_fvg(df5, i - 1)
        ):
            return None

        return bos_level


    # --------------------------------------------------------
    # BEARISH BOS
    # --------------------------------------------------------

    def _bearish_bos(
        self,
        df5: pd.DataFrame,
        sweep_index: int,
        i: int,
        atr_value: float
    ) -> Optional[float]:

        p = self.p

        _, lows = confirmed_swing_indices(
            df5,
            p.swing_w,
            i
        )

        candidates = [
            x for x in lows
            if sweep_index < x < i
        ]

        if not candidates:
            return None

        bos_index = candidates[-1]

        bos_level = float(
            df5["Low"].iloc[bos_index]
        )

        close = float(
            df5["Close"].iloc[i]
        )

        open_price = float(
            df5["Open"].iloc[i]
        )

        body = abs(
            close - open_price
        )

        if close >= bos_level:
            return None

        if close >= open_price:
            return None

        if body < p.disp_mult * atr_value:
            return None

        if not (
            bearish_fvg(df5, i)
            or
            bearish_fvg(df5, i - 1)
        ):
            return None

        return bos_level


    # --------------------------------------------------------
    # TP1 FROM CONFIRMED SWING
    # --------------------------------------------------------

    def _tp1_long(
        self,
        df5: pd.DataFrame,
        i: int,
        entry: float,
        atr_value: float
    ) -> float:

        highs, _ = confirmed_swing_indices(
            df5,
            self.p.swing_w,
            i
        )

        candidates = [
            float(df5["High"].iloc[x])
            for x in highs
            if x < i
            and float(df5["High"].iloc[x]) > entry
        ]

        if candidates:

            return min(candidates)

        return (
            entry
            +
            self.p.tp1_atr_fallback * atr_value
        )


    def _tp1_short(
        self,
        df5: pd.DataFrame,
        i: int,
        entry: float,
        atr_value: float
    ) -> float:

        _, lows = confirmed_swing_indices(
            df5,
            self.p.swing_w,
            i
        )

        candidates = [
            float(df5["Low"].iloc[x])
            for x in lows
            if x < i
            and float(df5["Low"].iloc[x]) < entry
        ]

        if candidates:

            return max(candidates)

        return (
            entry
            -
            self.p.tp1_atr_fallback * atr_value
        )


    # --------------------------------------------------------
    # HTF ERL
    # --------------------------------------------------------

    def _erl(
        self,
        df1h_c: pd.DataFrame,
        ref: float,
        side: str
    ) -> Optional[float]:

        if len(df1h_c) < 20:
            return None

        p = self.p

        highs, lows = confirmed_swing_indices(
            df1h_c,
            p.htf_swing_w,
            len(df1h_c) - 1
        )

        if side == "LONG":

            candidates = [
                float(df1h_c["High"].iloc[x])
                for x in highs
                if x < len(df1h_c) - 1
                and float(df1h_c["High"].iloc[x]) > ref
            ]

            if candidates:
                return min(candidates)

        else:

            candidates = [
                float(df1h_c["Low"].iloc[x])
                for x in lows
                if x < len(df1h_c) - 1
                and float(df1h_c["Low"].iloc[x]) < ref
            ]

            if candidates:
                return max(candidates)

        return None


    # --------------------------------------------------------
    # PUBLIC SIGNAL
    # --------------------------------------------------------

    def signal_at(
        self,
        df5_slice: pd.DataFrame,
        df1h_c: pd.DataFrame,
        t: pd.Timestamp
    ) -> Optional[Signal]:

        p = self.p

        minimum = (
            p.sweep_arm
            +
            p.swing_w * 2
            +
            p.atr_n
            +
            20
        )

        if len(df5_slice) < minimum:
            return None

        i = len(df5_slice) - 1

        # IMPORTANT:
        # signal_at is assumed to receive a CLOSED candle
        atr_series = atr(
            df5_slice,
            p.atr_n
        )

        atr_value = float(
            atr_series.iloc[i]
        )

        if not np.isfinite(atr_value):
            return None

        if atr_value <= 0:
            return None

        bias = self._htf_bias(df1h_c)

        if bias is None:
            return None

        entry = float(
            df5_slice["Close"].iloc[i]
        )


        # ====================================================
        # LONG
        # ====================================================

        if bias == "BULL":

            sweep = self._find_sweep_low(
                df5_slice,
                i
            )

            if sweep is not None:

                sweep_index = sweep[0]
                sweep_price = sweep[3]

                bos = self._bullish_bos(
                    df5_slice,
                    sweep_index,
                    i,
                    atr_value
                )

                if bos is not None:

                    sl = (
                        sweep_price
                        -
                        p.sl_atr_mult * atr_value
                    )

                    tp1 = self._tp1_long(
                        df5_slice,
                        i,
                        entry,
                        atr_value
                    )

                    erl = self._erl(
                        df1h_c,
                        entry,
                        "LONG"
                    )

                    if (
                        erl is not None
                        and
                        erl > tp1
                    ):
                        tp2 = erl
                    else:
                        tp2 = (
                            tp1
                            +
                            p.tp2_atr_fallback
                            * atr_value
                        )

                    risk = entry - sl

                    if risk <= 0:
                        return None

                    reward1 = tp1 - entry
                    reward2 = tp2 - entry

                    rr1 = reward1 / risk
                    rr2 = reward2 / risk

                    if rr2 < p.rr_min:
                        return None

                    return Signal(
                        side="LONG",
                        signal_time=t,
                        entry_ref=entry,
                        sl=sl,
                        tp1=tp1,
                        tp2=tp2,
                        rr_tp1=rr1,
                        rr_tp2=rr2,
                        atr_value=atr_value,
                        sweep_price=sweep_price,
                        bos_level=bos,
                        reason=(
                            "Bullish Sweep + BOS "
                            "+ Displacement + FVG "
                            "+ HTF Bull"
                        )
                    )


        # ====================================================
        # SHORT
        # ====================================================

        if bias == "BEAR":

            sweep = self._find_sweep_high(
                df5_slice,
                i
            )

            if sweep is not None:

                sweep_index = sweep[0]
                sweep_price = sweep[3]

                bos = self._bearish_bos(
                    df5_slice,
                    sweep_index,
                    i,
                    atr_value
                )

                if bos is not None:

                    sl = (
                        sweep_price
                        +
                        p.sl_atr_mult * atr_value
                    )

                    tp1 = self._tp1_short(
                        df5_slice,
                        i,
                        entry,
                        atr_value
                    )

                    erl = self._erl(
                        df1h_c,
                        entry,
                        "SHORT"
                    )

                    if (
                        erl is not None
                        and
                        erl < tp1
                    ):
                        tp2 = erl
                    else:
                        tp2 = (
                            tp1
                            -
                            p.tp2_atr_fallback
                            * atr_value
                        )

                    risk = sl - entry

                    if risk <= 0:
                        return None

                    reward1 = entry - tp1
                    reward2 = entry - tp2

                    rr1 = reward1 / risk
                    rr2 = reward2 / risk

                    if rr2 < p.rr_min:
                        return None

                    return Signal(
                        side="SHORT",
                        signal_time=t,
                        entry_ref=entry,
                        sl=sl,
                        tp1=tp1,
                        tp2=tp2,
                        rr_tp1=rr1,
                        rr_tp2=rr2,
                        atr_value=atr_value,
                        sweep_price=sweep_price,
                        bos_level=bos,
                        reason=(
                            "Bearish Sweep + BOS "
                            "+ Displacement + FVG "
                            "+ HTF Bear"
                        )
                    )

        return None


# ============================================================
# 9) TRADE
# ============================================================

@dataclass
class Trade:

    side: str

    signal_time: pd.Timestamp
    entry_time: pd.Timestamp

    entry: float

    sl_initial: float
    sl_final: float

    tp1: float
    tp2: float

    exit_time: pd.Timestamp
    exit_price: float

    result_r: float

    pnl_usd: float

    tp1_hit: bool

    exit_reason: str

    rr_tp2: float

    reason: str


# ============================================================
# 10) BACKTEST RESULT
# ============================================================

@dataclass
class BacktestResult:

    trades: List[Trade] = field(
        default_factory=list
    )

    skipped: int = 0


    def metrics(self) -> Dict:

        trades = self.trades

        if not trades:
            return {
                "trades": 0,
                "skipped": self.skipped
            }

        r_values = [
            x.result_r
            for x in trades
        ]

        pnl_values = [
            x.pnl_usd
            for x in trades
        ]

        wins = [
            x for x in trades
            if x.result_r > 0
        ]

        eq = np.cumsum(r_values)

        peak = np.maximum.accumulate(eq)

        dd = eq - peak

        max_dd = float(
            dd.min()
        )

        gross_profit = sum(
            x for x in r_values
            if x > 0
        )

        gross_loss = abs(
            sum(
                x for x in r_values
                if x < 0
            )
        )

        profit_factor = (
            gross_profit / gross_loss
            if gross_loss > 0
            else np.inf
        )

        tp1_count = sum(
            x.tp1_hit
            for x in trades
        )

        return {
            "trades": len(trades),

            "wins": len(wins),

            "losses":
                len(trades) - len(wins),

            "win_rate":
                len(wins) / len(trades),

            "avg_R":
                float(np.mean(r_values)),

            "total_R":
                float(eq[-1]),

            "max_dd_R":
                max_dd,

            "profit_factor":
                float(profit_factor),

            "tp1_hit_rate":
                tp1_count / len(trades),

            "avg_usd":
                float(np.mean(pnl_values)),

            "total_usd":
                float(np.sum(pnl_values)),

            "skipped":
                self.skipped
        }


# ============================================================
# 11) POSITION
# ============================================================

@dataclass
class Position:

    side: str

    signal_time: pd.Timestamp
    entry_time: pd.Timestamp

    entry: float

    sl: float

    initial_sl: float

    tp1: float
    tp2: float

    rr_tp2: float

    remaining_fraction: float = 1.0

    tp1_hit: bool = False


# ============================================================
# 12) BACKTESTER
# ============================================================

class Backtester:

    def __init__(
        self,
        df5: pd.DataFrame,
        df1h: pd.DataFrame,
        params: Optional[EngineParams] = None,
        warmup: int = 150
    ):

        self.df5 = df5
        self.df1h = df1h

        self.eng = SMCEngineV3(
            params
        )

        self.warmup = warmup


    # --------------------------------------------------------
    # EXIT R CALCULATION
    # --------------------------------------------------------

    def _r_from_price(
        self,
        pos: Position,
        price: float,
        fraction: float
    ) -> float:

        if pos.side == "LONG":

            risk = (
                pos.entry
                -
                pos.initial_sl
            )

            reward = (
                price
                -
                pos.entry
            )

        else:

            risk = (
                pos.initial_sl
                -
                pos.entry
            )

            reward = (
                pos.entry
                -
                price
            )

        if risk <= 0:
            return 0.0

        return (
            reward / risk
        ) * fraction


    # --------------------------------------------------------
    # RUN
    # --------------------------------------------------------

    def run(self) -> BacktestResult:

        df5 = self.df5

        idx = df5.index

        result = BacktestResult()

        position: Optional[Position] = None

        for i in range(
            self.warmup,
            len(df5) - 1
        ):

            bar = df5.iloc[i]

            t = idx[i]


            # =================================================
            # MANAGE OPEN POSITION
            # =================================================

            if position is not None:

                side = position.side

                if side == "LONG":

                    hit_sl = (
                        bar["Low"]
                        <=
                        position.sl
                    )

                    hit_tp1 = (
                        bar["High"]
                        >=
                        position.tp1
                    )

                    hit_tp2 = (
                        bar["High"]
                        >=
                        position.tp2
                    )

                else:

                    hit_sl = (
                        bar["High"]
                        >=
                        position.sl
                    )

                    hit_tp1 = (
                        bar["Low"]
                        <=
                        position.tp1
                    )

                    hit_tp2 = (
                        bar["Low"]
                        <=
                        position.tp2
                    )


                # ---------------------------------------------
                # Conservative ordering
                # ---------------------------------------------

                if not position.tp1_hit:

                    # If SL and TP1 happen in same candle:
                    # SL wins conservatively.

                    if hit_sl:

                        r = -1.0

                        result.trades.append(
                            Trade(
                                side=position.side,
                                signal_time=position.signal_time,
                                entry_time=position.entry_time,
                                entry=position.entry,
                                sl_initial=position.initial_sl,
                                sl_final=position.sl,
                                tp1=position.tp1,
                                tp2=position.tp2,
                                exit_time=t,
                                exit_price=float(position.sl),
                                result_r=r,
                                pnl_usd=(
                                    r
                                    *
                                    self.eng.p.contract_size
                                    *
                                    abs(
                                        position.entry
                                        -
                                        position.initial_sl
                                    )
                                ),
                                tp1_hit=False,
                                exit_reason="SL",
                                rr_tp2=position.rr_tp2,
                                reason=""
                            )
                        )

                        position = None

                        continue


                    # TP1 reached

                    if hit_tp1:

                        fraction = (
                            self.eng.p.tp1_fraction
                        )

                        r1 = self._r_from_price(
                            position,
                            position.tp1,
                            fraction
                        )

                        position.remaining_fraction = (
                            1.0 - fraction
                        )

                        position.tp1_hit = True

                        # Move SL to BE
                        position.sl = position.entry


                        # Conservative:
                        # do NOT count TP2 on same candle.
                        continue


                # ---------------------------------------------
                # AFTER TP1
                # ---------------------------------------------

                if position.tp1_hit:

                    # BE stop
                    if hit_sl:

                        fraction = (
                            position.remaining_fraction
                        )

                        r2 = self._r_from_price(
                            position,
                            position.sl,
                            fraction
                        )

                        total_r = (
                            self._r_from_price(
                                position,
                                position.tp1,
                                self.eng.p.tp1_fraction
                            )
                            +
                            r2
                        )

                        result.trades.append(
                            Trade(
                                side=position.side,
                                signal_time=position.signal_time,
                                entry_time=position.entry_time,
                                entry=position.entry,
                                sl_initial=position.initial_sl,
                                sl_final=position.sl,
                                tp1=position.tp1,
                                tp2=position.tp2,
                                exit_time=t,
                                exit_price=float(position.sl),
                                result_r=total_r,
                                pnl_usd=(
                                    total_r
                                    *
                                    self.eng.p.contract_size
                                    *
                                    abs(
                                        position.entry
                                        -
                                        position.initial_sl
                                    )
                                ),
                                tp1_hit=True,
                                exit_reason="TP1 + BE",
                                rr_tp2=position.rr_tp2,
                                reason=""
                            )
                        )

                        position = None

                        continue


                    # TP2
                    if hit_tp2:

                        fraction = (
                            position.remaining_fraction
                        )

                        r1 = self._r_from_price(
                            position,
                            position.tp1,
                            self.eng.p.tp1_fraction
                        )

                        r2 = self._r_from_price(
                            position,
                            position.tp2,
                            fraction
                        )

                        total_r = r1 + r2

                        result.trades.append(
                            Trade(
                                side=position.side,
                                signal_time=position.signal_time,
                                entry_time=position.entry_time,
                                entry=position.entry,
                                sl_initial=position.initial_sl,
                                sl_final=position.sl,
                                tp1=position.tp1,
                                tp2=position.tp2,
                                exit_time=t,
                                exit_price=float(position.tp2),
                                result_r=total_r,
                                pnl_usd=(
                                    total_r
                                    *
                                    self.eng.p.contract_size
                                    *
                                    abs(
                                        position.entry
                                        -
                                        position.initial_sl
                                    )
                                ),
                                tp1_hit=True,
                                exit_reason="TP2",
                                rr_tp2=position.rr_tp2,
                                reason=""
                            )
                        )

                        position = None

                        continue


                continue


            # =================================================
            # SEARCH FOR NEW SIGNAL
            # =================================================

            df5_slice = df5.iloc[
                :i + 1
            ]

            htf = htf_completed(
                self.df1h,
                t
            )

            sig = self.eng.signal_at(
                df5_slice,
                htf,
                t
            )

            if sig is None:
                continue


            # =================================================
            # NEXT CANDLE ENTRY
            # =================================================

            next_bar = df5.iloc[i + 1]

            entry = float(
                next_bar["Open"]
            )


            # =================================================
            # GAP FILTER
            # =================================================

            if sig.side == "LONG":

                if (
                    entry <= sig.sl
                    or
                    entry >= sig.tp1
                ):

                    result.skipped += 1

                    continue

                risk = (
                    entry
                    -
                    sig.sl
                )

                rr2 = (
                    sig.tp2
                    -
                    entry
                ) / risk

            else:

                if (
                    entry >= sig.sl
                    or
                    entry <= sig.tp1
                ):

                    result.skipped += 1

                    continue

                risk = (
                    sig.sl
                    -
                    entry
                )

                rr2 = (
                    entry
                    -
                    sig.tp2
                ) / risk


            if risk <= 0:

                result.skipped += 1

                continue


            if rr2 < self.eng.p.rr_min:

                result.skipped += 1

                continue


            # =================================================
            # CREATE POSITION
            # =================================================

            position = Position(
                side=sig.side,
                signal_time=sig.signal_time,
                entry_time=idx[i + 1],
                entry=entry,
                sl=sig.sl,
                initial_sl=sig.sl,
                tp1=sig.tp1,
                tp2=sig.tp2,
                rr_tp2=rr2
            )


        return result


    # --------------------------------------------------------
    # EQUITY PLOT
    # --------------------------------------------------------

    def plot(
        self,
        result: BacktestResult,
        out_png: str = "smc_v3_equity.png"
    ):

        if not result.trades:

            print(
                "No trades available for plotting."
            )

            return


        r = np.cumsum(
            [
                x.result_r
                for x in result.trades
            ]
        )

        usd = np.cumsum(
            [
                x.pnl_usd
                for x in result.trades
            ]
        )


        fig, ax = plt.subplots(
            2,
            1,
            figsize=(12, 8),
            sharex=True
        )


        ax[0].plot(
            r,
            linewidth=1.5
        )

        ax[0].set_title(
            "SMC v3 Equity Curve - R"
        )

        ax[0].grid(
            alpha=0.3
        )


        ax[1].plot(
            usd,
            linewidth=1.5
        )

        ax[1].set_title(
            "SMC v3 Cumulative PnL"
        )

        ax[1].grid(
            alpha=0.3
        )


        plt.tight_layout()

        plt.savefig(
            out_png,
            dpi=130
        )

        plt.close()

        print(
            f"Equity curve saved: {out_png}"
        )


# ============================================================
# 13) TRADE REPORT
# ============================================================

def print_trade_report(
    result: BacktestResult
):

    if not result.trades:

        print(
            "\nNo trades."
        )

        return


    print(
        "\n================ TRADE REPORT ================"
    )


    for n, trade in enumerate(
        result.trades,
        start=1
    ):

        print(
            f"\nTrade #{n}"
        )

        print(
            f"Side       : {trade.side}"
        )

        print(
            f"Signal     : {trade.signal_time}"
        )

        print(
            f"Entry      : {trade.entry_time}"
        )

        print(
            f"Entry Price: {trade.entry:.2f}"
        )

        print(
            f"SL         : {trade.sl_initial:.2f}"
        )

        print(
            f"TP1        : {trade.tp1:.2f}"
        )

        print(
            f"TP2        : {trade.tp2:.2f}"
        )

        print(
            f"Exit       : {trade.exit_price:.2f}"
        )

        print(
            f"Result R   : {trade.result_r:.2f}"
        )

        print(
            f"PnL USD    : {trade.pnl_usd:.2f}"
        )

        print(
            f"Exit Reason: {trade.exit_reason}"
        )


# ============================================================
# 14) LIVE / ONCE
# ============================================================

def get_last_closed_5m(
    df5: pd.DataFrame
) -> tuple:

    if len(df5) < 3:

        raise RuntimeError(
            "Not enough 5M candles."
        )


    now = pd.Timestamp.now(
        tz="UTC"
    )

    last_index = df5.index[-1]

    # Yahoo 5M candles normally begin at their timestamp.
    # We conservatively estimate candle close = timestamp + 5 minutes.

    estimated_close = (
        last_index
        +
        pd.Timedelta(minutes=5)
    )


    if estimated_close <= now:

        return (
            df5,
            last_index
        )


    # Last candle is potentially live.
    closed = df5.iloc[:-1]

    return (
        closed,
        closed.index[-1]
    )


def run_once(
    symbol: str
):

    df5, df1 = load_data(
        symbol
    )

    df5_closed, t = get_last_closed_5m(
        df5
    )

    htf = htf_completed(
        df1,
        t
    )

    engine = SMCEngineV3()

    signal = engine.signal_at(
        df5_closed,
        htf,
        t
    )

    output = {

        "time_utc":
            str(t),

        "price":
            float(
                df5_closed["Close"].iloc[-1]
            ),

        "signal":
            None
    }


    if signal:

        output["signal"] = {

            "side":
                signal.side,

            "entry_reference":
                round(
                    signal.entry_ref,
                    2
                ),

            "sl":
                round(
                    signal.sl,
                    2
                ),

            "tp1":
                round(
                    signal.tp1,
                    2
                ),

            "tp2":
                round(
                    signal.tp2,
                    2
                ),

            "rr_tp1":
                round(
                    signal.rr_tp1,
                    2
                ),

            "rr_tp2":
                round(
                    signal.rr_tp2,
                    2
                ),

            "atr":
                round(
                    signal.atr_value,
                    2
                ),

            "sweep":
                round(
                    signal.sweep_price,
                    2
                ),

            "bos":
                round(
                    signal.bos_level,
                    2
                ),

            "reason":
                signal.reason
        }


    return output


# ============================================================
# 15) MAIN
# ============================================================

def main():

    parser = argparse.ArgumentParser(
        description="SMC Engine v3 - Gold Intraday"
    )


    parser.add_argument(
        "--symbol",
        default="GC=F"
    )


    parser.add_argument(
        "--period",
        default="3mo"
    )


    parser.add_argument(
        "--rr",
        type=float,
        default=1.5
    )


    parser.add_argument(
        "--contract-size",
        type=float,
        default=100.0
    )


    parser.add_argument(
        "--backtest",
        action="store_true"
    )


    parser.add_argument(
        "--once",
        action="store_true"
    )


    parser.add_argument(
        "--plot",
        default="smc_v3_equity.png"
    )


    args = parser.parse_args()


    params = EngineParams(
        rr_min=args.rr,
        contract_size=args.contract_size
    )


    # ========================================================
    # ONCE
    # ========================================================

    if args.once:

        output = run_once(
            args.symbol
        )

        print(
            "\n================ LIVE ================"
        )

        print(output)

        return


    # ========================================================
    # BACKTEST
    # ========================================================

    if args.backtest:

        print(
            f"\nDownloading {args.symbol} ..."
        )

        df5, df1 = load_data(
            args.symbol
        )


        period_days = {

            "1mo": 30,

            "3mo": 90,

            "6mo": 60
        }.get(
            args.period,
            90
        )


        start = (
            df5.index[-1]
            -
            pd.Timedelta(
                days=period_days
            )
        )


        df5 = df5[
            df5.index >= start
        ]


        print(
            f"5M candles: {len(df5)}"
        )

        print(
            f"1H candles: {len(df1)}"
        )


        backtester = Backtester(
            df5,
            df1,
            params
        )


        result = backtester.run()


        metrics = result.metrics()


        print(
            "\n=========================================="
        )

        print(
            "       SMC ENGINE v3 BACKTEST"
        )

        print(
            "=========================================="
        )


        for key, value in metrics.items():

            if isinstance(
                value,
                float
            ):

                if key in (
                    "win_rate",
                    "tp1_hit_rate"
                ):

                    print(
                        f"{key:>18}: "
                        f"{value * 100:.2f}%"
                    )

                elif np.isfinite(value):

                    print(
                        f"{key:>18}: "
                        f"{value:.3f}"
                    )

                else:

                    print(
                        f"{key:>18}: INF"
                    )

            else:

                print(
                    f"{key:>18}: "
                    f"{value}"
                )


        print(
            "\nSkipped:"
            f" {result.skipped}"
        )


        print_trade_report(
            result
        )


        backtester.plot(
            result,
            args.plot
        )

        return


    parser.print_help()


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    main()
