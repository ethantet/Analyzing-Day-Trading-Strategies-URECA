"""
Distribution Phase — Climactic Up-Day Short Strategy Backtester
================================================================
Strategy rules:
  Signal  : Daily close up > 1× ATR (14-day)
  Entry   : Short at open of NEXT day (no lookahead)
  Stop    : Entry + 1.0 × ATR
  Target  : Entry − 1R  (R = stop − entry)
  Time    : Close at end of day 3 if neither stop nor target hit

Modes:
  phase  → only trade inside predefined distribution phase windows
  sweep  → trade every valid signal across entire price history,
            then compare WR inside vs outside predefined phase windows

Run:
    python distribution_short_backtester.py
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))
from data_loader import load_and_create_resampled
from asset_configs import ASSET_CONFIGS

# ── SHARED STRATEGY PARAMS ────────────────────────────────────────────────────
ATR_WINDOW       = 14
ATR_THRESHOLD    = 1.0
STOP_ATR_BUFFER  = 1.0
R_MULTIPLE       = 1.0
TIME_EXIT_DAYS   = 3

STARTING_BALANCE = 100_000
RISK_PCT         = 2.0
MAX_MARGIN_PCT   = 80.0

PHASE_COLORS = ['#58a6ff', '#f0e040', '#ff6b35', '#26a641', '#ce93d8', '#ff9100', '#f85149']


# ── LOAD DATA ─────────────────────────────────────────────────────────────────
def load_data(cfg):
    print(f"Loading minute data from {cfg['data_file']} and resampling to daily bars...")
    minute_data, daily = load_and_create_resampled(cfg["data_file"], timeframe='1D')
    daily.index       = pd.to_datetime(daily.index)
    minute_data.index = pd.to_datetime(minute_data.index)
    print(f"  Daily bars : {len(daily):,}  ({daily.index[0].date()} → {daily.index[-1].date()})")
    print(f"  Minute bars: {len(minute_data):,}")

    # ── Precompute date → minute bars index ───────────────────────────────────
    # Groups minute bars by date once so simulate_trade can do O(1) lookups
    # instead of scanning the entire minute dataset per trade.
    print("  Building minute bar index...")
    minute_index = {}
    for date, group in minute_data.groupby(minute_data.index.date):
        minute_index[date] = group
    print(f"  Index built: {len(minute_index)} trading days")

    return minute_data, daily, minute_index


# ── FEATURE ENGINEERING ───────────────────────────────────────────────────────
# ── REGIME PARAMETERS ────────────────────────────────────────────────────────
ATR_MA_WINDOW  = 30    # ATR elevated if ATR > its N-day MA
MA_FAST        = 50    # trend MAs
MA_SLOW        = 200


def build_features(daily):
    df = daily.copy()

    # True range → ATR
    tr = pd.concat([
        df['high'] - df['low'],
        (df['high'] - df['close'].shift(1)).abs(),
        (df['low']  - df['close'].shift(1)).abs(),
    ], axis=1).max(axis=1)
    df['atr']      = tr.rolling(ATR_WINDOW).mean()
    df['atr_move'] = (df['close'] - df['open']) / df['atr']
    df['signal']   = df['atr_move'] >= ATR_THRESHOLD

    # ── Regime 1: Volatility — ATR elevated vs its own MA ────────────────────
    df['atr_ma']          = df['atr'].rolling(ATR_MA_WINDOW).mean()
    df['regime_vol_high'] = df['atr'] > df['atr_ma']   # True = high vol regime

    # ── Regime 2: Trend — price relative to 50 and 200 day MA ────────────────
    df['ma_fast']              = df['close'].rolling(MA_FAST).mean()
    df['ma_slow']              = df['close'].rolling(MA_SLOW).mean()
    df['regime_downtrend_50']  = df['close'] < df['ma_fast']   # below 50d MA
    df['regime_downtrend_200'] = df['close'] < df['ma_slow']   # below 200d MA
    df['regime_bear']          = (df['close'] < df['ma_fast']) & (df['close'] < df['ma_slow'])  # below both

    return df


# ── PHASE MEMBERSHIP HELPER ───────────────────────────────────────────────────
def build_phase_mask(daily, cfg):
    """
    Returns a Series (bool, indexed like daily) — True if that date
    falls inside any predefined distribution phase window.
    Also returns a Series mapping each date to its phase name (or None).
    """
    in_phase   = pd.Series(False, index=daily.index)
    phase_name = pd.Series(None,  index=daily.index, dtype=object)
    for name, start, end in cfg["distribution_phases"]:
        mask = (daily.index >= start) & (daily.index <= end)
        in_phase[mask]   = True
        phase_name[mask] = name
    return in_phase, phase_name


# ── POSITION SIZING ───────────────────────────────────────────────────────────
def calculate_units(balance, entry, stop, cfg):
    risk_per_unit        = stop - entry
    if risk_per_unit <= 0:
        return 0
    dollar_risk_per_unit = risk_per_unit * cfg["point_value"]
    max_risk_dollars     = balance * (RISK_PCT / 100)
    units_by_risk        = int(max_risk_dollars / dollar_risk_per_unit)
    notional_per_unit    = entry * cfg["point_value"]
    margin_per_unit      = notional_per_unit * (cfg["initial_margin_pct"] / 100)
    available_for_margin = balance * (MAX_MARGIN_PCT / 100)
    units_by_margin      = int(available_for_margin / margin_per_unit) if margin_per_unit > 0 else units_by_risk
    return max(1, min(units_by_risk, units_by_margin))


# ── MARGIN CALL CHECK ─────────────────────────────────────────────────────────
def check_margin_call(balance, entry, current_price, units, cfg):
    unrealised   = (entry - current_price) * cfg["point_value"] * units
    equity       = balance + unrealised
    maint_margin = entry * cfg["point_value"] * (cfg["maintenance_margin_pct"] / 100) * units
    return equity < maint_margin, equity, maint_margin


# ── SIMULATE ONE TRADE ────────────────────────────────────────────────────────
def simulate_trade(minute_data, daily, signal_date_idx, signal_row, balance, cfg, flat_sizing=False, minute_index=None):
    """
    flat_sizing=True  → always 1 unit, no margin check (sweep mode)
    flat_sizing=False → ATR-based position sizing with margin check (phase mode)
    """
    daily_dates   = daily.index
    n             = len(daily_dates)
    entry_day_idx = signal_date_idx + 1
    if entry_day_idx >= n:
        return None

    entry_date    = daily_dates[entry_day_idx]
    entry_price   = daily.iloc[entry_day_idx]['open']
    atr_val       = signal_row['atr']
    stop          = entry_price + STOP_ATR_BUFFER * atr_val
    risk          = stop - entry_price
    if risk <= 0:
        return None

    target = entry_price - R_MULTIPLE * risk

    if flat_sizing:
        units        = 1
        initial_margin = 0.0
        pct_bal_used   = 0.0
    else:
        units = calculate_units(balance, entry_price, stop, cfg)
        if units == 0:
            return None
        initial_margin = entry_price * cfg["point_value"] * (cfg["initial_margin_pct"] / 100) * units
        pct_bal_used   = (initial_margin / balance) * 100

    time_exit_day_idx = min(entry_day_idx + TIME_EXIT_DAYS, n - 1)
    time_exit_date    = daily_dates[time_exit_day_idx]

    # Use precomputed index if available, otherwise fall back to mask scan
    if minute_index is not None:
        # Collect all minute bars for the trade window days in one concat
        days = [d for d in pd.date_range(entry_date.date(), time_exit_date.date()).date
                if d in minute_index]
        trade_minutes = pd.concat([minute_index[d] for d in days]) if days else pd.DataFrame()
    else:
        mask = (
            (minute_data.index.date >= entry_date.date()) &
            (minute_data.index.date <= time_exit_date.date())
        )
        trade_minutes = minute_data[mask]

    outcome = exit_price = exit_time = exit_reason = None

    for minute_ts, minute_bar in trade_minutes.iterrows():
        bar_date = minute_ts.date()

        if not flat_sizing:
            margin_called, _, _ = check_margin_call(
                balance, entry_price, minute_bar['close'], units, cfg)
            if margin_called:
                exit_price, exit_time, exit_reason, outcome = (
                    minute_bar['close'], minute_ts, 'Margin Call', 'MARGIN_CALL')
                break

        if minute_bar['high'] >= stop:
            exit_price, exit_time, exit_reason, outcome = (
                stop, minute_ts, 'Stop Loss', 'STOP')
            break

        if minute_bar['low'] <= target:
            exit_price, exit_time, exit_reason, outcome = (
                target, minute_ts, 'Target', 'TARGET')
            break

        if bar_date == time_exit_date.date():
            next_same = trade_minutes[
                (trade_minutes.index > minute_ts) &
                (trade_minutes.index.date == time_exit_date.date())]
            if len(next_same) == 0:
                exit_price, exit_time, exit_reason, outcome = (
                    minute_bar['close'], minute_ts,
                    f'Time Exit (Day {TIME_EXIT_DAYS})', 'TIME')
                break

    if outcome is None:
        exit_price  = trade_minutes.iloc[-1]['close'] if len(trade_minutes) > 0 else entry_price
        exit_time   = trade_minutes.index[-1]         if len(trade_minutes) > 0 else entry_date
        exit_reason = 'Data End'
        outcome     = 'TIME'

    pnl_points  = entry_price - exit_price
    pnl_dollars = pnl_points * cfg["point_value"] * units
    r_realised  = pnl_points / risk if risk > 0 else 0

    # ATR multiple — how strong was the signal day move
    signal_row  = daily.iloc[signal_date_idx]
    atr_multiple = signal_row['atr_move']   # already computed in build_features

    # Market context at signal date — distance from MAs
    ma_fast_val  = signal_row.get('ma_fast',  np.nan)
    ma_slow_val  = signal_row.get('ma_slow',  np.nan)
    dist_50d_pct  = ((signal_row['close'] - ma_fast_val) / ma_fast_val * 100)                     if not np.isnan(ma_fast_val) and ma_fast_val != 0 else np.nan
    dist_200d_pct = ((signal_row['close'] - ma_slow_val) / ma_slow_val * 100)                     if not np.isnan(ma_slow_val) and ma_slow_val != 0 else np.nan

    # Volume ratio — signal day volume vs 20d average
    vol_ma = daily['volume'].rolling(20).mean().iloc[signal_date_idx]
    vol_ratio = (signal_row['volume'] / vol_ma)                 if not np.isnan(vol_ma) and vol_ma != 0 else np.nan

    return {
        'phase'           : None,
        'in_phase'        : None,
        'signal_date'     : daily_dates[signal_date_idx],
        'entry_date'      : entry_date,
        'exit_date'       : exit_time,
        'entry_price'     : entry_price,
        'stop'            : stop,
        'target'          : target,
        'exit_price'      : exit_price,
        'risk_points'     : risk,
        'atr'             : atr_val,
        'atr_multiple'    : round(atr_multiple, 3),
        'dist_50d_pct'    : round(dist_50d_pct,  2) if not np.isnan(dist_50d_pct)  else None,
        'dist_200d_pct'   : round(dist_200d_pct, 2) if not np.isnan(dist_200d_pct) else None,
        'vol_ratio'       : round(vol_ratio, 2)      if not np.isnan(vol_ratio)     else None,
        'units'           : units,
        'initial_margin'  : initial_margin,
        'pct_balance_used': pct_bal_used,
        'outcome'         : outcome,
        'exit_reason'     : exit_reason,
        'pnl_points'      : pnl_points,
        'pnl_dollars'     : pnl_dollars,
        'r_realised'      : r_realised,
        'balance_before'  : balance,
    }


# ── SHARED BACKTEST LOOP ──────────────────────────────────────────────────────
def _run_loop(minute_data, daily, cfg, phase_filter_df, mode_label):
    """
    Core loop — iterates over phase_filter_df rows looking for signals.
    phase_filter_df is either a phase slice (phase mode) or full daily (sweep mode).
    Returns (all_trades, balance_curve, stats_dict).
    """
    balance       = STARTING_BALANCE
    peak_balance  = STARTING_BALANCE
    max_dd_usd    = 0
    max_dd_pct    = 0
    all_trades    = []
    balance_curve = [{'date': daily.index[0], 'balance': balance}]
    unit_label    = "contracts" if cfg["instrument_type"] == "futures" else "shares"
    skip_until    = None

    full_daily_indices = [daily.index.get_loc(ts) for ts in phase_filter_df.index]

    for local_i, (ts, row) in enumerate(phase_filter_df.iterrows()):
        if pd.isna(row['atr']):
            continue
        if skip_until is not None and ts <= skip_until:
            continue
        if not row['signal']:
            continue

        full_i = full_daily_indices[local_i]
        print(f"    Signal: {ts.date()}  atr_move={row['atr_move']:.2f}×  atr={row['atr']:.2f}")

        trade = simulate_trade(minute_data, daily, full_i, row, balance, cfg, minute_index=minute_index)
        if trade is None:
            print(f"           → No trade (entry invalid or data missing)")
            continue

        trade['balance_after'] = balance + trade['pnl_dollars']
        balance = trade['balance_after']
        all_trades.append(trade)

        if balance > peak_balance:
            peak_balance = balance
        dd_usd = peak_balance - balance
        dd_pct = dd_usd / peak_balance * 100
        if dd_usd > max_dd_usd:
            max_dd_usd = dd_usd
            max_dd_pct = dd_pct

        balance_curve.append({'date': trade['exit_date'], 'balance': balance})
        skip_until = trade['exit_date']

        status = '✓' if trade['pnl_dollars'] > 0 else '✗'
        print(f"           → {status} {trade['exit_reason']:20s} | "
              f"entry={trade['entry_price']:.2f}  exit={trade['exit_price']:.2f} | "
              f"R={trade['r_realised']:+.2f}  P&L=${trade['pnl_dollars']:+,.0f}  "
              f"({trade['units']} {unit_label}) | bal=${balance:,.0f}")

    return (
        pd.DataFrame(all_trades),
        pd.DataFrame(balance_curve),
        {'max_dd_usd': max_dd_usd, 'max_dd_pct': max_dd_pct,
         'final_balance': balance, 'peak_balance': peak_balance},
    )


# ── PHASE MODE ────────────────────────────────────────────────────────────────
def run_backtest_phase(minute_data, daily, cfg, minute_index=None):
    print(f"\n{'─'*60}")
    print(f"  MODE: PHASE  —  {cfg['instrument_type'].upper()}")
    print(f"  Starting balance : ${STARTING_BALANCE:,.0f}  |  Risk: {RISK_PCT}%")
    print(f"{'─'*60}")

    balance       = STARTING_BALANCE
    peak_balance  = STARTING_BALANCE
    max_dd_usd    = 0
    max_dd_pct    = 0
    all_trades    = []
    balance_curve = [{'date': daily.index[0], 'balance': balance}]
    unit_label    = "contracts" if cfg["instrument_type"] == "futures" else "shares"

    for phase_name, phase_start, phase_end in cfg["distribution_phases"]:
        phase_df = daily.loc[phase_start:phase_end].copy()
        if len(phase_df) < ATR_WINDOW + 2:
            print(f"\n  {phase_name}: insufficient data, skipping.")
            continue

        print(f"\n  {phase_name}  ({phase_start} → {phase_end})")
        print(f"  {'─'*40}")

        phase_trades       = 0
        full_daily_indices = [daily.index.get_loc(ts) for ts in phase_df.index]
        skip_until         = None

        for local_i, (ts, row) in enumerate(phase_df.iterrows()):
            if pd.isna(row['atr']):
                continue
            if skip_until is not None and ts <= skip_until:
                continue
            if not row['signal']:
                continue

            full_i = full_daily_indices[local_i]
            print(f"    Signal: {ts.date()}  atr_move={row['atr_move']:.2f}×  atr={row['atr']:.2f}")

            trade = simulate_trade(minute_data, daily, full_i, row, balance, cfg, minute_index=minute_index)
            if trade is None:
                print(f"           → No trade")
                continue

            trade['phase']         = phase_name
            trade['in_phase']      = True
            trade['balance_after'] = balance + trade['pnl_dollars']
            balance = trade['balance_after']
            all_trades.append(trade)
            phase_trades += 1

            if balance > peak_balance:
                peak_balance = balance
            dd_usd = peak_balance - balance
            dd_pct = dd_usd / peak_balance * 100
            if dd_usd > max_dd_usd:
                max_dd_usd = dd_usd
                max_dd_pct = dd_pct

            balance_curve.append({'date': trade['exit_date'], 'balance': balance})
            skip_until = trade['exit_date']

            status = '✓' if trade['pnl_dollars'] > 0 else '✗'
            print(f"           → {status} {trade['exit_reason']:20s} | "
                  f"entry={trade['entry_price']:.2f}  exit={trade['exit_price']:.2f} | "
                  f"R={trade['r_realised']:+.2f}  P&L=${trade['pnl_dollars']:+,.0f}  "
                  f"({trade['units']} {unit_label}) | bal=${balance:,.0f}")

        print(f"  Phase trades: {phase_trades}")

    return (
        pd.DataFrame(all_trades),
        pd.DataFrame(balance_curve),
        {'max_dd_usd': max_dd_usd, 'max_dd_pct': max_dd_pct,
         'final_balance': balance, 'peak_balance': peak_balance},
    )


# ── SWEEP MODE ────────────────────────────────────────────────────────────────
def run_backtest_sweep(minute_data, daily, cfg, minute_index=None):
    """
    Sweep entire price history for signals regardless of phase windows.
    Fixed $100k notional on every trade — no compounding — so position sizing
    is constant and every trade's R/P&L is directly comparable.
    Tags each trade with in_phase=True/False and phase name for later analysis.
    """
    print(f"\n{'─'*60}")
    print(f"  MODE: SWEEP  —  {cfg['instrument_type'].upper()}")
    print(f"  Fixed notional   : ${STARTING_BALANCE:,.0f} per trade (no compounding)")
    print(f"  Sweeping {daily.index[0].date()} → {daily.index[-1].date()}")
    print(f"{'─'*60}")

    in_phase_mask, phase_name_series = build_phase_mask(daily, cfg)
    unit_label = "contracts" if cfg["instrument_type"] == "futures" else "shares"

    cumulative_pnl = 0.0
    peak_pnl       = 0.0
    max_dd_usd     = 0.0
    max_dd_pct     = 0.0
    all_trades     = []
    # Balance curve tracks $100k + cumulative P&L for equity curve visualisation
    balance_curve  = [{'date': daily.index[0], 'balance': STARTING_BALANCE}]
    skip_until     = None

    for i, (ts, row) in enumerate(daily.iterrows()):
        if pd.isna(row['atr']):
            continue
        if skip_until is not None and ts <= skip_until:
            continue
        if not row['signal']:
            continue

        # Always size off fixed STARTING_BALANCE — no compounding
        trade = simulate_trade(minute_data, daily, i, row, STARTING_BALANCE, cfg, flat_sizing=True, minute_index=minute_index)
        if trade is None:
            continue

        trade['in_phase']      = bool(in_phase_mask.loc[ts])
        trade['phase']         = phase_name_series.loc[ts]
        trade['balance_after'] = STARTING_BALANCE   # fixed — not used for sizing

        # ── Regime tags at signal date (no lookahead — all backward-looking MAs) ──
        trade['regime_vol_high']      = bool(row['regime_vol_high'])      if not pd.isna(row['regime_vol_high'])      else False
        trade['regime_downtrend_50']  = bool(row['regime_downtrend_50'])  if not pd.isna(row['regime_downtrend_50'])  else False
        trade['regime_downtrend_200'] = bool(row['regime_downtrend_200']) if not pd.isna(row['regime_downtrend_200']) else False
        trade['regime_bear']          = bool(row['regime_bear'])          if not pd.isna(row['regime_bear'])          else False

        all_trades.append(trade)

        cumulative_pnl += trade['pnl_dollars']
        equity          = STARTING_BALANCE + cumulative_pnl

        if cumulative_pnl > peak_pnl:
            peak_pnl = cumulative_pnl
        dd_usd = (STARTING_BALANCE + peak_pnl) - equity
        dd_pct = dd_usd / (STARTING_BALANCE + peak_pnl) * 100 if (STARTING_BALANCE + peak_pnl) > 0 else 0
        if dd_usd > max_dd_usd:
            max_dd_usd = dd_usd
            max_dd_pct = dd_pct

        balance_curve.append({'date': trade['exit_date'], 'balance': equity})
        skip_until = trade['exit_date']

        status = '✓' if trade['pnl_dollars'] > 0 else '✗'
        tag    = '[IN PHASE]' if trade['in_phase'] else '[outside ]'
        print(f"  {tag} {ts.date()}  → {status} {trade['exit_reason']:20s} | "
              f"R={trade['r_realised']:+.2f}  P&L=${trade['pnl_dollars']:+,.0f}  "
              f"({trade['units']} {unit_label}) | cumPnL=${cumulative_pnl:+,.0f}")

    final_equity = STARTING_BALANCE + cumulative_pnl
    return (
        pd.DataFrame(all_trades),
        pd.DataFrame(balance_curve),
        {'max_dd_usd': max_dd_usd, 'max_dd_pct': max_dd_pct,
         'final_balance': final_equity, 'peak_balance': STARTING_BALANCE + peak_pnl,
         'cumulative_pnl': cumulative_pnl},
    )


# ── PERFORMANCE SUMMARY — PHASE MODE ─────────────────────────────────────────
def print_summary_phase(trades_df, stats, cfg):
    print(f"\n{'='*60}  PHASE MODE SUMMARY  {'='*60}")
    if trades_df.empty:
        print("  No trades executed.")
        return

    total   = len(trades_df)
    wins    = trades_df[trades_df['pnl_dollars'] > 0]
    losses  = trades_df[trades_df['pnl_dollars'] <= 0]
    ret_pct = (stats['final_balance'] - STARTING_BALANCE) / STARTING_BALANCE * 100
    pf      = abs(wins['pnl_dollars'].sum() / losses['pnl_dollars'].sum()) \
              if len(losses) > 0 and losses['pnl_dollars'].sum() != 0 else float('inf')

    print(f"\n  Account")
    print(f"    Starting : ${STARTING_BALANCE:,.0f}   Final : ${stats['final_balance']:,.0f}   "
          f"Return : {ret_pct:+.2f}%   Max DD : {stats['max_dd_pct']:.1f}%")
    print(f"\n  Overall — {total} trades  WR={len(wins)/total*100:.1f}%  "
          f"PF={pf:.2f}  Avg R={trades_df['r_realised'].mean():+.2f}")

    print(f"\n  Per-phase breakdown")
    for phase_name, _, _ in cfg["distribution_phases"]:
        pt = trades_df[trades_df['phase'] == phase_name]
        if len(pt) == 0:
            print(f"    {phase_name:18s}: no trades")
            continue
        pw = pt[pt['pnl_dollars'] > 0]
        print(f"    {phase_name:18s}: {len(pt):2d} trades  "
              f"WR={len(pw)/len(pt)*100:.0f}%  "
              f"P&L=${pt['pnl_dollars'].sum():+,.0f}  "
              f"avg R={pt['r_realised'].mean():+.2f}")


# ── PERFORMANCE SUMMARY — SWEEP MODE ─────────────────────────────────────────
def print_summary_sweep(trades_df, stats, cfg):
    print(f"\n{'='*60}  SWEEP MODE SUMMARY  {'='*60}")
    if trades_df.empty:
        print("  No trades executed.")
        return

    total   = len(trades_df)
    wins    = trades_df[trades_df['pnl_dollars'] > 0]
    losses  = trades_df[trades_df['pnl_dollars'] <= 0]
    ret_pct = (stats['final_balance'] - STARTING_BALANCE) / STARTING_BALANCE * 100
    pf      = abs(wins['pnl_dollars'].sum() / losses['pnl_dollars'].sum()) \
              if len(losses) > 0 and losses['pnl_dollars'].sum() != 0 else float('inf')

    print(f"\n  Account")
    print(f"    Starting : ${STARTING_BALANCE:,.0f}   Final : ${stats['final_balance']:,.0f}   "
          f"Return : {ret_pct:+.2f}%   Max DD : {stats['max_dd_pct']:.1f}%")
    print(f"\n  Overall — {total} trades  WR={len(wins)/total*100:.1f}%  "
          f"PF={pf:.2f}  Avg R={trades_df['r_realised'].mean():+.2f}")

    # ── In-phase vs out-of-phase breakdown ────────────────────────────────────
    print(f"\n  IN-PHASE vs OUT-OF-PHASE")
    print(f"  {'─'*50}")
    for label, subset in [("Inside  phase windows", trades_df[trades_df['in_phase'] == True]),
                           ("Outside phase windows", trades_df[trades_df['in_phase'] == False])]:
        if len(subset) == 0:
            print(f"    {label}: no trades")
            continue
        sw   = subset[subset['pnl_dollars'] > 0]
        sl   = subset[subset['pnl_dollars'] <= 0]
        spf  = abs(sw['pnl_dollars'].sum() / sl['pnl_dollars'].sum()) \
               if len(sl) > 0 and sl['pnl_dollars'].sum() != 0 else float('inf')
        print(f"    {label}: {len(subset):3d} trades  "
              f"WR={len(sw)/len(subset)*100:.1f}%  "
              f"PF={spf:.2f}  "
              f"avg R={subset['r_realised'].mean():+.2f}  "
              f"P&L=${subset['pnl_dollars'].sum():+,.0f}")

    # ── Per predefined phase breakdown ────────────────────────────────────────
    print(f"\n  PER PREDEFINED PHASE WINDOW")
    print(f"  {'─'*50}")
    for phase_name, _, _ in cfg["distribution_phases"]:
        pt = trades_df[trades_df['phase'] == phase_name]
        if len(pt) == 0:
            print(f"    {phase_name:20s}: no signals hit")
            continue
        pw = pt[pt['pnl_dollars'] > 0]
        print(f"    {phase_name:20s}: {len(pt):2d} trades  "
              f"WR={len(pw)/len(pt)*100:.0f}%  "
              f"avg R={pt['r_realised'].mean():+.2f}  "
              f"P&L=${pt['pnl_dollars'].sum():+,.0f}")

    # ── Rolling WR (20-trade window) ──────────────────────────────────────────
    if total >= 20:
        trades_df = trades_df.copy()
        trades_df['win']        = (trades_df['pnl_dollars'] > 0).astype(int)
        trades_df['rolling_wr'] = trades_df['win'].rolling(20).mean() * 100
        print(f"\n  Rolling 20-trade WR: "
              f"min={trades_df['rolling_wr'].min():.1f}%  "
              f"max={trades_df['rolling_wr'].max():.1f}%  "
              f"mean={trades_df['rolling_wr'].mean():.1f}%")

    # ── Regime breakdown ──────────────────────────────────────────────────────
    def regime_stats(label, mask):
        subset = trades_df[mask]
        if len(subset) == 0:
            print(f"    {label:45s}: no trades")
            return
        w   = subset[subset['pnl_dollars'] > 0]
        l   = subset[subset['pnl_dollars'] <= 0]
        pf  = abs(w['pnl_dollars'].sum() / l['pnl_dollars'].sum())               if len(l) > 0 and l['pnl_dollars'].sum() != 0 else float('inf')
        print(f"    {label:45s}: n={len(subset):3d}  "
              f"WR={len(w)/len(subset)*100:.1f}%  "
              f"PF={pf:.2f}  "
              f"avg R={subset['r_realised'].mean():+.2f}")

    print(f"\n  REGIME BREAKDOWN — Hypothesis Testing")
    print(f"  {'─'*60}")

    print(f"\n  Hypothesis 2 — Volatility regime (ATR vs {ATR_MA_WINDOW}d MA)")
    regime_stats("High vol  (ATR > ATR MA)",  trades_df['regime_vol_high'] == True)
    regime_stats("Low vol   (ATR <= ATR MA)", trades_df['regime_vol_high'] == False)

    print(f"\n  Hypothesis 3a — Trend regime (50d MA)")
    regime_stats("Downtrend (price < 50d MA)", trades_df['regime_downtrend_50'] == True)
    regime_stats("Uptrend   (price > 50d MA)", trades_df['regime_downtrend_50'] == False)

    print(f"\n  Hypothesis 3b — Trend regime (200d MA)")
    regime_stats("Downtrend (price < 200d MA)", trades_df['regime_downtrend_200'] == True)
    regime_stats("Uptrend   (price > 200d MA)", trades_df['regime_downtrend_200'] == False)

    print(f"\n  Hypothesis 4 — Combined (below both MAs + high vol)")
    regime_stats("Bear + high vol",             (trades_df['regime_bear'] == True)  & (trades_df['regime_vol_high'] == True))
    regime_stats("Bear + low vol",              (trades_df['regime_bear'] == True)  & (trades_df['regime_vol_high'] == False))
    regime_stats("Bull + high vol",             (trades_df['regime_bear'] == False) & (trades_df['regime_vol_high'] == True))
    regime_stats("Bull + low vol",              (trades_df['regime_bear'] == False) & (trades_df['regime_vol_high'] == False))

    print(f"\n  Wyckoff vs Regime — overlap analysis")
    print(f"  {'─'*60}")
    regime_stats("In phase  + high vol",        (trades_df['in_phase'] == True)  & (trades_df['regime_vol_high'] == True))
    regime_stats("In phase  + low vol",         (trades_df['in_phase'] == True)  & (trades_df['regime_vol_high'] == False))
    regime_stats("In phase  + downtrend 50d",   (trades_df['in_phase'] == True)  & (trades_df['regime_downtrend_50'] == True))
    regime_stats("In phase  + uptrend 50d",     (trades_df['in_phase'] == True)  & (trades_df['regime_downtrend_50'] == False))
    regime_stats("Out phase + high vol",        (trades_df['in_phase'] == False) & (trades_df['regime_vol_high'] == True))
    regime_stats("Out phase + downtrend 50d",   (trades_df['in_phase'] == False) & (trades_df['regime_downtrend_50'] == True))


# ── PLOT — PHASE MODE ─────────────────────────────────────────────────────────
def plot_results_phase(trades_df, balance_curve_df, daily, stats, cfg, asset_name):
    OUTPUT_PNG = f"distribution_short_results_{asset_name}_phase.png"
    phases     = cfg["distribution_phases"]
    n_phases   = len(phases)

    fig = plt.figure(figsize=(26, 22), facecolor='#0d1117')
    gs  = fig.add_gridspec(4, 4, hspace=0.52, wspace=0.32,
                            height_ratios=[1.6, 1.6, 1.2, 1.2])
    axes_price = [fig.add_subplot(gs[0, i]) for i in range(4)] + \
                 [fig.add_subplot(gs[1, i]) for i in range(3)]
    ax_equity = fig.add_subplot(gs[2, 0])
    ax_r      = fig.add_subplot(gs[2, 1])
    ax_exit   = fig.add_subplot(gs[2, 2])
    ax_phase  = fig.add_subplot(gs[3, 0])
    ax_dd     = fig.add_subplot(gs[3, 1])
    ax_txt    = fig.add_subplot(gs[3, 2])

    _style_axes(fig)

    for pi, ((phase_name, start, end), ax) in enumerate(zip(phases, axes_price[:n_phases])):
        _draw_candles(ax, daily.loc[start:end])
        p_trades = trades_df[trades_df['phase'] == phase_name] if not trades_df.empty else pd.DataFrame()
        _draw_signals_trades(ax, daily.loc[start:end], p_trades)
        _label_phase_ax(ax, phase_name, start, end, daily.loc[start:end], pi)

    for ax in axes_price[n_phases:]:
        ax.set_visible(False)

    _plot_equity(ax_equity, balance_curve_df)
    _plot_r_bars(ax_r, trades_df)
    _plot_exit_pie(ax_exit, trades_df)
    _plot_phase_pnl(ax_phase, trades_df, phases)
    _plot_drawdown(ax_dd, balance_curve_df)
    _plot_summary_text(ax_txt, trades_df, stats, cfg, asset_name, mode='phase')

    fig.suptitle(
        f'{asset_name} — Distribution Phase Short  [PHASE MODE]\n'
        f'Entry: next open | Stop: +{STOP_ATR_BUFFER}×ATR | Target: {R_MULTIPLE}R | Time: Day {TIME_EXIT_DAYS}',
        fontsize=11, color='#c9d1d9', fontweight='bold', y=1.01)

    plt.savefig(OUTPUT_PNG, dpi=150, bbox_inches='tight', facecolor='#0d1117')
    print(f"\nChart saved → {OUTPUT_PNG}")
    plt.show()


# ── PLOT — SWEEP MODE ─────────────────────────────────────────────────────────
def plot_results_sweep(trades_df, balance_curve_df, daily, stats, cfg, asset_name):
    OUTPUT_PNG = f"distribution_short_results_{asset_name}_sweep.png"

    fig = plt.figure(figsize=(22, 14), facecolor='#0d1117')
    gs  = fig.add_gridspec(2, 4, hspace=0.52, wspace=0.32)

    ax_equity = fig.add_subplot(gs[0, 0])
    ax_rwr    = fig.add_subplot(gs[0, 1])   # rolling WR
    ax_inout  = fig.add_subplot(gs[0, 2])   # in vs out WR bar
    ax_phase  = fig.add_subplot(gs[0, 3])   # per-phase WR
    ax_dd     = fig.add_subplot(gs[1, 0])
    ax_txt    = fig.add_subplot(gs[1, 1:])

    _style_axes(fig)

    # ── Equity curve ─────────────────────────────────────────────────────────
    _plot_equity(ax_equity, balance_curve_df)

    # ── Rolling 20-trade WR ───────────────────────────────────────────────────
    if not trades_df.empty and len(trades_df) >= 5:
        tdf = trades_df.copy()
        tdf['win']        = (tdf['pnl_dollars'] > 0).astype(float)
        tdf['rolling_wr'] = tdf['win'].rolling(min(20, len(tdf))).mean() * 100

        # Colour line by in/out phase
        x   = np.arange(len(tdf))
        rwr = tdf['rolling_wr'].values
        ax_rwr.plot(x, rwr, color='#8b949e', lw=1, zorder=2)
        ax_rwr.fill_between(x, rwr, 50, where=rwr >= 50, alpha=0.2, color='#26a641')
        ax_rwr.fill_between(x, rwr, 50, where=rwr < 50,  alpha=0.2, color='#f85149')

        # Shade in-phase regions on rolling WR chart
        in_phase_trade = tdf['in_phase'].values
        for j in range(len(tdf)):
            if in_phase_trade[j]:
                ax_rwr.axvspan(j - 0.5, j + 0.5, alpha=0.15,
                               color='#f0e040', zorder=1)

        ax_rwr.axhline(50, color='#30363d', lw=0.8, linestyle='--')
        ax_rwr.set_title('Rolling 20-trade WR\n(yellow shading = in predefined phase)', fontsize=8)
        ax_rwr.set_xlabel('Trade #', fontsize=8)
        ax_rwr.set_ylabel('Win Rate %', fontsize=8)
        ax_rwr.set_ylim(0, 100)

    # ── In-phase vs outside WR comparison bar ────────────────────────────────
    if not trades_df.empty:
        labels_io, wrs_io, ns_io, colors_io = [], [], [], []
        for label, subset, col in [
            ("Inside\nphases",  trades_df[trades_df['in_phase'] == True],  '#f0e040'),
            ("Outside\nphases", trades_df[trades_df['in_phase'] == False], '#58a6ff'),
        ]:
            if len(subset) == 0:
                continue
            wr = len(subset[subset['pnl_dollars'] > 0]) / len(subset) * 100
            labels_io.append(label)
            wrs_io.append(wr)
            ns_io.append(len(subset))
            colors_io.append(col)

        bars = ax_inout.bar(labels_io, wrs_io, color=colors_io, alpha=0.85, width=0.4)
        ax_inout.axhline(50, color='#30363d', lw=0.8, linestyle='--', alpha=0.7)
        for bar, n, wr in zip(bars, ns_io, wrs_io):
            ax_inout.text(bar.get_x() + bar.get_width() / 2,
                          bar.get_height() + 1,
                          f'WR={wr:.1f}%\nn={n}',
                          ha='center', va='bottom', fontsize=8,
                          color='#c9d1d9', fontweight='bold')
        ax_inout.set_title('Win Rate: Inside vs Outside\nPredefined Phase Windows', fontsize=9)
        ax_inout.set_ylabel('Win Rate %', fontsize=8)
        ax_inout.set_ylim(0, 105)

    # ── Per-phase WR bar ──────────────────────────────────────────────────────
    if not trades_df.empty:
        p_names, p_wrs, p_ns = [], [], []
        for pname, _, _ in cfg["distribution_phases"]:
            pt = trades_df[trades_df['phase'] == pname]
            if len(pt) == 0:
                continue
            p_names.append(pname.replace(' ', '\n'))
            p_wrs.append(len(pt[pt['pnl_dollars'] > 0]) / len(pt) * 100)
            p_ns.append(len(pt))

        if p_names:
            x = np.arange(len(p_names))
            ax_phase.bar(x, p_wrs,
                         color=['#26a641' if w >= 50 else '#f85149' for w in p_wrs],
                         alpha=0.85)
            ax_phase.axhline(50, color='#30363d', lw=0.8, linestyle='--', alpha=0.7)
            ax_phase.set_xticks(x)
            ax_phase.set_xticklabels(p_names, fontsize=6)
            ax_phase.set_title('WR per Predefined Phase Window', fontsize=9)
            ax_phase.set_ylabel('Win Rate %', fontsize=8)
            ax_phase.set_ylim(0, 105)
            for xi, (wr, n) in enumerate(zip(p_wrs, p_ns)):
                ax_phase.text(xi, wr + 1, f'n={n}',
                             ha='center', va='bottom', fontsize=7, color='#c9d1d9')

    _plot_drawdown(ax_dd, balance_curve_df)
    _plot_summary_text(ax_txt, trades_df, stats, cfg, asset_name, mode='sweep')

    fig.suptitle(
        f'{asset_name} — Distribution Phase Short  [SWEEP MODE]\n'
        f'All signals across full history | yellow shading = predefined phase windows',
        fontsize=11, color='#c9d1d9', fontweight='bold', y=1.01)

    plt.savefig(OUTPUT_PNG, dpi=150, bbox_inches='tight', facecolor='#0d1117')
    print(f"\nChart saved → {OUTPUT_PNG}")
    plt.show()


# ── SHARED PLOT HELPERS ───────────────────────────────────────────────────────
def _style_axes(fig):
    for ax in fig.get_axes():
        ax.set_facecolor('#0d1117')
        ax.tick_params(colors='#c9d1d9', labelsize=7.5)
        ax.spines[:].set_color('#30363d')
        ax.yaxis.label.set_color('#c9d1d9')
        ax.xaxis.label.set_color('#c9d1d9')
        ax.title.set_color('#c9d1d9')


def _draw_candles(ax, df):
    for i, (ts, row) in enumerate(df.iterrows()):
        c = '#26a641' if row['close'] >= row['open'] else '#f85149'
        ax.plot([i, i], [row['low'], row['high']], color=c, lw=0.6, alpha=0.8)
        ax.add_patch(plt.Rectangle(
            (i - 0.35, min(row['open'], row['close'])),
            0.7, abs(row['close'] - row['open']), color=c, alpha=0.8))


def _draw_signals_trades(ax, df, p_trades):
    for i, (ts, row) in enumerate(df.iterrows()):
        if row['signal']:
            offset = row['atr'] * 0.3 if not np.isnan(row['atr']) else 5
            ax.scatter(i, row['high'] + offset, color='#f0e040',
                       marker='^', s=60, zorder=5)
    date_to_idx = {ts: i for i, (ts, _) in enumerate(df.iterrows())}
    for _, t in p_trades.iterrows():
        sd = t['signal_date']
        if sd in date_to_idx:
            ax.scatter(date_to_idx[sd] + 1, t['entry_price'],
                       color='#ff6b35', marker='v', s=70, zorder=6)
        color  = '#26a641' if t['pnl_dollars'] > 0 else '#f85149'
        marker = '★' if t['outcome'] == 'TARGET' else ('✕' if t['outcome'] == 'STOP' else '◆')
        exit_date = pd.Timestamp(t['exit_date']).normalize()
        if exit_date in date_to_idx:
            ax.annotate(marker, (date_to_idx[exit_date], t['exit_price']),
                        color=color, fontsize=10, ha='center', va='center',
                        fontweight='bold', zorder=7)


def _label_phase_ax(ax, phase_name, start, end, df, pi):
    step = max(1, len(df) // 6)
    ax.set_xticks(range(0, len(df), step))
    ax.set_xticklabels(
        [df.index[i].strftime('%b %y') for i in range(0, len(df), step)],
        rotation=30, ha='right', fontsize=6)
    ax.set_title(f"{phase_name}\n▲=signal  ▼=entry  ★/✕/◆=exit",
                 fontsize=8, fontweight='bold',
                 color=PHASE_COLORS[pi % len(PHASE_COLORS)])


def _plot_equity(ax, balance_curve_df):
    if balance_curve_df.empty:
        return
    bal = balance_curve_df['balance'].values
    ax.plot(range(len(balance_curve_df)), bal, color='#58a6ff', lw=2)
    ax.axhline(STARTING_BALANCE, color='#30363d', lw=0.8, linestyle='--', alpha=0.7)
    ax.fill_between(range(len(balance_curve_df)), bal, STARTING_BALANCE,
                    where=bal >= STARTING_BALANCE, alpha=0.12, color='#26a641')
    ax.fill_between(range(len(balance_curve_df)), bal, STARTING_BALANCE,
                    where=bal < STARTING_BALANCE, alpha=0.12, color='#f85149')
    ax.set_title('Equity Curve', fontsize=9)
    ax.set_xlabel('Trade #', fontsize=8)
    ax.set_ylabel('Balance ($)', fontsize=8)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f'${x:,.0f}'))


def _plot_r_bars(ax, trades_df):
    if trades_df.empty:
        return
    colors = ['#26a641' if r > 0 else '#f85149' for r in trades_df['r_realised']]
    ax.bar(range(len(trades_df)), trades_df['r_realised'].values, color=colors, alpha=0.85)
    ax.axhline(0,          color='#30363d', lw=0.8)
    ax.axhline(R_MULTIPLE, color='#26a641', lw=0.8, linestyle='--', alpha=0.6)
    ax.axhline(-1,         color='#f85149', lw=0.8, linestyle='--', alpha=0.6)
    ax.set_title('R-Multiple Per Trade', fontsize=9)
    ax.set_xlabel('Trade #', fontsize=8)
    ax.set_ylabel('R', fontsize=8)


def _plot_exit_pie(ax, trades_df):
    if trades_df.empty:
        return
    counts     = trades_df['outcome'].value_counts()
    colors_pie = {'TARGET': '#26a641', 'STOP': '#f85149',
                  'TIME': '#f0e040', 'MARGIN_CALL': '#8b949e'}
    _, _, autotexts = ax.pie(
        counts.values,
        labels=counts.index.tolist(),
        colors=[colors_pie.get(l, '#8b949e') for l in counts.index],
        autopct='%1.0f%%', startangle=90,
        textprops={'color': '#c9d1d9', 'fontsize': 8})
    for at in autotexts:
        at.set_color('#0d1117')
        at.set_fontweight('bold')
    ax.set_title('Exit Type Breakdown', fontsize=9)


def _plot_phase_pnl(ax, trades_df, phases):
    if trades_df.empty:
        return
    phase_pnls, phase_names, phase_wrs = [], [], []
    for pn, _, _ in phases:
        pt = trades_df[trades_df['phase'] == pn]
        if len(pt) == 0:
            continue
        phase_pnls.append(pt['pnl_dollars'].sum())
        phase_wrs.append(len(pt[pt['pnl_dollars'] > 0]) / len(pt) * 100)
        phase_names.append(pn)
    if not phase_names:
        return
    x = np.arange(len(phase_names))
    ax.bar(x, phase_pnls,
           color=['#26a641' if p > 0 else '#f85149' for p in phase_pnls], alpha=0.85)
    ax.axhline(0, color='#30363d', lw=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels(phase_names, fontsize=7, rotation=15, ha='right')
    ax.set_title('P&L by Phase', fontsize=9)
    ax.set_ylabel('P&L ($)', fontsize=8)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f'${x:,.0f}'))
    for xi, (wr, pnl) in enumerate(zip(phase_wrs, phase_pnls)):
        ax.text(xi, 0, f'WR={wr:.0f}%',
               ha='center', va='bottom' if pnl >= 0 else 'top',
               fontsize=7, color='#c9d1d9', fontweight='bold')


def _plot_drawdown(ax, balance_curve_df):
    if balance_curve_df.empty:
        return
    bal    = balance_curve_df['balance'].values
    peak   = np.maximum.accumulate(bal)
    dd_pct = (peak - bal) / peak * 100
    ax.fill_between(range(len(dd_pct)), -dd_pct, 0, color='#f85149', alpha=0.5)
    ax.plot(range(len(dd_pct)), -dd_pct, color='#f85149', lw=1.2)
    ax.set_title('Drawdown (%)', fontsize=9)
    ax.set_xlabel('Trade #', fontsize=8)
    ax.set_ylabel('Drawdown %', fontsize=8)
    ax.axhline(0, color='#30363d', lw=0.8)


def _plot_summary_text(ax, trades_df, stats, cfg, asset_name, mode):
    ax.axis('off')
    if trades_df.empty:
        return
    total   = len(trades_df)
    wins    = trades_df[trades_df['pnl_dollars'] > 0]
    losses  = trades_df[trades_df['pnl_dollars'] <= 0]
    ret_pct = (stats['final_balance'] - STARTING_BALANCE) / STARTING_BALANCE * 100
    pf      = abs(wins['pnl_dollars'].sum() / losses['pnl_dollars'].sum()) \
              if len(losses) > 0 and losses['pnl_dollars'].sum() != 0 else float('inf')

    in_t  = trades_df[trades_df['in_phase'] == True]
    out_t = trades_df[trades_df['in_phase'] == False]
    in_wr  = f"{len(in_t[in_t['pnl_dollars']>0])/len(in_t)*100:.1f}%" if len(in_t) > 0 else "—"
    out_wr = f"{len(out_t[out_t['pnl_dollars']>0])/len(out_t)*100:.1f}%" if len(out_t) > 0 else "—"

    lines = [
        ('SUMMARY',                  '',                                              '#c9d1d9'),
        ('',                         '',                                              '#c9d1d9'),
        ('Asset',                    asset_name,                                      '#58a6ff'),
        ('Mode',                     mode.upper(),                                    '#58a6ff'),
        ('Starting balance',         f"${STARTING_BALANCE:,.0f}",                    '#c9d1d9'),
        ('Final balance',            f"${stats['final_balance']:,.0f}",              '#26a641' if stats['final_balance'] > STARTING_BALANCE else '#f85149'),
        ('Total return',             f"{ret_pct:+.2f}%",                             '#26a641' if ret_pct > 0 else '#f85149'),
        ('Max drawdown',             f"{stats['max_dd_pct']:.1f}%",                  '#f85149'),
        ('',                         '',                                              '#c9d1d9'),
        ('Total trades',             f"{total}",                                      '#c9d1d9'),
        ('Win rate (all)',            f"{len(wins)/total*100:.1f}%",                  '#c9d1d9'),
        ('Profit factor',            f"{pf:.2f}",                                    '#c9d1d9'),
        ('Avg R',                    f"{trades_df['r_realised'].mean():+.2f}R",      '#c9d1d9'),
        ('',                         '',                                              '#c9d1d9'),
        ('WR inside phases',         in_wr,                                           '#f0e040'),
        ('WR outside phases',        out_wr,                                          '#58a6ff'),
        ('',                         '',                                              '#c9d1d9'),
        ('Signal: ATR >= 1x',        '',                                              '#8b949e'),
        (f'Stop: +{STOP_ATR_BUFFER}xATR', '',                                        '#8b949e'),
        (f'Target: {R_MULTIPLE}R',   '',                                              '#8b949e'),
        ('⚠ Exploratory only.',      '',                                              '#8b949e'),
    ]

    for i, (label, val, color) in enumerate(lines):
        ax.text(0.02, 1 - i * 0.050, label,
               transform=ax.transAxes, fontsize=8,
               color='#8b949e', va='top', fontfamily='monospace')
        if val:
            ax.text(0.65, 1 - i * 0.050, val,
                   transform=ax.transAxes, fontsize=8,
                   color=color, va='top', fontfamily='monospace',
                   fontweight='bold')
    ax.set_title('Summary', fontsize=9)


# ── DETECT MODE ───────────────────────────────────────────────────────────────
# Rules:
#   Phase START : 2 wins within 30 calendar days
#   Phase END   : 2 consecutive losses  OR  90 days from phase start (hard cap)
#   Sizing      : flat 1 unit (same as sweep, pure signal quality assessment)
# ─────────────────────────────────────────────────────────────────────────────
DETECT_WIN_TRIGGER   = 2    # wins needed to start a phase
DETECT_TRIGGER_DAYS  = 30   # window in which wins must occur
DETECT_CONSEC_LOSSES = 2    # consecutive losses to end a phase
DETECT_MAX_DAYS      = 90   # hard cap on phase duration


def run_backtest_detect(minute_data, daily, cfg, minute_index=None):
    """
    Single-pass detect mode — no lookahead bias.

    Every signal bar is paper-traded (flat 1 unit, no money) to update
    the detection state. The outcome of that paper trade is only known
    AFTER the trade exits, so phase state is updated using only
    information available at that point in time.

    A real compounding trade is only executed when:
      (a) we are already in a detected phase, AND
      (b) the current signal bar is strictly AFTER the trigger trade
          has fully exited (i.e. after the exit_date of the 2nd trigger win)

    Phase state machine (updated after each paper trade closes):
      OUT → IN  : DETECT_WIN_TRIGGER wins within DETECT_TRIGGER_DAYS days
      IN  → OUT : DETECT_CONSEC_LOSSES consecutive losses OR DETECT_MAX_DAYS hard cap
    """
    print(f"\n{'─'*60}")
    print(f"  MODE: DETECT  —  {cfg['instrument_type'].upper()}")
    print(f"  Phase trigger  : {DETECT_WIN_TRIGGER} wins within {DETECT_TRIGGER_DAYS} days")
    print(f"  Phase exit     : {DETECT_CONSEC_LOSSES} consecutive losses OR {DETECT_MAX_DAYS}-day cap")
    print(f"  Sizing         : flat paper trade for detection | ATR compounding for real trades")
    print(f"  Sweeping {daily.index[0].date()} → {daily.index[-1].date()}")
    print(f"{'─'*60}")

    unit_label = "contracts" if cfg["instrument_type"] == "futures" else "shares"

    # ── Detection state ───────────────────────────────────────────────────────
    in_phase         = False
    phase_start_date = None
    consec_losses    = 0
    recent_wins      = []       # list of (signal_date, exit_date) for trigger window
    trigger_exit     = None     # exit_date of the trade that triggered phase start
                                # real trades only allowed after this timestamp
    detected_phases  = []       # (phase_start, phase_end, trigger_exit) for plotting

    # ── Account state ─────────────────────────────────────────────────────────
    balance       = STARTING_BALANCE
    peak_balance  = STARTING_BALANCE
    max_dd_usd    = 0.0
    max_dd_pct    = 0.0
    all_trades    = []
    balance_curve = [{'date': daily.index[0], 'balance': balance}]

    # ── Missed P&L tracking (flat 1-unit, informational only) ─────────────────
    missed_lockout_pnl  = 0.0   # in phase but still in trigger lockout window
    missed_outside_pnl  = 0.0   # outside detected phase entirely
    missed_lockout_n    = 0
    missed_outside_n    = 0

    # Single skip_until shared — paper and real trades share the same signal stream
    skip_until = None

    for i, (ts, row) in enumerate(daily.iterrows()):
        if pd.isna(row['atr']):
            continue
        if skip_until is not None and ts <= skip_until:
            continue
        if not row['signal']:
            continue

        # ── Check hard cap BEFORE processing this signal ──────────────────────
        # Only uses information known at ts (phase_start_date was set in the past)
        if in_phase and (ts - phase_start_date).days > DETECT_MAX_DAYS:
            print(f"  [PHASE END — cap]  {ts.date()}")
            detected_phases[-1] = (detected_phases[-1][0], ts, detected_phases[-1][2])
            in_phase      = False
            consec_losses = 0
            trigger_exit  = None

        # ── Paper trade — always run for detection state ───────────────────────
        paper = simulate_trade(minute_data, daily, i, row, STARTING_BALANCE, cfg, flat_sizing=True, minute_index=minute_index)
        if paper is None:
            continue

        paper_won  = paper['pnl_dollars'] > 0
        paper_exit = paper['exit_date']

        # skip_until advances past this trade so next signal doesn't overlap
        skip_until = paper_exit

        # ── Real trade — only if in phase AND after trigger has fully exited ──
        # trigger_exit is None when not in phase, so the condition naturally fails
        real_trade_allowed = (
            in_phase and
            trigger_exit is not None and
            ts > pd.Timestamp(trigger_exit)
        )

        if real_trade_allowed:
            real = simulate_trade(minute_data, daily, i, row, balance, cfg, flat_sizing=False, minute_index=minute_index)
            if real is not None:
                real['in_detected_phase'] = True
                real['balance_after']     = balance + real['pnl_dollars']
                balance = real['balance_after']
                all_trades.append(real)

                if balance > peak_balance:
                    peak_balance = balance
                dd_usd = peak_balance - balance
                dd_pct = dd_usd / peak_balance * 100
                if dd_usd > max_dd_usd:
                    max_dd_usd = dd_usd
                    max_dd_pct = dd_pct

                balance_curve.append({'date': real['exit_date'], 'balance': balance})

                status = '✓' if real['pnl_dollars'] > 0 else '✗'
                print(f"  [REAL  ] {ts.date()} → {status} {real['exit_reason']:20s} | "
                      f"entry={real['entry_price']:.2f}  R={real['r_realised']:+.2f}  "
                      f"P&L=${real['pnl_dollars']:+,.0f} ({real['units']} {unit_label}) | "
                      f"bal=${balance:,.0f}")
        else:
            status = '✓' if paper_won else '✗'
            if not in_phase:
                # Outside phase entirely
                missed_outside_pnl += paper['pnl_dollars']
                missed_outside_n   += 1
                tag = '[PAPER ] '
            else:
                # In phase but still in trigger lockout window
                missed_lockout_pnl += paper['pnl_dollars']
                missed_lockout_n   += 1
                tag = '[PAPER*]'
            print(f"  {tag} {ts.date()} → {status} {paper['exit_reason']:20s} | "
                  f"R={paper['r_realised']:+.2f}  missed P&L=${paper['pnl_dollars']:+,.0f}  (detection only)")

        # ── Update detection state AFTER trade result is known ────────────────
        # This is strictly backward-looking — we only use paper_won which is
        # the outcome of a trade that has now fully closed at paper_exit
        if in_phase:
            if paper_won:
                consec_losses = 0
            else:
                consec_losses += 1
                if consec_losses >= DETECT_CONSEC_LOSSES:
                    print(f"  [PHASE END — loss] {ts.date()}")
                    detected_phases[-1] = (detected_phases[-1][0], ts, detected_phases[-1][2])
                    in_phase      = False
                    consec_losses = 0
                    trigger_exit  = None
        else:
            if paper_won:
                recent_wins.append((ts, paper_exit))
                # Prune wins outside the trigger window
                recent_wins = [(d, ex) for d, ex in recent_wins
                               if (ts - d).days <= DETECT_TRIGGER_DAYS]
                if len(recent_wins) >= DETECT_WIN_TRIGGER:
                    in_phase         = True
                    phase_start_date = ts
                    trigger_exit     = paper_exit   # real trades allowed after this
                    consec_losses    = 0
                    recent_wins      = []
                    detected_phases.append((phase_start_date, None, trigger_exit))
                    print(f"  [PHASE START]      {ts.date()}  "
                          f"(real trades from after {pd.Timestamp(trigger_exit).date()})")

    # Close any still-open phase at end of data
    if in_phase and detected_phases and detected_phases[-1][1] is None:
        detected_phases[-1] = (detected_phases[-1][0], daily.index[-1], detected_phases[-1][2])

    print(f"\n  Detected {len(detected_phases)} phase(s)")
    for s, e, tex in detected_phases:
        print(f"    {s.date()} → {e.date() if e else 'open'}  "
              f"(real trades from {pd.Timestamp(tex).date()})")

    return (
        pd.DataFrame(all_trades),
        pd.DataFrame(balance_curve),
        {'max_dd_usd': max_dd_usd, 'max_dd_pct': max_dd_pct,
         'final_balance': balance, 'peak_balance': peak_balance,
         'missed_lockout_pnl': missed_lockout_pnl, 'missed_lockout_n': missed_lockout_n,
         'missed_outside_pnl': missed_outside_pnl, 'missed_outside_n': missed_outside_n},
        detected_phases,
    )

    balance       = STARTING_BALANCE
    peak_balance  = STARTING_BALANCE
    max_dd_usd    = 0.0
    max_dd_pct    = 0.0
    all_trades    = []
    balance_curve = [{'date': daily.index[0], 'balance': balance}]
    skip_until    = None

    for i, (ts, row) in enumerate(daily.iterrows()):
        if pd.isna(row['atr']):
            continue
        if skip_until is not None and ts <= skip_until:
            continue
        if not row['signal']:
            continue
        if not in_any_phase(ts):
            continue

        trade = simulate_trade(minute_data, daily, i, row, balance, cfg, flat_sizing=False, minute_index=minute_index)
        if trade is None:
            continue

        trade['in_detected_phase'] = True
        trade['balance_after']     = balance + trade['pnl_dollars']
        balance = trade['balance_after']
        all_trades.append(trade)

        if balance > peak_balance:
            peak_balance = balance
        dd_usd = peak_balance - balance
        dd_pct = dd_usd / peak_balance * 100
        if dd_usd > max_dd_usd:
            max_dd_usd = dd_usd
            max_dd_pct = dd_pct

        balance_curve.append({'date': trade['exit_date'], 'balance': balance})
        skip_until = trade['exit_date']

        status = '✓' if trade['pnl_dollars'] > 0 else '✗'
        print(f"  [IN PHASE] {ts.date()}  → {status} {trade['exit_reason']:20s} | "
              f"entry={trade['entry_price']:.2f}  R={trade['r_realised']:+.2f}  "
              f"P&L=${trade['pnl_dollars']:+,.0f} ({trade['units']} {unit_label}) | "
              f"bal=${balance:,.0f}")

    return (
        pd.DataFrame(all_trades),
        pd.DataFrame(balance_curve),
        {'max_dd_usd': max_dd_usd, 'max_dd_pct': max_dd_pct,
         'final_balance': balance, 'peak_balance': peak_balance,
         'missed_lockout_pnl': missed_lockout_pnl, 'missed_lockout_n': missed_lockout_n,
         'missed_outside_pnl': missed_outside_pnl, 'missed_outside_n': missed_outside_n},
        detected_phases,
    )


# ── PERFORMANCE SUMMARY — DETECT MODE ────────────────────────────────────────
def print_summary_detect(trades_df, stats, detected_phases):
    print(f"\n{'='*60}  DETECT MODE SUMMARY  {'='*60}")
    if trades_df.empty:
        print("  No trades executed.")
        return

    total   = len(trades_df)
    wins    = trades_df[trades_df['pnl_dollars'] > 0]
    losses  = trades_df[trades_df['pnl_dollars'] <= 0]
    ret_pct = (stats['final_balance'] - STARTING_BALANCE) / STARTING_BALANCE * 100
    pf      = abs(wins['pnl_dollars'].sum() / losses['pnl_dollars'].sum()) \
              if len(losses) > 0 and losses['pnl_dollars'].sum() != 0 else float('inf')

    print(f"\n  Account (flat 1-unit sizing)")
    print(f"    Starting : ${STARTING_BALANCE:,.0f}   Final : ${stats['final_balance']:,.0f}   "
          f"Return : {ret_pct:+.2f}%   Max DD : ${stats['max_dd_usd']:,.0f} ({stats['max_dd_pct']:.1f}%)")
    print(f"\n  Overall — {total} trades  WR={len(wins)/total*100:.1f}%  "
          f"PF={pf:.2f}  Avg R={trades_df['r_realised'].mean():+.2f}")

    print(f"\n  IN DETECTED PHASE vs OUTSIDE")
    print(f"  {'─'*50}")
    for label, subset in [
        ("Inside detected phases",  trades_df[trades_df['in_detected_phase'] == True]),
        ("Outside detected phases", trades_df[trades_df['in_detected_phase'] == False]),
    ]:
        if len(subset) == 0:
            print(f"    {label}: no trades")
            continue
        sw  = subset[subset['pnl_dollars'] > 0]
        sl  = subset[subset['pnl_dollars'] <= 0]
        spf = abs(sw['pnl_dollars'].sum() / sl['pnl_dollars'].sum()) \
              if len(sl) > 0 and sl['pnl_dollars'].sum() != 0 else float('inf')
        print(f"    {label}: {len(subset):3d} trades  "
              f"WR={len(sw)/len(subset)*100:.1f}%  "
              f"PF={spf:.2f}  "
              f"avg R={subset['r_realised'].mean():+.2f}  "
              f"P&L=${subset['pnl_dollars'].sum():+,.0f}")

    print(f"\n  MISSED P&L (flat 1-unit, informational)")
    print(f"  {'─'*50}")
    print(f"    Trigger lockout (in phase, pre-entry) : "
          f"{stats['missed_lockout_n']} trades  P&L=${stats['missed_lockout_pnl']:+,.0f}")
    print(f"    Outside detected phases               : "
          f"{stats['missed_outside_n']} trades  P&L=${stats['missed_outside_pnl']:+,.0f}")
    total_missed = stats['missed_lockout_pnl'] + stats['missed_outside_pnl']
    print(f"    Total missed                          : ${total_missed:+,.0f}")
    if total_missed < 0:
        print(f"    → Detector correctly avoided net-negative paper trades")
    else:
        print(f"    → Missed net-positive paper trades (cost of confirmation)")

    print(f"\n  Detected phases ({len(detected_phases)} total)")
    for start, end, tex in detected_phases:
        end_str = end.date() if end else "open"
        dur     = (end - start).days if end else "?"
        print(f"    {start.date()} → {end_str}  ({dur} days)  "
              f"(real entry after {pd.Timestamp(tex).date()})")


# ── PLOT — DETECT MODE ────────────────────────────────────────────────────────
def plot_results_detect(trades_df, balance_curve_df, daily, stats, cfg, asset_name, detected_phases):
    OUTPUT_PNG = f"distribution_short_results_{asset_name}_detect.png"

    fig = plt.figure(figsize=(26, 16), facecolor='#0d1117')
    gs  = fig.add_gridspec(2, 4, hspace=0.50, wspace=0.32)

    ax_price  = fig.add_subplot(gs[0, :])   # full price + detected phases
    ax_equity = fig.add_subplot(gs[1, 0])
    ax_inout  = fig.add_subplot(gs[1, 1])
    ax_dd     = fig.add_subplot(gs[1, 2])
    ax_txt    = fig.add_subplot(gs[1, 3])

    _style_axes(fig)

    # ── Full price history with detected phase shading ────────────────────────
    close     = daily['close'].values
    date_to_i = {ts: i for i, ts in enumerate(daily.index)}
    ax_price.plot(range(len(daily)), close, color='#58a6ff', lw=0.8, alpha=0.9)

    for pi, (start, end, tex) in enumerate(detected_phases):
        s_idx = date_to_i.get(start)
        # Find nearest available end date
        end_idx = None
        if end is not None:
            end_idx = date_to_i.get(end)
            if end_idx is None:
                # Find nearest date
                candidates = [d for d in daily.index if d <= end]
                if candidates:
                    end_idx = date_to_i[candidates[-1]]
        if s_idx is not None and end_idx is not None:
            ax_price.axvspan(s_idx, end_idx, alpha=0.15,
                             color=PHASE_COLORS[pi % len(PHASE_COLORS)],
                             label=f"Phase {pi+1}: {start.date()}→{end.date() if end else '?'}")

    # Mark trades
    if not trades_df.empty:
        for _, t in trades_df.iterrows():
            sd = t['signal_date']
            if sd not in date_to_i:
                continue
            xi    = date_to_i[sd]
            yi    = daily.loc[sd, 'high'] * 1.005
            color = '#26a641' if t['pnl_dollars'] > 0 else '#f85149'
            fill  = color if t['in_detected_phase'] else 'none'
            ax_price.scatter(xi, yi, color=color, facecolors=fill,
                             s=50, linewidths=1.2, zorder=5)

    step = max(1, len(daily) // 12)
    ax_price.set_xticks(range(0, len(daily), step))
    ax_price.set_xticklabels(
        [daily.index[i].strftime('%Y') for i in range(0, len(daily), step)],
        fontsize=7)
    ax_price.set_title(
        'Full Price History — shaded = detected distribution phases  '
        '● filled=in-phase  ○ open=outside  green=win  red=loss',
        fontsize=9, color='#c9d1d9')
    ax_price.legend(fontsize=6, facecolor='#161b22', edgecolor='#30363d',
                    labelcolor='#c9d1d9', ncol=5, loc='upper left')

    # ── Equity curve ─────────────────────────────────────────────────────────
    _plot_equity(ax_equity, balance_curve_df)

    # ── In vs out WR bar ─────────────────────────────────────────────────────
    if not trades_df.empty:
        labels_io, wrs_io, ns_io, colors_io = [], [], [], []
        for label, subset, col in [
            ("Inside\ndetected", trades_df[trades_df['in_detected_phase'] == True],  '#f0e040'),
            ("Outside\ndetected", trades_df[trades_df['in_detected_phase'] == False], '#58a6ff'),
        ]:
            if len(subset) == 0:
                continue
            wr = len(subset[subset['pnl_dollars'] > 0]) / len(subset) * 100
            labels_io.append(label)
            wrs_io.append(wr)
            ns_io.append(len(subset))
            colors_io.append(col)

        bars = ax_inout.bar(labels_io, wrs_io, color=colors_io, alpha=0.85, width=0.4)
        ax_inout.axhline(50, color='#30363d', lw=0.8, linestyle='--', alpha=0.7)
        for bar, n, wr in zip(bars, ns_io, wrs_io):
            ax_inout.text(bar.get_x() + bar.get_width() / 2,
                          bar.get_height() + 1,
                          f'WR={wr:.1f}%\nn={n}',
                          ha='center', va='bottom', fontsize=9,
                          color='#c9d1d9', fontweight='bold')
        ax_inout.set_title('WR: Inside vs Outside\nDetected Phases', fontsize=9)
        ax_inout.set_ylabel('Win Rate %', fontsize=8)
        ax_inout.set_ylim(0, 110)

    # ── Drawdown ──────────────────────────────────────────────────────────────
    _plot_drawdown(ax_dd, balance_curve_df)

    # ── Summary text ──────────────────────────────────────────────────────────
    ax_txt.axis('off')
    if not trades_df.empty:
        total  = len(trades_df)
        wins   = trades_df[trades_df['pnl_dollars'] > 0]
        losses = trades_df[trades_df['pnl_dollars'] <= 0]
        in_t   = trades_df[trades_df['in_detected_phase'] == True]
        out_t  = trades_df[trades_df['in_detected_phase'] == False]
        in_wr  = f"{len(in_t[in_t['pnl_dollars']>0])/len(in_t)*100:.1f}%" if len(in_t) > 0 else "—"
        out_wr = f"{len(out_t[out_t['pnl_dollars']>0])/len(out_t)*100:.1f}%" if len(out_t) > 0 else "—"
        pf     = abs(wins['pnl_dollars'].sum() / losses['pnl_dollars'].sum()) \
                 if len(losses) > 0 and losses['pnl_dollars'].sum() != 0 else float('inf')

        lines = [
            ('DETECT MODE SUMMARY',        '',                                             '#c9d1d9'),
            ('',                           '',                                             '#c9d1d9'),
            ('Trigger',                    f"{DETECT_WIN_TRIGGER} wins / {DETECT_TRIGGER_DAYS}d", '#58a6ff'),
            ('Exit',                       f"{DETECT_CONSEC_LOSSES} consec loss / {DETECT_MAX_DAYS}d cap", '#58a6ff'),
            ('Detected phases',            f"{len(detected_phases)}",                     '#f0e040'),
            ('',                           '',                                             '#c9d1d9'),
            ('Final balance',              f"${stats['final_balance']:,.0f}",             '#26a641' if stats['final_balance'] > STARTING_BALANCE else '#f85149'),
            ('Max drawdown',               f"{stats['max_dd_pct']:.1f}%",                '#f85149'),
            ('',                           '',                                             '#c9d1d9'),
            ('Total trades',               f"{total}",                                    '#c9d1d9'),
            ('Win rate (all)',             f"{len(wins)/total*100:.1f}%",                '#c9d1d9'),
            ('Profit factor',              f"{pf:.2f}",                                   '#c9d1d9'),
            ('Avg R',                      f"{trades_df['r_realised'].mean():+.2f}R",    '#c9d1d9'),
            ('',                           '',                                             '#c9d1d9'),
            ('WR inside detected',         in_wr,                                         '#f0e040'),
            ('WR outside detected',        out_wr,                                        '#58a6ff'),
            ('',                           '',                                             '#c9d1d9'),
            ('',                           '',                                             '#c9d1d9'),
            ('Missed (lockout)',            f"${stats['missed_lockout_pnl']:+,.0f}  (n={stats['missed_lockout_n']})", '#ce93d8'),
            ('Missed (outside)',            f"${stats['missed_outside_pnl']:+,.0f}  (n={stats['missed_outside_n']})", '#58a6ff'),
            ('',                           '',                                             '#c9d1d9'),
            ('Sizing: flat 1 unit',        '',                                             '#8b949e'),
            ('⚠ Exploratory only.',        '',                                             '#8b949e'),
        ]

        for i, (label, val, color) in enumerate(lines):
            ax_txt.text(0.02, 1 - i * 0.054, label,
                       transform=ax_txt.transAxes, fontsize=8,
                       color='#8b949e', va='top', fontfamily='monospace')
            if val:
                ax_txt.text(0.68, 1 - i * 0.054, val,
                           transform=ax_txt.transAxes, fontsize=8,
                           color=color, va='top', fontfamily='monospace',
                           fontweight='bold')
    ax_txt.set_title('Summary', fontsize=9)

    fig.suptitle(
        f'{asset_name} — Distribution Phase Short  [DETECT MODE]\n'
        f'Trigger: {DETECT_WIN_TRIGGER} wins/{DETECT_TRIGGER_DAYS}d  |  '
        f'Exit: {DETECT_CONSEC_LOSSES} consec losses or {DETECT_MAX_DAYS}d cap',
        fontsize=11, color='#c9d1d9', fontweight='bold', y=1.01)

    plt.savefig(OUTPUT_PNG, dpi=150, bbox_inches='tight', facecolor='#0d1117')
    print(f"\nChart saved → {OUTPUT_PNG}")
    plt.show()

# ── ROLLING WR DETECTOR ───────────────────────────────────────────────────────
# Rules:
#   Gate open  : trailing N completed paper trades have WR >= threshold
#   Gate close : trailing N completed paper trades have WR < threshold
#   Sizing     : ATR-based compounding when gate open, paper only when closed
#   No lookahead: WR is computed from already-closed paper trades only
# ─────────────────────────────────────────────────────────────────────────────
WR_DETECT_WINDOW    = 10     # trailing N trades to compute WR
WR_DETECT_THRESHOLD = 0.60   # WR must exceed this to open gate (60%)


def run_backtest_wr_detect(minute_data, daily, cfg, minute_index=None):
    """
    Single-pass rolling WR detector — no lookahead bias.

    Every signal is paper-traded (flat 1 unit) to maintain a rolling
    window of outcomes. After each paper trade closes, the trailing WR
    is recomputed. The real trade gate is open when that WR >= threshold,
    closed otherwise.

    Real trades use ATR-based sizing off a compounding balance.
    Paper trades use flat sizing and don't affect the balance.

    No lookahead: the WR used to decide whether to take trade N is computed
    from trades 1..N-1 only — the current trade's outcome is not known until
    after the decision is made.
    """
    print(f"\n{'─'*60}")
    print(f"  MODE: WR DETECT  —  {cfg['instrument_type'].upper()}")
    print(f"  Rolling window   : {WR_DETECT_WINDOW} trades")
    print(f"  WR threshold     : {WR_DETECT_THRESHOLD*100:.0f}%")
    print(f"  Sizing           : ATR compounding when gate open")
    print(f"  Sweeping {daily.index[0].date()} → {daily.index[-1].date()}")
    print(f"{'─'*60}")

    unit_label = "contracts" if cfg["instrument_type"] == "futures" else "shares"

    # Rolling window of paper trade outcomes (1=win, 0=loss)
    outcome_window  = []    # most recent N outcomes
    detected_phases = []    # (start, end) of gate-open periods for plotting
    gate_open       = False
    gate_start      = None

    # Account state
    balance       = STARTING_BALANCE
    peak_balance  = STARTING_BALANCE
    max_dd_usd    = 0.0
    max_dd_pct    = 0.0
    all_trades    = []
    balance_curve = [{'date': daily.index[0], 'balance': balance}]

    # Missed PnL tracking
    missed_gate_closed_pnl = 0.0
    missed_gate_closed_n   = 0

    skip_until = None

    for i, (ts, row) in enumerate(daily.iterrows()):
        if pd.isna(row['atr']):
            continue
        if skip_until is not None and ts <= skip_until:
            continue
        if not row['signal']:
            continue

        # ── Compute rolling WR BEFORE this trade (no lookahead) ──────────────
        # Uses only outcomes from previously completed paper trades
        if len(outcome_window) >= WR_DETECT_WINDOW:
            trailing_wr = sum(outcome_window[-WR_DETECT_WINDOW:]) / WR_DETECT_WINDOW
        else:
            trailing_wr = None   # not enough history yet — gate stays closed

        # ── Gate state update ─────────────────────────────────────────────────
        new_gate = (trailing_wr is not None) and (trailing_wr >= WR_DETECT_THRESHOLD)

        if new_gate and not gate_open:
            gate_open  = True
            gate_start = ts
            detected_phases.append([gate_start, None])
            print(f"  [GATE OPEN]  {ts.date()}  trailing WR={trailing_wr*100:.0f}%")
        elif not new_gate and gate_open:
            gate_open = False
            detected_phases[-1][1] = ts
            wr_str = f"{trailing_wr*100:.0f}%" if trailing_wr is not None else "n/a"
            print(f"  [GATE CLOSE] {ts.date()}  trailing WR={wr_str}")

        # ── Paper trade — always run to update outcome window ─────────────────
        paper = simulate_trade(minute_data, daily, i, row, STARTING_BALANCE, cfg, flat_sizing=True, minute_index=minute_index)
        if paper is None:
            continue

        paper_won  = paper['pnl_dollars'] > 0
        skip_until = paper['exit_date']

        # ── Real trade — only when gate is open ───────────────────────────────
        if gate_open:
            real = simulate_trade(minute_data, daily, i, row, balance, cfg, flat_sizing=False, minute_index=minute_index)
            if real is not None:
                real['in_detected_phase'] = True
                real['trailing_wr']       = trailing_wr
                real['balance_after']     = balance + real['pnl_dollars']
                balance = real['balance_after']
                all_trades.append(real)

                if balance > peak_balance:
                    peak_balance = balance
                dd_usd = peak_balance - balance
                dd_pct = dd_usd / peak_balance * 100
                if dd_usd > max_dd_usd:
                    max_dd_usd = dd_usd
                    max_dd_pct = dd_pct

                balance_curve.append({'date': real['exit_date'], 'balance': balance})

                status = '✓' if real['pnl_dollars'] > 0 else '✗'
                print(f"  [REAL ] {ts.date()} → {status} {real['exit_reason']:20s} | "
                      f"WR={trailing_wr*100:.0f}%  R={real['r_realised']:+.2f}  "
                      f"P&L=${real['pnl_dollars']:+,.0f} ({real['units']} {unit_label}) | "
                      f"bal=${balance:,.0f}")
        else:
            missed_gate_closed_pnl += paper['pnl_dollars']
            missed_gate_closed_n   += 1
            wr_str = f"{trailing_wr*100:.0f}%" if trailing_wr is not None else "<10 trades"
            status = '✓' if paper_won else '✗'
            print(f"  [PAPER] {ts.date()} → {status} {paper['exit_reason']:20s} | "
                  f"WR={wr_str:10s}  R={paper['r_realised']:+.2f}  "
                  f"missed=${paper['pnl_dollars']:+,.0f}  (gate closed)")

        # ── Update outcome window AFTER trade result ───────────────────────────
        outcome_window.append(1 if paper_won else 0)

    # Close any open gate
    if gate_open and detected_phases and detected_phases[-1][1] is None:
        detected_phases[-1][1] = daily.index[-1]

    # Convert to tuples
    detected_phases = [(s, e) for s, e in detected_phases]

    print(f"\n  Gate open periods: {len(detected_phases)}")
    for s, e in detected_phases:
        dur = (e - s).days if e else "?"
        print(f"    {s.date()} → {e.date() if e else 'open'}  ({dur} days)")

    return (
        pd.DataFrame(all_trades),
        pd.DataFrame(balance_curve),
        {'max_dd_usd': max_dd_usd, 'max_dd_pct': max_dd_pct,
         'final_balance': balance, 'peak_balance': peak_balance,
         'missed_gate_closed_pnl': missed_gate_closed_pnl,
         'missed_gate_closed_n': missed_gate_closed_n},
        detected_phases,
    )


# ── PERFORMANCE SUMMARY — WR DETECT MODE ─────────────────────────────────────
def print_summary_wr_detect(trades_df, stats, detected_phases):
    print(f"\n{'='*60}  WR DETECT MODE SUMMARY  {'='*60}")
    if trades_df.empty:
        print("  No trades executed.")
        return

    total   = len(trades_df)
    wins    = trades_df[trades_df['pnl_dollars'] > 0]
    losses  = trades_df[trades_df['pnl_dollars'] <= 0]
    ret_pct = (stats['final_balance'] - STARTING_BALANCE) / STARTING_BALANCE * 100
    pf      = abs(wins['pnl_dollars'].sum() / losses['pnl_dollars'].sum()) \
              if len(losses) > 0 and losses['pnl_dollars'].sum() != 0 else float('inf')

    print(f"\n  Account")
    print(f"    Starting : ${STARTING_BALANCE:,.0f}   Final : ${stats['final_balance']:,.0f}   "
          f"Return : {ret_pct:+.2f}%   Max DD : ${stats['max_dd_usd']:,.0f} ({stats['max_dd_pct']:.1f}%)")
    print(f"\n  Real trades (gate open) — {total} trades  "
          f"WR={len(wins)/total*100:.1f}%  PF={pf:.2f}  Avg R={trades_df['r_realised'].mean():+.2f}")

    print(f"\n  MISSED P&L (gate closed, flat 1 unit)")
    print(f"    Gate closed : {stats['missed_gate_closed_n']} trades  "
          f"P&L=${stats['missed_gate_closed_pnl']:+,.0f}")
    if stats['missed_gate_closed_pnl'] < 0:
        print(f"    → Gate correctly filtered out net-negative trades")
    else:
        print(f"    → Gate missed net-positive trades (cost of confirmation)")

    print(f"\n  Gate open periods: {len(detected_phases)}")
    for s, e in detected_phases:
        dur = (e - s).days if e else "?"
        print(f"    {s.date()} → {e.date() if e else 'open'}  ({dur} days)")


# ── PLOT — WR DETECT MODE ─────────────────────────────────────────────────────
def plot_results_wr_detect(trades_df, balance_curve_df, daily, stats, cfg, asset_name, detected_phases):
    OUTPUT_PNG = f"distribution_short_results_{asset_name}_wr_detect.png"

    fig = plt.figure(figsize=(26, 16), facecolor='#0d1117')
    gs  = fig.add_gridspec(2, 4, hspace=0.50, wspace=0.32)

    ax_price  = fig.add_subplot(gs[0, :])
    ax_equity = fig.add_subplot(gs[1, 0])
    ax_inout  = fig.add_subplot(gs[1, 1])
    ax_dd     = fig.add_subplot(gs[1, 2])
    ax_txt    = fig.add_subplot(gs[1, 3])

    _style_axes(fig)

    # ── Full price history with gate-open shading ─────────────────────────────
    close     = daily['close'].values
    date_to_i = {ts: i for i, ts in enumerate(daily.index)}
    ax_price.plot(range(len(daily)), close, color='#58a6ff', lw=0.8, alpha=0.9)

    for pi, (start, end) in enumerate(detected_phases):
        s_idx = date_to_i.get(start)
        e_idx = None
        if end is not None:
            e_idx = date_to_i.get(end)
            if e_idx is None:
                candidates = [d for d in daily.index if d <= end]
                if candidates:
                    e_idx = date_to_i[candidates[-1]]
        if s_idx is not None and e_idx is not None:
            ax_price.axvspan(s_idx, e_idx, alpha=0.15,
                             color=PHASE_COLORS[pi % len(PHASE_COLORS)])

    # Mark trades
    if not trades_df.empty:
        for _, t in trades_df.iterrows():
            sd = t['signal_date']
            if sd not in date_to_i:
                continue
            xi    = date_to_i[sd]
            yi    = daily.loc[sd, 'high'] * 1.005
            color = '#26a641' if t['pnl_dollars'] > 0 else '#f85149'
            ax_price.scatter(xi, yi, color=color, s=50, zorder=5)

    step = max(1, len(daily) // 12)
    ax_price.set_xticks(range(0, len(daily), step))
    ax_price.set_xticklabels(
        [daily.index[i].strftime('%Y') for i in range(0, len(daily), step)],
        fontsize=7)
    ax_price.set_title(
        f'Full Price History — shaded = gate open (trailing {WR_DETECT_WINDOW}-trade WR ≥ {WR_DETECT_THRESHOLD*100:.0f}%)  '
        f'green=win  red=loss',
        fontsize=9, color='#c9d1d9')

    # ── Equity curve ─────────────────────────────────────────────────────────
    _plot_equity(ax_equity, balance_curve_df)

    # ── Gate open vs closed WR bar ────────────────────────────────────────────
    if not trades_df.empty:
        total  = len(trades_df)
        wins   = trades_df[trades_df['pnl_dollars'] > 0]
        wr     = len(wins) / total * 100
        missed_n   = stats['missed_gate_closed_n']
        missed_pnl = stats['missed_gate_closed_pnl']
        missed_wr  = None  # we don't track individual missed outcomes

        bars = ax_inout.bar(
            ['Gate Open\n(real trades)', 'Gate Closed\n(paper, missed)'],
            [wr, 50],   # 50 as reference line placeholder
            color=['#f0e040', '#58a6ff'], alpha=0.85, width=0.4)
        ax_inout.axhline(50, color='#30363d', lw=0.8, linestyle='--', alpha=0.7)
        ax_inout.text(0, wr + 1, f'WR={wr:.1f}%\nn={total}',
                     ha='center', va='bottom', fontsize=9,
                     color='#c9d1d9', fontweight='bold')
        ax_inout.text(1, 52, f'n={missed_n}\n${missed_pnl:+,.0f}',
                     ha='center', va='bottom', fontsize=9,
                     color='#c9d1d9', fontweight='bold')
        ax_inout.set_title(f'Gate Open WR vs\nMissed Trades', fontsize=9)
        ax_inout.set_ylabel('Win Rate %', fontsize=8)
        ax_inout.set_ylim(0, 110)

    # ── Drawdown ──────────────────────────────────────────────────────────────
    _plot_drawdown(ax_dd, balance_curve_df)

    # ── Summary text ──────────────────────────────────────────────────────────
    ax_txt.axis('off')
    if not trades_df.empty:
        total   = len(trades_df)
        wins    = trades_df[trades_df['pnl_dollars'] > 0]
        losses  = trades_df[trades_df['pnl_dollars'] <= 0]
        ret_pct = (stats['final_balance'] - STARTING_BALANCE) / STARTING_BALANCE * 100
        pf      = abs(wins['pnl_dollars'].sum() / losses['pnl_dollars'].sum()) \
                  if len(losses) > 0 and losses['pnl_dollars'].sum() != 0 else float('inf')

        lines = [
            ('WR DETECT SUMMARY',          '',                                              '#c9d1d9'),
            ('',                           '',                                              '#c9d1d9'),
            ('Asset',                      asset_name,                                      '#58a6ff'),
            ('Rolling window',             f"{WR_DETECT_WINDOW} trades",                   '#58a6ff'),
            ('WR threshold',               f"{WR_DETECT_THRESHOLD*100:.0f}%",              '#58a6ff'),
            ('Gate open periods',          f"{len(detected_phases)}",                      '#f0e040'),
            ('',                           '',                                              '#c9d1d9'),
            ('Final balance',              f"${stats['final_balance']:,.0f}",              '#26a641' if stats['final_balance'] > STARTING_BALANCE else '#f85149'),
            ('Total return',               f"{ret_pct:+.2f}%",                             '#26a641' if ret_pct > 0 else '#f85149'),
            ('Max drawdown',               f"{stats['max_dd_pct']:.1f}%",                  '#f85149'),
            ('',                           '',                                              '#c9d1d9'),
            ('Real trades (gate open)',     f"{total}",                                     '#c9d1d9'),
            ('Win rate',                   f"{len(wins)/total*100:.1f}%",                  '#c9d1d9'),
            ('Profit factor',              f"{pf:.2f}",                                    '#c9d1d9'),
            ('Avg R',                      f"{trades_df['r_realised'].mean():+.2f}R",      '#c9d1d9'),
            ('',                           '',                                              '#c9d1d9'),
            ('Missed (gate closed)',        f"{stats['missed_gate_closed_n']} trades  "
                                           f"${stats['missed_gate_closed_pnl']:+,.0f}",    '#58a6ff'),
            ('',                           '',                                              '#c9d1d9'),
            ('Sizing: ATR compounding',    '',                                              '#8b949e'),
            ('⚠ Exploratory only.',        '',                                              '#8b949e'),
        ]

        for i, (label, val, color) in enumerate(lines):
            ax_txt.text(0.02, 1 - i * 0.052, label,
                       transform=ax_txt.transAxes, fontsize=8,
                       color='#8b949e', va='top', fontfamily='monospace')
            if val:
                ax_txt.text(0.65, 1 - i * 0.052, val,
                           transform=ax_txt.transAxes, fontsize=8,
                           color=color, va='top', fontfamily='monospace',
                           fontweight='bold')
    ax_txt.set_title('Summary', fontsize=9)

    fig.suptitle(
        f'{asset_name} — Distribution Phase Short  [WR DETECT MODE]\n'
        f'Rolling {WR_DETECT_WINDOW}-trade WR ≥ {WR_DETECT_THRESHOLD*100:.0f}% gates real trades | '
        f'ATR-based compounding sizing',
        fontsize=11, color='#c9d1d9', fontweight='bold', y=1.01)

    plt.savefig(OUTPUT_PNG, dpi=150, bbox_inches='tight', facecolor='#0d1117')
    print(f"\nChart saved → {OUTPUT_PNG}")
    plt.show()

# ── CSV EXPORT ───────────────────────────────────────────────────────────────
def export_trades_csv(trades_df, asset_name, mode):
    if trades_df.empty:
        print("  No trades to export.")
        return

    output_path = f"trades_{asset_name}_{mode}.csv"

    # Select and order columns cleanly
    base_cols = [
        'phase', 'signal_date', 'entry_date', 'exit_date',
        'entry_price', 'stop', 'target', 'exit_price',
        'atr', 'atr_multiple', 'dist_50d_pct', 'dist_200d_pct', 'vol_ratio',
        'risk_points', 'units', 'outcome', 'exit_reason',
        'pnl_points', 'pnl_dollars', 'r_realised',
        'balance_before',
    ]

    # Regime cols — only present in sweep mode
    regime_cols = [
        'in_phase', 'regime_vol_high', 'regime_downtrend_50',
        'regime_downtrend_200', 'regime_bear',
    ]

    # Detect/WR detect cols
    detect_cols = ['in_detected_phase', 'trailing_wr']

    export_cols = base_cols.copy()
    for col in regime_cols + detect_cols:
        if col in trades_df.columns:
            export_cols.append(col)

    # Only keep cols that exist
    export_cols = [c for c in export_cols if c in trades_df.columns]

    df = trades_df[export_cols].copy()

    # Format dates
    for col in ['signal_date', 'entry_date', 'exit_date']:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col]).dt.strftime('%Y-%m-%d')

    df.to_csv(output_path, index=False)
    print(f"  Trades exported → {output_path}  ({len(df)} rows)")


# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    available = list(ASSET_CONFIGS.keys())

    print(f"\nAvailable assets: {', '.join(available)}")
    asset_name = input("Select asset: ").strip().upper()
    while asset_name not in ASSET_CONFIGS:
        print(f"  '{asset_name}' not found. Choose from: {', '.join(available)}")
        asset_name = input("Select asset: ").strip().upper()

    print(f"\nModes: phase | sweep | detect | wr_detect")
    mode = input("Select mode: ").strip().lower()
    while mode not in ('phase', 'sweep', 'detect', 'wr_detect'):
        print("  Please enter 'phase', 'sweep', 'detect', or 'wr_detect'")
        mode = input("Select mode: ").strip().lower()

    cfg = ASSET_CONFIGS[asset_name]

    if not os.path.exists(cfg["data_file"]):
        print(f"ERROR: data file not found: {cfg['data_file']}")
        sys.exit(1)

    minute_data, daily, minute_index = load_data(cfg)
    daily = build_features(daily)

    if mode == 'phase':
        trades_df, balance_curve_df, stats = run_backtest_phase(minute_data, daily, cfg, minute_index=minute_index)
        print_summary_phase(trades_df, stats, cfg)
        export_trades_csv(trades_df, asset_name, mode)
        plot_results_phase(trades_df, balance_curve_df, daily, stats, cfg, asset_name)
    elif mode == 'sweep':
        trades_df, balance_curve_df, stats = run_backtest_sweep(minute_data, daily, cfg, minute_index=minute_index)
        print_summary_sweep(trades_df, stats, cfg)
        export_trades_csv(trades_df, asset_name, mode)
        plot_results_sweep(trades_df, balance_curve_df, daily, stats, cfg, asset_name)
    elif mode == 'detect':
        trades_df, balance_curve_df, stats, detected_phases = run_backtest_detect(minute_data, daily, cfg, minute_index=minute_index)
        print_summary_detect(trades_df, stats, detected_phases)
        export_trades_csv(trades_df, asset_name, mode)
        plot_results_detect(trades_df, balance_curve_df, daily, stats, cfg, asset_name, detected_phases)
    else:
        trades_df, balance_curve_df, stats, detected_phases = run_backtest_wr_detect(minute_data, daily, cfg, minute_index=minute_index)
        print_summary_wr_detect(trades_df, stats, detected_phases)
        export_trades_csv(trades_df, asset_name, mode)
        plot_results_wr_detect(trades_df, balance_curve_df, daily, stats, cfg, asset_name, detected_phases)


if __name__ == "__main__":
    main()