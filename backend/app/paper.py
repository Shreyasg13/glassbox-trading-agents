"""Paper-trading + backtest engine.

Turns the quant engine's BUY/SELL/HOLD signals into simulated portfolios so
the strategy can be measured, compared and improved. No database and no
network in here -- accounts are plain dicts and prices come from a PriceBook,
so the same code drives the historical replay ("backtest") and the daily live
cycle, and can be tested exhaustively. Persistence and scheduling live in
app/scripts/run_paper_cycle.py; the admin API is routers/paper.py.

RULES (deliberately simple, all stated so results can be audited):

* Decisions use day D's close; orders EXECUTE at day D+1's close. Signals at D
  use D's close, so filling at D's close would be a look-ahead. Fractional
  shares, long-only, no leverage, no shorting.
* Costs: COMMISSION_BPS (default 5 bps) of every traded notional.
* engine_tilt: target weight of a symbol = invested * strategic_weight * tilt,
  where tilt is the engine's own suggested-weight multiplier (BUY 1.5x, HOLD
  1.0x, SELL 0.5x -- the same numbers /api/live-signals reports). Total above
  100% is scaled down; anything left over is cash, so SELL signals raise cash
  (risk-off) and BUY signals deploy it. A position is capped at the risk
  policy's max_position unless the strategic weight itself is larger.
  Trading is event-driven, not every-wiggle: an account rebalances when its
  signal set changes, or every REBALANCE_DAYS trading days if any weight has
  drifted more than DRIFT_THRESHOLD from target.
* static_rebalanced: the same strategic weights and the SAME rebalancing rules
  but every signal treated as HOLD. Every profile gets one as its benchmark
  ("policy portfolio"), so alpha = what the signals added, net of costs,
  rather than being muddied by rebalancing-vs-buy-and-hold effects.
* static_hold: buy the weights once and never trade (SPY / equal-weight
  controls).
* random_tilt: a placebo. Each symbol is driven by a DIFFERENT symbol's real
  signal history (a seeded cyclic shift), so signal frequency and persistence
  are exactly the engine's but any relationship to the symbol's own price is
  destroyed. If the engine cannot beat this, its signals carry no information.
* Every trading day is tagged "backtest" (before the account's live_from date)
  or "live". Backtest results are IN-SAMPLE: the trained parameters
  (RSI thresholds, MA windows) were fitted on this same history, so backtest
  returns are optimistic. Live results are genuine out-of-sample paper trading.
* committee_tilt: engine_tilt, except that a symbol the Investment Committee reviewed
  in the last COMMITTEE_MAX_AGE_DAYS trading days is tilted by the committee's
  risk-gated action instead of the raw engine signal. Same universe and rules as the
  "engine on all symbols" control, so the difference between the two is exactly what
  the committee added. It only differs from the engine on live days (there is no
  committee history to replay).
* Tax lens (an ESTIMATE, not advice): every buy opens a FIFO lot and every sell closes
  lots, splitting realised gains into short-term (held 365 days or less) and long-term.
  est_tax applies PAPER_TAX_ST / PAPER_TAX_LT (assumed 32% / 15%) to the net gains, so
  the leaderboard can show what an active strategy keeps AFTER tax versus buy-and-hold,
  where the gain stays unrealised and untaxed.
* Wash sales (taxable accounts): a loss realised on a symbol that was also bought in the 30
  days before or after is DISALLOWED and added to the replacement shares' cost basis, so a
  strategy that sells at a loss and rebuys soon after cannot bank the loss. (Simplified: symbol
  level, holding period of the replacement is not tacked on.)
* tax_status: "taxable" (the default; the tax lens above applies) or "sheltered" (IRA / 401k /
  Roth-style: no tax on realised gains and no wash-sale rule, trading costs only). Both wrappers
  are tracked as separate accounts so the same strategy can be compared in each.
* engine_taxaware (for the taxable wrapper): the engine, minus what makes it expensive after tax.
    - Lot selection: sells losses first, then long-term gains, and short-term gains last.
    - Short-term gain lock: a lot held a year or less at a gain is NOT sold unless that
      symbol's risk regime is HIGH (then protecting the gain wins over the tax).
    - Signals must persist TA_DEBOUNCE_DAYS trading days before they are acted on.
    - Wider rebalance bands (TA_DRIFT_THRESHOLD / TA_REBALANCE_DAYS / TA_MIN_TRADE_FRACTION).
    - A BUY into a HIGH-risk regime is held at HOLD.
  The pre-tax return can be lower than the plain engine's; what matters is the return AFTER tax.
* external_tilt (the arena): trades the tilts (BUY 1.5x, HOLD 1.0x, SELL 0.5x) of an OUTSIDE challenger's recorded
  daily calls, e.g. another multi-agent framework, on the same universe and rules as "engine on all symbols". A symbol
  the challenger gave no fresh call on is NEUTRAL (HOLD), never quietly backed by our own engine, so the result is the
  challenger's and nothing else.
* trend_filter (Faber-style, no engine signals): a symbol is held at its strategic weight only while its
  close at the END OF THE PREVIOUS MONTH was above its 200-day average; otherwise that slice sits in cash.
  The state changes at most once a month and uses only past data, so it neither peeks nor churns.
* vol_target (risk-managed sizing, no engine signals): the strategic basket is held in full when its trailing
  VOL_WINDOW-day volatility is at or below VOL_TARGET, and scaled by VOL_TARGET / volatility above that,
  rounded to VOL_STEP (10%) steps so it only trades on a real change in regime. Never above 100%: no leverage.
  Both were specified in advance from their standard textbook forms (200 days, 15% / 60 days) and are NOT
  tuned to this history; they are judged like every other strategy, by their result after tax.
"""
from __future__ import annotations

import bisect
import hashlib
import math
import os
from datetime import date as _date
from typing import Any, Dict, List, Optional, Tuple

from . import data_source as ds
from . import risk as _risk

COMMISSION_BPS = float(os.environ.get("PAPER_COMMISSION_BPS", "5"))
DRIFT_THRESHOLD = 0.05
REBALANCE_DAYS = 21  # trading days between drift-driven rebalances (~monthly)
MIN_TRADE_FRACTION = 0.002  # ignore rebalances smaller than 0.2% of equity
TRADING_DAYS = 252
MAX_STORED_TRADES = 1000
MAX_GAP_DAYS = 5  # more calendar days than this between consecutive price bars is a hole in the data, not a weekend

TILT = {"BUY": 1.5, "HOLD": 1.0, "SELL": 0.5}

# invested = share of the portfolio deployed at neutral (all-HOLD) signals.
RISK_POLICY = {
    "conservative": {"invested": 0.60, "max_position": 0.35},
    "moderate": {"invested": 0.80, "max_position": 0.45},
    "aggressive": {"invested": 0.95, "max_position": 0.60},
}

STRATEGIES = ("engine_tilt", "static_hold", "static_rebalanced", "random_tilt", "cash", "committee_tilt", "engine_taxaware", "trend_filter", "vol_target", "external_tilt")

TREND_WINDOW = 200  # trading days in the trend average
VOL_TARGET = 0.15  # annualised volatility the sized basket aims for
VOL_WINDOW = 60  # trading days used to measure it
VOL_STEP = 0.10  # exposure moves in steps of this size
TAX_STATUSES = ("taxable", "sheltered")

# engine_taxaware knobs (see the module docstring)
TA_DEBOUNCE_DAYS = 5
TA_DRIFT_THRESHOLD = 0.10
TA_REBALANCE_DAYS = 63  # ~quarterly
TA_MIN_TRADE_FRACTION = 0.01
WASH_DAYS = 30

COMMITTEE_MAX_AGE_DAYS = 5  # a committee call stays in force this many trading days (its own 1-5 day horizon)
TAX_ST = float(os.environ.get("PAPER_TAX_ST", "0.32"))  # assumed rate on gains held <= 1 year (ordinary income)
TAX_LT = float(os.environ.get("PAPER_TAX_LT", "0.15"))  # assumed rate on gains held > 1 year
LONG_TERM_DAYS = 365


# --------------------------------------------------------------- price data --


class PriceBook:
    """Closes and per-day engine signals for a set of symbols, keyed by
    'YYYY-MM-DD' strings. Build with PriceBook.from_frames()."""

    def __init__(self) -> None:
        self.close: Dict[str, Dict[str, float]] = {}
        self.signal: Dict[str, Dict[str, Tuple[str, float, float]]] = {}  # date -> (signal, confidence, rsi)
        self._sorted_dates: Dict[str, List[str]] = {}
        self.dates: List[str] = []
        self.committee: Dict[str, Dict[str, str]] = {}  # symbol -> decision date -> committee action
        self._committee_dates: Dict[str, List[str]] = {}
        self.external: Dict[str, Dict[str, Dict[str, str]]] = {}  # challenger -> symbol -> decision date -> action
        self._external_dates: Dict[str, Dict[str, List[str]]] = {}

    def set_committee(self, decisions: Dict[str, Dict[str, str]]) -> None:
        self.committee = {sym: dict(by_date) for sym, by_date in decisions.items() if by_date}
        self._committee_dates = {sym: sorted(by_date) for sym, by_date in self.committee.items()}

    def _fresh_call(self, dts: Optional[List[str]], calls: Dict[str, str], d: str, max_age: int) -> Optional[str]:
        if not dts:
            return None
        i = bisect.bisect_right(dts, d)
        if not i:
            return None
        age = bisect.bisect_right(self.dates, d) - bisect.bisect_right(self.dates, dts[i - 1])
        return calls[dts[i - 1]] if age <= max_age else None

    def committee_at(self, sym: str, d: str, max_age: int = COMMITTEE_MAX_AGE_DAYS) -> Optional[str]:
        """The committee's action on `sym` as known after day d's close, if it is still fresh."""
        return self._fresh_call(self._committee_dates.get(sym), self.committee.get(sym, {}), d, max_age)

    def set_external(self, source: str, decisions: Dict[str, Dict[str, str]]) -> None:
        self.external[source] = {sym: dict(by_date) for sym, by_date in decisions.items() if by_date}
        self._external_dates[source] = {sym: sorted(by_date) for sym, by_date in self.external[source].items()}

    def external_at(self, source: Optional[str], sym: str, d: str, max_age: int = COMMITTEE_MAX_AGE_DAYS) -> Optional[str]:
        """A challenger's call on `sym` as known after day d's close, if it is still fresh (same rule as the committee)."""
        if not source:
            return None
        return self._fresh_call(self._external_dates.get(source, {}).get(sym), self.external.get(source, {}).get(sym, {}), d, max_age)

    @classmethod
    def from_frames(cls, frames: Dict[str, Any], params: Dict[str, Any]) -> "PriceBook":
        import numpy as np
        import pandas as pd

        book = cls()
        all_dates = set()
        for sym, df in frames.items():
            if df is None or len(df) == 0 or "Close" not in df:
                continue
            idx = pd.to_datetime(df.index)
            dstr = [ts.strftime("%Y-%m-%d") for ts in idx]
            p = params.get(sym, {})
            fast_col = f"MA_{p.get('fast_ma', 20)}"
            slow_col = f"MA_{p.get('slow_ma', 50)}"
            close = df["Close"].astype(float).to_numpy()
            rsi = df["RSI"].astype(float).to_numpy() if "RSI" in df else np.full(len(df), 50.0)
            fast = (df[fast_col] if fast_col in df else df["MA_20"] if "MA_20" in df else df["Close"]).astype(float).to_numpy()
            slow = (df[slow_col] if slow_col in df else df["MA_50"] if "MA_50" in df else df["Close"]).astype(float).to_numpy()

            closes: Dict[str, float] = {}
            sigs: Dict[str, Tuple[str, float, float]] = {}
            for i, d in enumerate(dstr):
                if not math.isfinite(close[i]) or close[i] <= 0:
                    continue
                closes[d] = float(close[i])
                # NaN indicators (warm-up rows) fail every comparison -> HOLD,
                # exactly as get_live_signals treats them.
                if fast[i] > slow[i]:
                    cross = "BULLISH"
                elif fast[i] < slow[i]:
                    cross = "BEARISH"
                else:
                    cross = "NEUTRAL"
                r = float(rsi[i]) if math.isfinite(rsi[i]) else 50.0
                sig, conf = ds.signal_from_indicators(r, cross, p)
                sigs[d] = (sig, float(conf), r)
            book.close[sym] = closes
            book.signal[sym] = sigs
            book._sorted_dates[sym] = sorted(closes)
            all_dates.update(closes)
        book.dates = sorted(all_dates)
        return book

    @property
    def symbols(self) -> List[str]:
        return sorted(self.close)

    def gaps(self) -> List[Dict[str, Any]]:
        """Holes in the shared calendar: [{from, to, days}], where days exceeds MAX_GAP_DAYS."""
        out = []
        for a, b in zip(self.dates, self.dates[1:]):
            n = (_date.fromisoformat(b) - _date.fromisoformat(a)).days
            if n > MAX_GAP_DAYS:
                out.append({"from": a, "to": b, "days": n})
        return out

    @property
    def latest_date(self) -> Optional[str]:
        return self.dates[-1] if self.dates else None

    def close_on(self, sym: str, d: str) -> Optional[float]:
        """Close on `d`, or the last known close before it (a halted symbol
        keeps its last price for valuation; it just can't be traded)."""
        series = self.close.get(sym)
        if not series:
            return None
        if d in series:
            return series[d]
        ds_ = self._sorted_dates[sym]
        i = bisect.bisect_right(ds_, d)
        return series[ds_[i - 1]] if i else None

    def signal_at(self, sym: str, d: str) -> Tuple[str, float]:
        s = self.signal.get(sym, {}).get(d)
        return (s[0], s[1]) if s else ("HOLD", 50.0)

    def signals_on(self, d: str, symbols: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        out = []
        for sym in symbols or self.symbols:
            s = self.signal.get(sym, {}).get(d)
            if s:
                out.append({"symbol": sym, "signal": s[0], "confidence": s[1], "rsi": round(s[2], 1), "close": self.close[sym][d]})
        return out


# ---------------------------------------------------------------- accounts --


def _normalise(weights: Dict[str, float]) -> Dict[str, float]:
    total = sum(w for w in weights.values() if w > 0)
    return {k: w / total for k, w in weights.items() if w > 0} if total > 0 else {}


def new_account(
    account_id: str,
    name: str,
    kind: str,
    strategy: str,
    weights: Dict[str, float],
    *,
    invested: float,
    start_cash: float = 100_000.0,
    risk_level: Optional[str] = None,
    username: Optional[str] = None,
    benchmark_id: Optional[str] = None,
    profile: Optional[Dict[str, Any]] = None,
    note: str = "",
    tax_status: str = "taxable",
    seed: Optional[str] = None,
    source: Optional[str] = None,
) -> Dict[str, Any]:
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy {strategy!r}")
    if tax_status not in TAX_STATUSES:
        raise ValueError(f"unknown tax_status {tax_status!r}")
    return {
        "tax_status": tax_status,
        "source": source,  # external_tilt only: whose recorded calls this account trades
        "seed": seed,  # placebo only: which account's shift to reuse, so a sheltered twin draws the SAME placebo
        "id": account_id,
        "name": name,
        "kind": kind,  # profile | benchmark | control | twin (a profile run by the committee)
        "strategy": strategy,
        "weights": _normalise(weights),
        "invested": invested,
        "risk_level": risk_level,
        "username": username,
        "benchmark_id": benchmark_id,
        "profile": profile or {},
        "note": note,
        "start_cash": float(start_cash),
        "cash": float(start_cash),
        "positions": {},
        "pending": None,
        "inception": None,
        "last_date": None,
        "curve": [],  # [date, equity, mode]
        "trades": [],
        "trade_count": 0,
        "cost_paid": 0.0,
        "traded_notional": 0.0,
        "last_targets": {},
        "last_tilt_key": None,
        "since_rebalance": 10**6,  # first decision is always "due"
        "lots": {},  # symbol -> [[buy date, shares, price], ...] oldest first (FIFO)
        "recent_losses": {},  # symbol -> [[sale date, bucket, loss per share, shares not yet matched], ...] (wash-sale watch)
        "tax": new_tax(),
    }


def new_tax() -> Dict[str, Any]:
    return {
        "tracked": True, "st": 0.0, "lt": 0.0, "held_notional_days": 0.0, "sold_notional": 0.0, "unrealized_st": 0.0, "unrealized_lt": 0.0,
        "wash_disallowed": 0.0, "deferred_notional": 0.0, "deferred_sells": 0,
    }


def _wash_on(acct: Dict[str, Any]) -> bool:
    return acct.get("tax_status", "taxable") == "taxable" and bool(acct.get("tax", {}).get("tracked"))


def _open_lot(acct: Dict[str, Any], sym: str, d: str, shares: float, price: float) -> None:
    if not acct.get("tax", {}).get("tracked"):
        return
    if _wash_on(acct):
        price = _wash_on_buy(acct, sym, d, shares, price)
    acct.setdefault("lots", {}).setdefault(sym, []).append([d, shares, price])


def _wash_on_buy(acct: Dict[str, Any], sym: str, d: str, shares: float, price: float) -> float:
    """A loss on `sym` sold within WASH_DAYS BEFORE this buy is disallowed (up to the shares bought):
    it stops counting as a realised loss and is added to the new lot's cost basis instead."""
    tax = acct["tax"]
    now, left, disallowed = _date.fromisoformat(d), shares, 0.0
    for entry in acct.get("recent_losses", {}).get(sym) or []:
        if left <= 1e-12:
            break
        sold_on, bucket, per_share, unmatched = entry
        if unmatched <= 1e-12 or (now - _date.fromisoformat(sold_on)).days > WASH_DAYS:
            continue
        match = min(left, unmatched)
        tax[bucket] += match * per_share  # the loss no longer counts
        entry[3] -= match
        left -= match
        disallowed += match * per_share
    if disallowed:
        tax["wash_disallowed"] = tax.get("wash_disallowed", 0.0) + disallowed
        price += disallowed / shares
    return price


def _lot_preference(now: _date, price: float):
    """Tax-aware sell order: short-term losses, long-term losses, long-term gains (smallest first),
    then short-term gains (smallest first)."""

    def key(lot: List[Any]):
        gain_ps = price - lot[2]
        long_term = (now - _date.fromisoformat(lot[0])).days > LONG_TERM_DAYS
        if gain_ps <= 0:
            return (0, 1 if long_term else 0, gain_ps)
        return (1 if long_term else 2, 0, gain_ps)

    return key


def _sellable_shares(acct: Dict[str, Any], sym: str, d: str, price: float, risk_high: bool) -> float:
    """Shares the tax-aware engine may sell today: lots at a loss, lots held over a year, and (only when
    the symbol's risk regime is HIGH) short-term gains too."""
    lots = acct.get("lots", {}).get(sym)
    if lots is None or risk_high:
        return acct["positions"].get(sym, 0.0)
    now = _date.fromisoformat(d)
    return sum(sh for lot_d, sh, px in lots if price <= px or (now - _date.fromisoformat(lot_d)).days > LONG_TERM_DAYS)


def _close_lots(acct: Dict[str, Any], sym: str, d: str, shares: float, price: float, cost: float) -> None:
    """Realise the gain on `shares` sold at `price` (sell commission `cost` reduces it). FIFO, except the
    tax-aware strategy picks lots by tax cost. A loss that a recent purchase makes a wash sale is disallowed."""
    tax = acct.get("tax")
    if not tax or not tax.get("tracked"):
        return
    lots = acct.setdefault("lots", {}).get(sym) or []
    sale = _date.fromisoformat(d)
    if acct.get("strategy") == "engine_taxaware":
        lots.sort(key=_lot_preference(sale, price))
    wash = _wash_on(acct)
    replacement_used = 0.0
    remaining = shares
    while remaining > 1e-12 and lots:
        lot = lots[0]
        lot_d, lot_shares, lot_px = lot
        take = min(remaining, lot_shares)
        days = (sale - _date.fromisoformat(lot_d)).days
        bucket = "lt" if days > LONG_TERM_DAYS else "st"
        pnl = take * (price - lot_px) - cost * (take / shares)
        tax[bucket] += pnl
        tax["held_notional_days"] += days * take * price
        tax["sold_notional"] += take * price
        remaining -= take
        if take >= lot_shares - 1e-12:
            lots.pop(0)
        else:
            lot[1] = lot_shares - take
        if pnl < 0 and wash:
            loss = -pnl
            # shares of this symbol bought in the 30 days BEFORE the sale (and still held) are replacements
            window = [l for l in lots if l is not lot and 0 <= (sale - _date.fromisoformat(l[0])).days <= WASH_DAYS]
            avail = max(0.0, sum(l[1] for l in window) - replacement_used)
            matched = min(take, avail)
            if matched > 1e-12:
                dis = loss * matched / take
                tax[bucket] += dis
                tax["wash_disallowed"] = tax.get("wash_disallowed", 0.0) + dis
                newest = max(window, key=lambda l: l[0])
                newest[2] += dis / newest[1]  # the disallowed loss moves into the replacement's basis
                replacement_used += matched
            unmatched = take - matched
            if unmatched > 1e-12:  # a purchase in the next 30 days would still make it a wash sale
                book_ = acct.setdefault("recent_losses", {}).setdefault(sym, [])
                book_[:] = [e for e in book_ if (sale - _date.fromisoformat(e[0])).days <= WASH_DAYS and e[3] > 1e-12]
                book_.append([d, bucket, loss / take, unmatched])
    if not lots:
        acct.get("lots", {}).pop(sym, None)


def refresh_unrealized(acct: Dict[str, Any], book: PriceBook, d: str) -> None:
    """Gains still sitting in open lots at day d, split by how long they have been held."""
    tax = acct.get("tax")
    if not tax or not tax.get("tracked"):
        return
    now, st, lt = _date.fromisoformat(d), 0.0, 0.0
    for sym, lots in (acct.get("lots") or {}).items():
        px = book.close_on(sym, d)
        if not px:
            continue
        for lot_d, shares, lot_px in lots:
            gain = shares * (px - lot_px)
            if (now - _date.fromisoformat(lot_d)).days > LONG_TERM_DAYS:
                lt += gain
            else:
                st += gain
    tax["unrealized_st"], tax["unrealized_lt"] = st, lt


def estimate_tax(st: float, lt: float) -> float:
    """Tax on realised gains at the assumed rates. A loss in one bucket offsets gains in the
    other; a net loss overall is simply zero tax (no carry-forward modelled)."""
    if st < 0 <= lt:
        lt, st = max(0.0, lt + st), 0.0
    elif lt < 0 <= st:
        st, lt = max(0.0, st + lt), 0.0
    return max(st, 0.0) * TAX_ST + max(lt, 0.0) * TAX_LT


def equity(acct: Dict[str, Any], book: PriceBook, d: str) -> float:
    total = acct["cash"]
    for sym, shares in acct["positions"].items():
        px = book.close_on(sym, d)
        if px:
            total += shares * px
    return total


def current_weights(acct: Dict[str, Any], book: PriceBook, d: str) -> Dict[str, float]:
    eq = equity(acct, book, d)
    if eq <= 0:
        return {}
    out = {}
    for sym, shares in acct["positions"].items():
        px = book.close_on(sym, d)
        if px:
            out[sym] = shares * px / eq
    return out


def _persistent_signal(book: PriceBook, sym: str, d: str, n: int = TA_DEBOUNCE_DAYS, lookback: int = 60) -> Tuple[str, float]:
    """The engine's most recent signal that has held for at least `n` consecutive trading days (HOLD if
    none lately). Acting only on persistent signals filters the flip-flops that generate taxable trades."""
    dates = book._sorted_dates.get(sym) or []
    sigs = book.signal.get(sym, {})
    end = bisect.bisect_right(dates, d) - 1
    floor = max(0, end - lookback)
    while end >= floor:
        cur = sigs.get(dates[end])
        if cur is None:
            break
        run, j = 1, end - 1
        while run < n and j >= 0 and sigs.get(dates[j]) is not None and sigs[dates[j]][0] == cur[0]:
            run += 1
            j -= 1
        if run >= n:
            return cur[0], cur[1]
        end = j  # this run was too short to act on: look at the one before it
    return "HOLD", 50.0


def _trend_state(book: PriceBook, sym: str, d: str) -> Tuple[bool, str]:
    """(invested?, why) for the trend filter on day d: was the LAST bar of the previous month above its
    200-day average? Cached per (symbol, month) on the book -- the answer is fixed for the whole month."""
    dates = book._sorted_dates.get(sym) or []
    i = bisect.bisect_right(dates, d) - 1
    if i < 0:
        return True, "no data"
    month = dates[i][:7]
    j = i
    while j > 0 and dates[j - 1][:7] == month:
        j -= 1
    k = j - 1  # last bar of the previous month
    if k < TREND_WINDOW - 1:
        return True, "not enough history for a 200-day average: stay invested"
    cache = book.__dict__.setdefault("_trend_cache", {})
    if (sym, k) not in cache:
        closes = book.close[sym]
        sma = sum(closes[dates[x]] for x in range(k - TREND_WINDOW + 1, k + 1)) / TREND_WINDOW
        px = closes[dates[k]]
        cache[(sym, k)] = (px > sma, px, sma)
    on, px, sma = cache[(sym, k)]
    return on, f"month-end {px:.2f} {'above' if on else 'below'} 200-day average {sma:.2f}"


def _basket_vol(book: PriceBook, weights: Dict[str, float], d: str, window: int = VOL_WINDOW) -> Optional[float]:
    """Annualised volatility of the strategic basket (fixed weights) over the last `window` trading days
    up to and including d, or None when there is not yet enough history."""
    key = (tuple(sorted(weights.items())), d, window)
    cache = book.__dict__.setdefault("_basket_vol_cache", {})
    if key in cache:
        return cache[key]
    end = bisect.bisect_right(book.dates, d)
    days = book.dates[max(0, end - window - 1): end]
    out: Optional[float] = None
    if len(days) >= window // 2 + 1:
        prev = {s: book.close_on(s, days[0]) for s in weights}
        rets: List[float] = []
        for k, day in enumerate(days[1:], start=1):
            gap = (_date.fromisoformat(day) - _date.fromisoformat(days[k - 1])).days > MAX_GAP_DAYS
            r = 0.0
            for s, w in weights.items():
                px, p0 = book.close_on(s, day), prev[s]
                if px and p0:
                    if not gap:  # a return that spans a hole in the data is not a daily return: leave it out of the volatility
                        r += w * (px / p0 - 1)
                    prev[s] = px
            rets.append(r)
        if len(rets) > 1:
            m = sum(rets) / len(rets)
            out = math.sqrt(sum((x - m) ** 2 for x in rets) / (len(rets) - 1)) * math.sqrt(TRADING_DAYS)
    cache[key] = out
    return out


def _placebo_symbol(seed: str, sym: str, symbols: List[str]) -> str:
    """The symbol whose real signal history stands in for `sym`: a cyclic shift
    of the sorted universe by a seeded amount (never 0, so never itself)."""
    n = len(symbols)
    if n < 2 or sym not in symbols:
        return sym
    k = 1 + int(hashlib.sha256(seed.encode()).hexdigest()[:8], 16) % (n - 1)
    return symbols[(symbols.index(sym) + k) % n]


def _target_weights(acct: Dict[str, Any], book: PriceBook, d: str) -> Tuple[Dict[str, float], Dict[str, str], Dict[str, str]]:
    """The weights this account wants to hold after seeing day d's close.
    Returns (weights, human-readable reasons, raw signal per symbol)."""
    strategy = acct["strategy"]
    if strategy == "cash":
        return {}, {}, {}
    invested, base = acct["invested"], acct["weights"]
    if strategy == "static_hold":
        return {s: invested * w for s, w in base.items()}, {s: "strategic weight" for s in base}, {s: "HOLD" for s in base}

    if strategy == "trend_filter":
        t_raw, t_reasons, t_sigs = {}, {}, {}
        for sym, w in base.items():
            on, why = _trend_state(book, sym, d)
            t_raw[sym] = invested * w if on else 0.0
            t_reasons[sym], t_sigs[sym] = why, "BUY" if on else "SELL"  # BUY = invested, SELL = in cash (only feeds the change key)
        return t_raw, t_reasons, t_sigs
    if strategy == "vol_target":
        vol = _basket_vol(book, base, d)
        exposure = 1.0 if not vol else min(1.0, VOL_TARGET / vol)
        exposure = round(exposure / VOL_STEP) * VOL_STEP
        why = f"vol-target exposure {exposure:.0%}" + (f" (basket volatility {vol:.0%} vs {VOL_TARGET:.0%} target)" if vol else " (not enough history: fully invested)")
        return {s: invested * w * exposure for s, w in base.items()}, {s: why for s in base}, {s: f"x{exposure:.1f}" for s in base}

    raw: Dict[str, float] = {}
    reasons: Dict[str, str] = {}
    sigs: Dict[str, str] = {}
    symbols = book.symbols
    for sym, w in base.items():
        if strategy == "static_rebalanced":
            sig, conf = "HOLD", 50.0
        elif strategy == "random_tilt":
            sig, conf = book.signal_at(_placebo_symbol(acct.get("seed") or acct["id"], sym, symbols), d)
        elif strategy == "engine_taxaware":
            sig, conf = _persistent_signal(book, sym, d)
            rk = _risk.risk_at(book, sym, d)
            if sig == "BUY" and rk and rk["level"] == "HIGH":  # never add into a HIGH-risk regime
                sig = "HOLD"
        elif strategy == "external_tilt":
            view = book.external_at(acct.get("source"), sym, d)
            sig, conf = (view, 50.0) if view else ("HOLD", 50.0)  # no fresh call from the challenger: neutral, never our engine
        elif strategy == "committee_tilt":
            view = book.committee_at(sym, d)
            sig, conf = (view, 50.0) if view else book.signal_at(sym, d)
        else:
            sig, conf = book.signal_at(sym, d)
        sigs[sym] = sig
        raw[sym] = invested * w * TILT[sig]
        if strategy == "external_tilt":
            reasons[sym] = f"{acct.get('source')} {sig}" if book.external_at(acct.get("source"), sym, d) else f"no fresh call from {acct.get('source')}: neutral"
        elif strategy == "committee_tilt":
            reasons[sym] = f"committee {sig}" if book.committee_at(sym, d) else f"engine {sig} ({conf:.0f}%), no committee view"
        elif strategy == "engine_taxaware":
            reasons[sym] = f"{sig} (persisted {TA_DEBOUNCE_DAYS}d, {conf:.0f}%)"
        else:
            reasons[sym] = f"{sig} ({conf:.0f}%)" if strategy == "engine_tilt" else ("policy weight" if strategy == "static_rebalanced" else f"placebo {sig}")
    total = sum(raw.values())
    if total > 1.0:
        raw = {s: v / total for s, v in raw.items()}
    policy = RISK_POLICY.get(acct.get("risk_level") or "")
    if policy:
        raw = {s: min(v, max(policy["max_position"], invested * base[s])) for s, v in raw.items()}
    return raw, reasons, sigs


def _record_trade(acct: Dict[str, Any], d: str, sym: str, side: str, shares: float, price: float, cost: float, reason: str) -> None:
    acct["trade_count"] += 1
    acct["cost_paid"] += cost
    acct["traded_notional"] += shares * price
    acct["trades"].append(
        {"date": d, "symbol": sym, "side": side, "shares": round(shares, 6), "price": round(price, 4), "cost": round(cost, 2), "reason": reason}
    )
    if len(acct["trades"]) > MAX_STORED_TRADES:
        del acct["trades"][: len(acct["trades"]) - MAX_STORED_TRADES]


def _execute_pending(acct: Dict[str, Any], book: PriceBook, d: str) -> None:
    pend, acct["pending"] = acct["pending"], None
    if not pend:
        return
    targets, reasons = pend["weights"], pend.get("reasons", {})
    eq = equity(acct, book, d)
    if eq <= 0:
        return
    bps = COMMISSION_BPS / 10_000.0
    taxaware = acct["strategy"] == "engine_taxaware"
    min_frac = TA_MIN_TRADE_FRACTION if taxaware else MIN_TRADE_FRACTION
    sells: List[Tuple[str, float, float]] = []
    buys: List[Tuple[str, float, float]] = []
    for sym in set(acct["positions"]) | set(targets):
        price = book.close.get(sym, {}).get(d)  # tradable only on a day it actually printed
        if not price:
            continue
        held_value = acct["positions"].get(sym, 0.0) * price
        delta = targets.get(sym, 0.0) * eq - held_value
        if abs(delta) < max(1.0, min_frac * eq):
            continue
        (sells if delta < 0 else buys).append((sym, delta, price))

    for sym, delta, price in sells:
        shares = min(-delta / price, acct["positions"].get(sym, 0.0))
        if taxaware:
            rk = _risk.risk_at(book, sym, d)
            allowed = _sellable_shares(acct, sym, d, price, bool(rk and rk["level"] == "HIGH"))
            if shares > allowed + 1e-9:  # the rest is locked: a short-term gain we would rather hold a while
                tax_ = acct.setdefault("tax", new_tax())
                tax_["deferred_notional"] = tax_.get("deferred_notional", 0.0) + (shares - allowed) * price
                tax_["deferred_sells"] = tax_.get("deferred_sells", 0) + 1
                shares = allowed
            if shares * price < max(1.0, min_frac * eq):
                continue
        proceeds = shares * price
        cost = proceeds * bps
        acct["cash"] += proceeds - cost
        left = acct["positions"].get(sym, 0.0) - shares
        if left > 1e-9:
            acct["positions"][sym] = left
        else:
            acct["positions"].pop(sym, None)
        _close_lots(acct, sym, d, shares, price, cost)
        if left <= 1e-9:
            acct.get("lots", {}).pop(sym, None)
        _record_trade(acct, d, sym, "SELL", shares, price, cost, reasons.get(sym, "rebalance"))

    want = sum(delta for _s, delta, _p in buys)
    if want > 0:
        scale = min(1.0, acct["cash"] / (want * (1 + bps)))  # never spend cash we don't have
        for sym, delta, price in buys:
            notional = delta * scale
            if notional < 1.0:
                continue
            shares = notional / price
            cost = notional * bps
            acct["cash"] -= notional + cost
            acct["positions"][sym] = acct["positions"].get(sym, 0.0) + shares
            _open_lot(acct, sym, d, shares, price)
            _record_trade(acct, d, sym, "BUY", shares, price, cost, reasons.get(sym, "rebalance"))


def _decide(acct: Dict[str, Any], book: PriceBook, d: str) -> None:
    if acct["strategy"] == "cash":
        return
    if acct["strategy"] == "static_hold":
        if acct["inception"] == d:  # buy once, on the first day only
            targets, reasons, _ = _target_weights(acct, book, d)
            acct["pending"] = {"weights": targets, "reasons": reasons, "decided": d}
            acct["last_targets"] = targets
        return
    targets, reasons, sigs = _target_weights(acct, book, d)
    acct["last_targets"] = targets
    tilt_key = "|".join(f"{s}:{sigs[s]}" for s in sorted(sigs))
    first = acct["last_tilt_key"] is None
    changed = tilt_key != acct["last_tilt_key"]
    now = current_weights(acct, book, d)
    drift = max((abs(targets.get(s, 0.0) - now.get(s, 0.0)) for s in set(targets) | set(now)), default=0.0)
    ta = acct["strategy"] == "engine_taxaware"  # wider bands: every rebalance can realise a taxable gain
    due = acct["since_rebalance"] >= (TA_REBALANCE_DAYS if ta else REBALANCE_DAYS) and drift > (TA_DRIFT_THRESHOLD if ta else DRIFT_THRESHOLD)
    if first or changed or due:
        acct["pending"] = {"weights": targets, "reasons": reasons, "decided": d}
        acct["last_tilt_key"] = tilt_key
        acct["since_rebalance"] = 0


def advance(
    acct: Dict[str, Any],
    book: PriceBook,
    *,
    live_from: str,
    upto: Optional[str] = None,
    start: Optional[str] = None,
) -> int:
    """Process every trading day after the account's last processed date (or
    from `start` for a new account) through `upto` (default: latest data).
    Idempotent: calling it again with nothing new processes nothing. Returns
    the number of days processed."""
    upto = upto or book.latest_date
    if upto is None:
        return 0
    if acct["last_date"] is not None:
        lo = bisect.bisect_right(book.dates, acct["last_date"])
    else:
        lo = bisect.bisect_left(book.dates, start) if start else 0
    hi = bisect.bisect_right(book.dates, upto)
    processed = 0
    for d in book.dates[lo:hi]:
        if acct["inception"] is None:
            acct["inception"] = d
        acct["since_rebalance"] += 1
        _execute_pending(acct, book, d)
        acct["curve"].append([d, round(equity(acct, book, d), 2), "live" if d >= live_from else "backtest"])
        _decide(acct, book, d)
        acct["last_date"] = d
        processed += 1
    return processed


# ----------------------------------------------------------------- metrics --


def _returns(values: List[float]) -> List[float]:
    return [b / a - 1 for a, b in zip(values, values[1:]) if a > 0]


def max_drawdown(values: List[float]) -> float:
    peak, worst = float("-inf"), 0.0
    for v in values:
        peak = max(peak, v)
        if peak > 0:
            worst = min(worst, v / peak - 1)
    return worst


def sharpe(values: List[float]) -> float:
    r = _returns(values)
    if len(r) < 2:
        return 0.0
    mean = sum(r) / len(r)
    var = sum((x - mean) ** 2 for x in r) / (len(r) - 1)
    return 0.0 if var <= 0 else mean / math.sqrt(var) * math.sqrt(TRADING_DAYS)


def annual_volatility(values: List[float]) -> float:
    r = _returns(values)
    if len(r) < 2:
        return 0.0
    mean = sum(r) / len(r)
    return math.sqrt(sum((x - mean) ** 2 for x in r) / (len(r) - 1)) * math.sqrt(TRADING_DAYS)


def _tax_summary(acct: Dict[str, Any], last: float) -> Dict[str, Any]:
    tax = acct.get("tax")
    status = acct.get("tax_status", "taxable")
    if not tax or not tax.get("tracked"):  # an account created before tax tracking: unknown, not zero
        return {"tax_status": status, "tax_tracked": False, "realized_st": None, "realized_lt": None, "est_tax": None, "after_tax_return": None, "tax_drag": None,
                "avg_holding_days": None, "unrealized_st": None, "unrealized_lt": None, "wash_disallowed": None, "deferred_notional": None, "deferred_sells": None,
                "liquidation_tax": None, "after_tax_liquidated_return": None}
    # inside an IRA / 401k / Roth-style wrapper realised gains are not taxed: after-tax == pre-tax
    est = 0.0 if status == "sheltered" else estimate_tax(tax["st"], tax["lt"])
    # "If sold today": also tax the gains still sitting in open lots. Without it, buy-and-hold (which never
    # sells) looks tax-free and any strategy that defers gains looks better than it is; deferral is worth
    # something, but the liability is real.
    liq = 0.0 if status == "sheltered" else estimate_tax(tax["st"] + tax.get("unrealized_st", 0.0), tax["lt"] + tax.get("unrealized_lt", 0.0))
    start = acct["start_cash"]
    return {
        "tax_status": status,
        "tax_tracked": True,
        "realized_st": round(tax["st"], 2),
        "realized_lt": round(tax["lt"], 2),
        "est_tax": round(est, 2),
        "after_tax_return": (last - est) / start - 1,
        "liquidation_tax": round(liq, 2),
        "after_tax_liquidated_return": (last - liq) / start - 1,
        "tax_drag": est / start,
        "avg_holding_days": (tax["held_notional_days"] / tax["sold_notional"]) if tax["sold_notional"] else None,
        "unrealized_st": round(tax.get("unrealized_st", 0.0), 2),
        "unrealized_lt": round(tax.get("unrealized_lt", 0.0), 2),
        "wash_disallowed": round(tax.get("wash_disallowed", 0.0), 2),
        "deferred_notional": round(tax.get("deferred_notional", 0.0), 2),
        "deferred_sells": tax.get("deferred_sells", 0),
    }


def since(acct: Dict[str, Any], d: str) -> Dict[str, Any]:
    """The account as if it had started at the close before day d: the curve keeps that close
    as its first point and its equity becomes start_cash, so every summary metric covers d onward."""
    curve = acct["curve"]
    before = [p for p in curve if p[0] < d]
    window = before[-1:] + [p for p in curve if p[0] >= d]
    if not window:
        return acct
    # shortcut: trades are capped at MAX_STORED_TRADES, so counts for a very early date can be low; store per-day totals if that matters
    trades = [t for t in acct["trades"] if t["date"] >= d]
    return {
        **acct,
        "curve": window,
        "start_cash": window[0][1],
        "trade_count": len(trades),
        "cost_paid": sum(t["cost"] for t in trades),
        "traded_notional": sum(t["shares"] * t["price"] for t in trades),
        "inception": window[0][0],
    }


def summarize(acct: Dict[str, Any], bench: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Leaderboard row: total and live-only return, risk, cost, and alpha vs
    the account's benchmark over the same dates."""
    curve = acct["curve"]
    values = [p[1] for p in curve]
    last = values[-1] if values else acct["start_cash"]
    total = last / acct["start_cash"] - 1
    live_pts = [p for p in curve if p[2] == "live"]
    live_ret: Optional[float] = None
    if live_pts:
        bt = [p for p in curve if p[2] == "backtest"]
        base = bt[-1][1] if bt else acct["start_cash"]
        live_ret = last / base - 1
    years = max(len(values) / TRADING_DAYS, 1e-9)
    out = {
        "id": acct["id"],
        "name": acct["name"],
        "kind": acct["kind"],
        "strategy": acct["strategy"],
        "username": acct.get("username"),
        "risk_level": acct.get("risk_level"),
        "profile": acct.get("profile") or {},
        "equity": round(last, 2),
        "start_equity": round(acct["start_cash"], 2),
        "profit": round(last - acct["start_cash"], 2),
        "total_return": total,
        "cagr": (last / acct["start_cash"]) ** (1 / years) - 1 if last > 0 and len(values) > 20 else None,
        "live_return": live_ret,
        "max_drawdown": max_drawdown(values),
        "sharpe": sharpe(values),
        "volatility": annual_volatility(values),
        "trade_count": acct["trade_count"],
        "cost_paid": round(acct["cost_paid"], 2),
        "turnover": acct["traded_notional"] / acct["start_cash"] / years,  # annualised, in multiples of starting capital
        "days": len(values),
        "live_days": len(live_pts),
        "inception": acct["inception"],
        "last_date": acct["last_date"],
        "cash_weight": (acct["cash"] / last) if last else 1.0,
        "benchmark_id": acct.get("benchmark_id"),
        "alpha": None,
        "live_alpha": None,
        **_tax_summary(acct, last),
    }
    if bench and bench["curve"]:
        b_last = bench["curve"][-1][1]
        out["benchmark_return"] = b_last / bench["start_cash"] - 1
        out["alpha"] = total - out["benchmark_return"]
        b_live = [p for p in bench["curve"] if p[2] == "live"]
        if live_ret is not None and b_live:
            b_bt = [p for p in bench["curve"] if p[2] == "backtest"]
            b_base = b_bt[-1][1] if b_bt else bench["start_cash"]
            out["live_alpha"] = live_ret - (b_last / b_base - 1)
    return out


# --------------------------------------------------------------- scorecard --


def signal_scorecard(book: PriceBook, horizons: Tuple[int, ...] = (1, 5, 20)) -> Dict[str, Any]:
    """How good has the engine's signal been? For every historical BUY / SELL /
    HOLD, the forward return over each horizon, measured the way the paper
    trader would trade it (enter at the NEXT day's close). Reported against the
    unconditional average so a signal's edge is visible, not just its sign.
    IN-SAMPLE, like the backtest (parameters were fitted on this history)."""
    stats: Dict[str, Dict[int, List[float]]] = {s: {h: [] for h in horizons} for s in ("BUY", "SELL", "HOLD", "ALL")}
    per_symbol: Dict[str, Dict[str, Dict[int, List[float]]]] = {}
    for sym in book.symbols:
        dates = book._sorted_dates[sym]
        closes = book.close[sym]
        sigs = book.signal[sym]
        per_symbol[sym] = {s: {h: [] for h in horizons} for s in ("BUY", "SELL", "HOLD")}
        for i, d in enumerate(dates):
            sig = sigs.get(d, ("HOLD", 50.0, 50.0))[0]
            for h in horizons:
                if i + 1 + h >= len(dates):
                    continue
                entry, exit_ = closes[dates[i + 1]], closes[dates[i + 1 + h]]
                r = exit_ / entry - 1
                stats[sig][h].append(r)
                stats["ALL"][h].append(r)
                per_symbol[sym][sig][h].append(r)

    def agg(vals: List[float], signal: str) -> Dict[str, Any]:
        if not vals:
            return {"n": 0, "mean_return": None, "hit_rate": None}
        mean = sum(vals) / len(vals)
        if signal == "BUY":
            hit = sum(1 for v in vals if v > 0) / len(vals)
        elif signal == "SELL":
            hit = sum(1 for v in vals if v < 0) / len(vals)
        else:
            hit = sum(1 for v in vals if v > 0) / len(vals)  # share of up-moves
        return {"n": len(vals), "mean_return": mean, "hit_rate": hit}

    overall = {
        sig: {str(h): agg(stats[sig][h], sig if sig != "ALL" else "HOLD") for h in horizons} for sig in ("BUY", "SELL", "HOLD", "ALL")
    }
    edge = {}
    for sig in ("BUY", "SELL"):
        edge[sig] = {}
        for h in horizons:
            m, base = overall[sig][str(h)]["mean_return"], overall["ALL"][str(h)]["mean_return"]
            # BUY edge = mean above average; SELL edge = mean BELOW average (a good sell avoids gains)
            edge[sig][str(h)] = None if m is None or base is None else (m - base if sig == "BUY" else base - m)
    return {
        "horizons": list(horizons),
        "signals": overall,
        "edge_vs_average": edge,
        "by_symbol": {
            sym: {sig: {str(h): agg(per_symbol[sym][sig][h], sig) for h in horizons} for sig in ("BUY", "SELL", "HOLD")} for sym in book.symbols
        },
        "in_sample": True,
        "as_of": book.latest_date,
    }
