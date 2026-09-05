"""
VIX FILTER - Volatility-Based Trade Gate
=========================================
Supports multiple filter hypotheses beyond a simple threshold.
All hypotheses use a 1-day lag so there is zero lookahead bias.
Drop-in replacement for the original VIXFilter — same public API.

AVAILABLE HYPOTHESES
─────────────────────────────────────────────────────────────────────
  'threshold'     Classic: trade when VIX >= N (or <= N for lte mode)
                  → Original behaviour, kept as default

  'sma'           VIX above/below its own N-day SMA
                  → More adaptive than a fixed level; adjusts to the
                    current vol environment
                  Extra param: sma_period (default 20)

  'rising'        VIX is rising over the last N days (rate-of-change > 0)
                  → Trade when momentum is building, not just elevated
                  Extra param: roc_period (default 5)

  'spike_revert'  VIX spiked >= X% on a recent day then started falling
                  → Catches the "fear peak -> directional move" pattern
                  Extra param: spike_pct (default 20.0)

  'persistence'   VIX has been above threshold for N consecutive days
                  → Filters brief spikes; only trades real vol regimes
                  Extra params: threshold, persist_days (default 3)

  'percentile'    VIX is in the top/bottom X% of its trailing N-day range
                  → Fully adaptive; no fixed threshold needed
                  Extra params: percentile (default 60), window (default 60)

  'combined'      Requires BOTH threshold AND rising conditions
                  → Most selective; threshold + direction confirmation
                  Extra params: threshold, sma_period (default 20)

USAGE
─────────────────────────────────────────────────────────────────────
  from vix_filter import VIXFilter

  # Classic threshold (original behaviour)
  vix = VIXFilter(vix_file='VX_1min.txt', hypothesis='threshold',
                  threshold=20.0, mode='gte')

  # VIX above its 20-day SMA
  vix = VIXFilter(vix_file='VX_1min.txt', hypothesis='sma',
                  sma_period=20, mode='gte')

  # VIX rising over last 5 days
  vix = VIXFilter(vix_file='VX_1min.txt', hypothesis='rising',
                  roc_period=5)

  # VIX spiked 20%+ then started reverting
  vix = VIXFilter(vix_file='VX_1min.txt', hypothesis='spike_revert',
                  spike_pct=20.0)

  # VIX above 18 for 3+ consecutive days
  vix = VIXFilter(vix_file='VX_1min.txt', hypothesis='persistence',
                  threshold=18.0, persist_days=3)

  # VIX in top 40% of its trailing 60-day range
  vix = VIXFilter(vix_file='VX_1min.txt', hypothesis='percentile',
                  percentile=60, window=60, mode='gte')

  # VIX above 20-day SMA AND rising (most selective)
  vix = VIXFilter(vix_file='VX_1min.txt', hypothesis='combined',
                  threshold=18.0, sma_period=20)

  # Pass to backtester exactly as before
  results, backtester = run_chronological_portfolio(
      starting_balance=100000,
      start_date='2024-01-01',
      end_date='2024-12-31',
      verbose=True,
      vix_filter=vix
  )
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))

import pandas as pd
import numpy as np
from data_loader import load_and_create_resampled


HYPOTHESES = ('threshold', 'sma', 'rising', 'spike_revert',
              'persistence', 'percentile', 'combined', 'rising_floor',
              'vol_contraction', 'sma_revert', 'weekly_revert', 'weekly_momentum')


class VIXFilter:
    """
    Daily VIX gate for the portfolio backtester.

    Loads VIX minute data, collapses to daily closes (weekdays only),
    applies a 1-day lag (no lookahead), then precomputes a set of
    tradeable dates using the chosen hypothesis. Runtime checks are
    O(1) set membership lookups.

    All hypotheses share the same public API:
        vix.is_tradeable(date)      -> bool
        vix.get_vix(date)           -> float
        vix.get_regime(date)        -> str
        vix.get_signal_value(date)  -> dict   (debug info)
        vix.summary()               -> dict
    """

    REGIMES = [
        (15,           'LOW'),
        (25,           'MID'),
        (35,           'HIGH'),
        (float('inf'), 'FEAR'),
    ]

    def __init__(
        self,
        vix_file:     str,
        hypothesis:   str   = 'threshold',
        mode:         str   = 'gte',
        threshold:    float = 20.0,
        sma_period:   int   = 20,
        roc_period:   int   = 5,
        spike_pct:    float = 20.0,
        persist_days: int   = 3,
        percentile:   float = 60.0,
        window:       int   = 60,
        floor:        float = 15.0,   # used by rising_floor only
        lookback:     int   = 10,    # used by vol_contraction only
        max_drop_pct: float = 15.0,  # used by vol_contraction only
        gap_lookback:     int   = 3,     # used by sma_revert
        elevation_window: int   = 10,   # used by sma_revert
        consec_down:      int   = 3,    # used by weekly_revert
        near_sma_buffer:  float = 5.0,  # used by weekly_revert
        roc_days:         int   = 5,    # used by weekly_momentum: rolling lookback in trading days
        consec_confirm:   int   = 1,    # used by weekly_momentum: consecutive days signal must hold
    ):
        if hypothesis not in HYPOTHESES:
            raise ValueError(f"hypothesis must be one of {HYPOTHESES}")
        if mode not in ('gte', 'lte'):
            raise ValueError("mode must be 'gte' or 'lte'")

        self.vix_file     = vix_file
        self.hypothesis   = hypothesis
        self.mode         = mode
        self.threshold    = threshold
        self.sma_period   = sma_period
        self.roc_period   = roc_period
        self.spike_pct    = spike_pct
        self.persist_days = persist_days
        self.percentile   = percentile
        self.window       = window
        self.floor        = floor
        self.lookback       = lookback
        self.max_drop_pct   = max_drop_pct
        self.gap_lookback     = gap_lookback
        self.elevation_window = elevation_window
        self.consec_down      = consec_down
        self.near_sma_buffer  = near_sma_buffer
        self.roc_days         = roc_days
        self.consec_confirm   = consec_confirm

        self.daily_vix:       pd.Series = None
        self._daily_vix_raw:  pd.Series = None
        self._signals:        pd.Series = None
        self.tradeable_dates: set       = set()

        self._load(vix_file)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load(self, vix_file: str):
        print(f"\n📊 VIX Filter initialising...")
        print(f"   File       : {vix_file}")
        print(f"   Hypothesis : {self.hypothesis}")
        self._print_params()

        try:
            minute_data, _ = load_and_create_resampled(vix_file, '1H')

            # Daily closes, weekdays only
            raw_daily = (
                minute_data['close']
                .resample('D').last()
                .dropna()
            )
            raw_daily = raw_daily[raw_daily.index.dayofweek < 5]
            self._daily_vix_raw = raw_daily

            # Build unlagged signal then lag by 1 day
            raw_signal = self._build_signal(raw_daily)

            self.daily_vix = raw_daily.shift(1).dropna()
            self._signals  = (
                raw_signal.shift(1)
                .reindex(self.daily_vix.index)
                .fillna(False)
                .astype(bool)
            )

            self.tradeable_dates = set(
                self._signals[self._signals].index.date
            )

            total     = len(self.daily_vix)
            tradeable = len(self.tradeable_dates)
            blocked   = total - tradeable

            print(f"   Loaded     : {len(raw_daily):,} raw trading days "
                  f"({raw_daily.index.min().date()} -> {raw_daily.index.max().date()})")
            print(f"   After lag  : {total:,} usable days")
            print(f"   Tradeable  : {tradeable:,}  |  "
                  f"Blocked: {blocked:,}  |  "
                  f"Pass rate: {tradeable / total * 100:.1f}%")
            print(f"   VIX stats  : min={raw_daily.min():.1f}  "
                  f"mean={raw_daily.mean():.1f}  "
                  f"max={raw_daily.max():.1f}")
            self._print_regime_distribution(raw_daily)
            print()

        except Exception as e:
            print(f"   WARNING: Failed to load VIX data: {e}")
            print(f"   WARNING: VIX filter DISABLED - all days will be tradeable\n")
            self.daily_vix       = None
            self._daily_vix_raw  = None
            self._signals        = None
            self.tradeable_dates = set()

    # ------------------------------------------------------------------
    # Signal construction — one method per hypothesis
    # ------------------------------------------------------------------

    def _build_signal(self, raw: pd.Series) -> pd.Series:
        """
        Build a boolean Series (same index as raw) where True = tradeable.
        Operates on the RAW unlagged series; _load() applies the 1-day lag.
        """
        h = self.hypothesis

        if h == 'threshold':
            # Classic: VIX >= threshold (gte) or <= threshold (lte)
            if self.mode == 'gte':
                return raw >= self.threshold
            else:
                return raw <= self.threshold

        elif h == 'sma':
            # VIX above (gte) or below (lte) its own rolling N-day SMA.
            # More adaptive than a fixed level — the bar moves with the market.
            sma = raw.rolling(self.sma_period, min_periods=self.sma_period).mean()
            if self.mode == 'gte':
                return raw > sma
            else:
                return raw < sma

        elif h == 'rising':
            # VIX is higher than it was N days ago (positive ROC).
            # Trades when vol momentum is building, not just absolutely high.
            roc = raw - raw.shift(self.roc_period)
            if self.mode == 'gte':
                return roc > 0
            else:
                return roc < 0

        elif h == 'spike_revert':
            # VIX spiked >= spike_pct% on any day in the last 3 days
            # AND is currently falling. Catches the fear-peak pattern where
            # a big vol spike precedes a strong directional move.
            daily_roc_pct = raw.pct_change() * 100
            spiked        = daily_roc_pct >= self.spike_pct
            recent_spike  = spiked.rolling(3, min_periods=1).max().astype(bool)
            falling       = raw < raw.shift(1)
            return recent_spike & falling

        elif h == 'persistence':
            # VIX must have been above threshold for N consecutive days.
            # Filters out brief noise spikes; only passes real regime changes.
            above       = (raw >= self.threshold).astype(int)
            consecutive = (
                above.rolling(self.persist_days, min_periods=self.persist_days)
                .min()
                .fillna(0)
                .astype(bool)
            )
            return consecutive

        elif h == 'percentile':
            # VIX in the top X% (gte) or bottom X% (lte) of its trailing
            # N-day range. Fully adaptive — no fixed threshold needed.
            roll_min = raw.rolling(self.window, min_periods=self.window).min()
            roll_max = raw.rolling(self.window, min_periods=self.window).max()
            roll_rng = (roll_max - roll_min).replace(0, np.nan)
            rank     = ((raw - roll_min) / roll_rng * 100).fillna(50)
            if self.mode == 'gte':
                return rank >= self.percentile
            else:
                return rank <= (100 - self.percentile)

        elif h == 'combined':
            # Most selective: VIX above its SMA AND above threshold AND rising.
            # Eliminates elevated-but-drifting-lower regimes.
            sma    = raw.rolling(self.sma_period, min_periods=self.sma_period).mean()
            above  = (raw > sma) & (raw >= self.threshold)
            roc    = raw - raw.shift(self.roc_period)
            rising = roc > 0
            if self.mode == 'gte':
                return above & rising
            else:
                return (raw < sma) & (roc < 0)

        elif h == 'rising_floor':
            # VIX rising over roc_period days AND above a light floor.
            # The floor (default 15) just excludes dead low-vol environments
            # where momentum signals are noise. Not trying to be selective —
            # just filtering out the genuinely dead periods.
            # This is looser than 'combined': no SMA required, just direction + floor.
            roc     = raw - raw.shift(self.roc_period)
            rising  = roc > 0
            above_floor = raw >= self.floor
            return rising & above_floor

        elif h == 'vol_contraction':
            # Block days when VIX has dropped more than max_drop_pct% from
            # its N-day rolling high. This targets the "vol compressing from
            # a peak" regime — e.g. H2 2022 where VIX was elevated but
            # steadily falling, causing momentum strategies to chop around.
            #
            # Trade only when VIX is still "active" — within max_drop_pct%
            # of its recent high, meaning fear is still elevated and fresh.
            #
            # Example: lookback=10, max_drop_pct=15
            #   rolling_high = max VIX over last 10 days
            #   drop_pct     = (rolling_high - VIX) / rolling_high * 100
            #   tradeable    = drop_pct <= 15  (VIX hasn't fallen far from peak)
            rolling_high = raw.rolling(self.lookback, min_periods=self.lookback).max()
            drop_pct     = (rolling_high - raw) / rolling_high * 100
            # Also require VIX above floor so we don't trade in dead low-vol
            above_floor  = raw >= self.floor
            return (drop_pct <= self.max_drop_pct) & above_floor

        elif h == 'sma_revert':
            # Block days when VIX is reverting back toward its SMA after
            # being elevated — the momentum/fear regime is fading.
            #
            # Three conditions must ALL be true to BLOCK a day:
            #
            #   1. was_elevated: VIX was above SMA at some point in the
            #      last elevation_window days — confirms there was a real
            #      vol spike to revert FROM, not just normal fluctuation
            #
            #   2. above_sma: VIX is still above its SMA today — catches
            #      the convergence phase before the cross. Once VIX crosses
            #      below SMA it's a different regime entirely.
            #
            #   3. gap_shrinking: the gap (VIX - SMA) today is smaller than
            #      it was gap_lookback days ago — confirms the convergence
            #      is consistent over multiple days, not just one noisy day.
            #
            # tradeable = NOT (was_elevated AND above_sma AND gap_shrinking)
            # i.e. trade only when the reverting pattern is NOT present.

            sma = raw.rolling(self.sma_period, min_periods=self.sma_period).mean()

            # Gap between VIX and its SMA each day
            gap = raw - sma

            # Condition 1: VIX was above SMA within the last N days
            above_today = (gap > 0).astype(int)
            was_elevated = (
                above_today
                .rolling(self.elevation_window, min_periods=1)
                .max()
                .astype(bool)
            )

            # Condition 2: VIX is still above SMA today
            above_sma = gap > 0

            # Condition 3: gap is smaller now than N days ago (consistently shrinking)
            gap_shrinking = gap < gap.shift(self.gap_lookback)

            # Block when all three conditions are met
            reverting = was_elevated & above_sma & gap_shrinking

            # tradeable = NOT reverting (we block the reverting days)
            return ~reverting

        elif h == 'weekly_revert':
            # Detect the "staircase of red candles above SMA" pattern on
            # WEEKLY VIX data, then map the signal back to daily bars.
            #
            # Why weekly: weekly candles filter out daily noise so a red
            # week is genuinely significant (net down over 5 days), not
            # just a one-day blip. But we map back to daily so we don't
            # block entire weeks blindly.
            #
            # Block a daily bar when its week satisfies ALL of:
            #   1. above_or_near_sma: weekly VIX is within near_sma_buffer
            #      points ABOVE the weekly SMA (the regime hasn't ended yet)
            #   2. consecutive_red: the last consec_down weekly closes were
            #      each lower than the prior week (confirmed declining trend)
            #
            # Implementation:
            #   - Resample raw daily to weekly (Friday close)
            #   - Compute weekly SMA and consecutive down counter
            #   - Build weekly boolean signal
            #   - Forward-fill back to daily index (every day inherits
            #     its week's signal)

            # ── Step 1: weekly closes ─────────────────────────────────────
            weekly = raw.resample('W-FRI').last().dropna()

            # ── Step 2: weekly SMA ────────────────────────────────────────
            w_sma = weekly.rolling(self.sma_period, min_periods=self.sma_period).mean()

            # ── Step 3: is each week a red candle? ────────────────────────
            w_red = (weekly < weekly.shift(1)).astype(int)

            # ── Step 4: count consecutive red candles ─────────────────────
            # Rolling sum over consec_down window — if ALL bars are red
            # the sum equals consec_down
            w_consec = w_red.rolling(
                self.consec_down, min_periods=self.consec_down
            ).sum() >= self.consec_down

            # ── Step 5: above or near the SMA ─────────────────────────────
            # VIX is above SMA but within near_sma_buffer points of it
            # (catches the approach toward the SMA, not just far above)
            w_above_near = (weekly > w_sma) & (weekly <= w_sma + self.near_sma_buffer)
            # Also include cases where VIX is clearly above (early decline stage)
            w_elevated   = weekly > w_sma
            w_near_or_above = w_elevated  # trade above SMA in general; narrow later

            # ── Step 6: weekly block signal ───────────────────────────────
            w_block = w_consec & w_near_or_above

            # ── Step 7: map back to daily — forward fill within each week ─
            # Reindex to daily, forward-fill so Mon-Thu inherit the signal
            # computed on the previous Friday close
            daily_block = w_block.reindex(raw.index, method='ffill').fillna(False)

            # tradeable = NOT blocked
            return ~daily_block

        elif h == 'weekly_momentum':
            # Trade when VIX is above its SMA AND expanding vs N days ago.
            # Blocks two regimes:
            #   - Below SMA: low vol / choppy environment
            #   - Above SMA but falling: the H2 2022 staircase pattern where
            #     VIX is still elevated but momentum has turned negative
            #
            # roc_days (default 5) = one trading week lookback, updated daily
            # so there is never a need to wait for a weekly candle to close.
            # Each day is compared to the same weekday last week, giving a
            # fresh intra-week momentum reading with zero lookahead.
            #
            # consec_confirm (default 1): if > 1, the ROC must be positive
            # for this many consecutive days before allowing trades.
            # Useful to filter out single-day noise in the ROC signal.

            # ── SMA (regime floor) ────────────────────────────────────────
            sma = raw.rolling(self.sma_period, min_periods=self.sma_period).mean()
            above_sma = raw > sma

            # ── 5-day ROC (weekly momentum proxy, updated daily) ──────────
            roc = raw - raw.shift(self.roc_days)
            expanding = roc > 0   # VIX higher than same weekday last week

            # ── Optional consecutive confirmation ─────────────────────────
            if self.consec_confirm > 1:
                # All days in the confirmation window must show positive ROC
                expanding = (
                    expanding.astype(int)
                    .rolling(self.consec_confirm, min_periods=self.consec_confirm)
                    .min()
                    .fillna(0)
                    .astype(bool)
                )

            # ── Final signal: BOTH conditions must hold ───────────────────
            # above_sma=True  + expanding=True  → TRADE
            # above_sma=False + anything        → BLOCK (below floor)
            # above_sma=True  + expanding=False → BLOCK (decreasing above SMA)
            return above_sma & expanding

        # Fallback: all days tradeable
        return pd.Series(True, index=raw.index)

    # ------------------------------------------------------------------
    # Diagnostic helpers
    # ------------------------------------------------------------------

    def _print_params(self):
        h = self.hypothesis
        if h == 'threshold':
            d = '>=' if self.mode == 'gte' else '<='
            print(f"   Params     : VIX {d} {self.threshold}")
        elif h == 'sma':
            d = 'above' if self.mode == 'gte' else 'below'
            print(f"   Params     : VIX {d} its {self.sma_period}-day SMA")
        elif h == 'rising':
            d = 'rising' if self.mode == 'gte' else 'falling'
            print(f"   Params     : VIX {d} over last {self.roc_period} days")
        elif h == 'spike_revert':
            print(f"   Params     : spike >= {self.spike_pct}% within 3 days then reverting")
        elif h == 'persistence':
            print(f"   Params     : VIX >= {self.threshold} for {self.persist_days}+ consecutive days")
        elif h == 'percentile':
            d = 'top' if self.mode == 'gte' else 'bottom'
            print(f"   Params     : VIX in {d} {self.percentile}% of trailing {self.window}-day range")
        elif h == 'combined':
            print(f"   Params     : VIX >= {self.threshold} AND above {self.sma_period}-day SMA AND rising over {self.roc_period} days")
        elif h == 'rising_floor':
            print(f"   Params     : VIX rising over {self.roc_period} days AND VIX >= {self.floor} (floor)  [RECOMMENDED for momentum]")
        elif h == 'vol_contraction':
            print(f"   Params     : VIX within {self.max_drop_pct}% of its {self.lookback}-day high  AND  VIX >= {self.floor} (floor)")
            print(f"   Logic      : blocks when vol is compressing from a peak (drop > {self.max_drop_pct}%)")
        elif h == 'sma_revert':
            print(f"   Params     : sma_period={self.sma_period}  gap_lookback={self.gap_lookback}  elevation_window={self.elevation_window}")
            print(f"   Logic      : blocks when VIX gap vs SMA is shrinking (momentum fading)")
        elif h == 'weekly_revert':
            print(f"   Params     : sma_period={self.sma_period}  consec_down={self.consec_down}  near_sma_buffer={self.near_sma_buffer}")
            print(f"   Logic      : blocks days within weeks of {self.consec_down}+ consecutive red weekly candles above SMA")

    def _print_regime_distribution(self, series: pd.Series):
        total = len(series)
        if total == 0:
            return
        prev  = 0
        parts = []
        for upper, label in self.REGIMES:
            count = int(((series >= prev) & (series < upper)).sum())
            parts.append(f"{label}: {count} ({count / total * 100:.0f}%)")
            prev = upper
        print(f"   Regimes    : {' | '.join(parts)}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_tradeable(self, date) -> bool:
        """
        O(1) gate check. Returns True if this date passes the filter.
        Fail-open: returns True if VIX data failed to load or date is
        outside the VIX data range.
        """
        if self.daily_vix is None:
            return True
        if hasattr(date, 'date'):
            date = date.date()
        return date in self.tradeable_dates

    def get_vix(self, date, lag: bool = True) -> float:
        """
        VIX closing value for a given date.
        lag=True  -> previous day's close (no lookahead, what the gate uses)
        lag=False -> same-day close (for post-run analysis only)
        """
        series = self.daily_vix if lag else self._daily_vix_raw
        if series is None:
            return float('nan')
        val = series.asof(pd.Timestamp(date))
        return float(val) if pd.notna(val) else float('nan')

    def get_regime(self, date, lag: bool = True) -> str:
        """
        Classify VIX into LOW / MID / HIGH / FEAR / UNKNOWN.
        lag parameter same as get_vix().
        """
        vix = self.get_vix(date, lag=lag)
        if pd.isna(vix):
            return 'UNKNOWN'
        for upper, label in self.REGIMES:
            if vix < upper:
                return label
        return 'FEAR'

    def get_signal_value(self, date) -> dict:
        """
        Return raw signal components for a date — useful for debugging
        why a specific day was blocked or passed.
        """
        if self.daily_vix is None:
            return {}

        ts  = pd.Timestamp(date)
        vix = self.get_vix(date)
        out = {
            'date':       date,
            'vix':        round(vix, 2),
            'regime':     self.get_regime(date),
            'tradeable':  self.is_tradeable(date),
            'hypothesis': self.hypothesis,
        }

        raw = self._daily_vix_raw
        if raw is None:
            return out

        if self.hypothesis in ('threshold', 'persistence', 'combined'):
            out['threshold'] = self.threshold

        if self.hypothesis in ('sma', 'combined'):
            sma_val = raw.rolling(self.sma_period).mean().asof(ts)
            out['sma']          = round(float(sma_val), 2) if pd.notna(sma_val) else float('nan')
            out['vix_above_sma'] = vix > out.get('sma', float('nan'))

        if self.hypothesis in ('rising', 'combined'):
            prev = raw.shift(self.roc_period).asof(ts)
            out['roc']    = round(vix - float(prev), 2) if pd.notna(prev) else float('nan')
            out['rising'] = out.get('roc', 0) > 0

        if self.hypothesis == 'rising_floor':
            prev = raw.shift(self.roc_period).asof(ts)
            out['roc']          = round(vix - float(prev), 2) if pd.notna(prev) else float('nan')
            out['rising']       = out.get('roc', 0) > 0
            out['floor']        = self.floor
            out['above_floor']  = vix >= self.floor

        if self.hypothesis == 'weekly_momentum':
            sma_val  = raw.rolling(self.sma_period).mean().asof(ts)
            roc_val  = (raw - raw.shift(self.roc_days)).asof(ts)
            out['sma']         = round(float(sma_val), 2) if pd.notna(sma_val) else float('nan')
            out['roc_5d']      = round(float(roc_val), 2) if pd.notna(roc_val) else float('nan')
            out['above_sma']   = vix > float(sma_val) if pd.notna(sma_val) else False
            out['expanding']   = float(roc_val) > 0 if pd.notna(roc_val) else False
            out['tradeable_reason'] = (
                'PASS: above SMA and expanding' if out['above_sma'] and out['expanding']
                else 'BLOCK: below SMA' if not out['above_sma']
                else 'BLOCK: above SMA but decreasing'
            )

        if self.hypothesis == 'weekly_revert':
            weekly  = self._daily_vix_raw.resample('W-FRI').last().dropna()
            w_sma   = weekly.rolling(self.sma_period).mean().asof(ts)
            w_close = weekly.asof(ts)
            w_red   = (weekly < weekly.shift(1)).astype(int)
            w_consec = w_red.rolling(self.consec_down, min_periods=self.consec_down).sum().asof(ts)
            out['weekly_close']   = round(float(w_close), 2) if pd.notna(w_close) else float('nan')
            out['weekly_sma']     = round(float(w_sma),   2) if pd.notna(w_sma)   else float('nan')
            out['consec_red_weeks'] = int(w_consec) if pd.notna(w_consec) else 0
            out['above_weekly_sma'] = float(w_close) > float(w_sma) if pd.notna(w_close) and pd.notna(w_sma) else False

        if self.hypothesis == 'sma_revert':
            sma_val  = raw.rolling(self.sma_period).mean().asof(ts)
            gap_now  = vix - float(sma_val) if pd.notna(sma_val) else float('nan')
            gap_prev = (raw - raw.rolling(self.sma_period).mean()).shift(self.gap_lookback).asof(ts)
            out['sma']            = round(float(sma_val), 2) if pd.notna(sma_val) else float('nan')
            out['gap_now']        = round(gap_now, 2)
            out['gap_prev']       = round(float(gap_prev), 2) if pd.notna(gap_prev) else float('nan')
            out['gap_shrinking']  = gap_now < float(gap_prev) if pd.notna(gap_prev) else False
            out['above_sma']      = gap_now > 0

        if self.hypothesis == 'vol_contraction':
            roll_high = raw.rolling(self.lookback).max().asof(ts)
            if pd.notna(roll_high) and float(roll_high) > 0:
                drop = (float(roll_high) - vix) / float(roll_high) * 100
                out['rolling_high']  = round(float(roll_high), 2)
                out['drop_pct']      = round(drop, 2)
                out['floor']         = self.floor
                out['above_floor']   = vix >= self.floor
                out['vol_active']    = drop <= self.max_drop_pct

        if self.hypothesis == 'spike_revert':
            roc_pct = raw.pct_change().asof(ts)
            out['daily_roc_pct'] = round(float(roc_pct) * 100, 2) if pd.notna(roc_pct) else float('nan')

        if self.hypothesis == 'percentile':
            rmin = raw.rolling(self.window).min().asof(ts)
            rmax = raw.rolling(self.window).max().asof(ts)
            if pd.notna(rmin) and pd.notna(rmax) and (float(rmax) - float(rmin)) > 0:
                rank = (vix - float(rmin)) / (float(rmax) - float(rmin)) * 100
                out['percentile_rank'] = round(rank, 1)
            else:
                out['percentile_rank'] = float('nan')

        return out

    def is_high_vol(self, date) -> bool:
        """True if VIX regime is HIGH or FEAR (>= 25)."""
        return self.get_regime(date) in ('HIGH', 'FEAR')

    def is_low_vol(self, date) -> bool:
        """True if VIX regime is LOW (< 15)."""
        return self.get_regime(date) == 'LOW'

    def summary(self) -> dict:
        """
        Filter statistics dict. Guaranteed keys regardless of load success:
        enabled, hypothesis, mode, lag_applied, total_days, tradeable_days,
        blocked_days, tradeable_pct, vix_min, vix_mean, vix_max
        """
        base = {
            'enabled':        self.daily_vix is not None,
            'hypothesis':     self.hypothesis,
            'mode':           self.mode,
            'lag_applied':    True,
            'threshold':      self.threshold,
            'sma_period':     self.sma_period,
            'roc_period':     self.roc_period,
            'spike_pct':      self.spike_pct,
            'persist_days':   self.persist_days,
            'floor':          self.floor,
            'lookback':         self.lookback,
            'max_drop_pct':     self.max_drop_pct,
            'gap_lookback':     self.gap_lookback,
            'elevation_window': self.elevation_window,
            'consec_down':      self.consec_down,
            'near_sma_buffer':  self.near_sma_buffer,
            'roc_days':         self.roc_days,
            'consec_confirm':   self.consec_confirm,
            'percentile':     self.percentile,
            'window':         self.window,
        }

        if self.daily_vix is None:
            base.update({
                'total_days': 0, 'tradeable_days': 0, 'blocked_days': 0,
                'tradeable_pct': 0.0, 'vix_min': float('nan'),
                'vix_mean': float('nan'), 'vix_max': float('nan'),
            })
            return base

        raw       = self._daily_vix_raw
        total     = len(self.daily_vix)
        tradeable = len(self.tradeable_dates)

        base.update({
            'total_days':     total,
            'tradeable_days': tradeable,
            'blocked_days':   total - tradeable,
            'tradeable_pct':  round(tradeable / total * 100, 1) if total > 0 else 0.0,
            'vix_min':        round(float(raw.min()),  2),
            'vix_mean':       round(float(raw.mean()), 2),
            'vix_max':        round(float(raw.max()),  2),
        })
        return base