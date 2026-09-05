"""
VIX FILTER TESTER
==================
Test VIX filter hypotheses against a date range WITHOUT running the
full backtester. Cross-reference against existing trade history to
score filter quality instantly.

Active hypotheses:
    threshold        - Classic VIX >= N baseline
    sma              - VIX above its own N-day SMA
    rising_floor     - VIX rising over N days AND above a floor
    vol_contraction  - Block when VIX dropped >X% from recent high
    sma_revert       - Block when VIX converging back toward SMA
    weekly_revert    - Block when N consecutive red weekly candles above SMA
    weekly_momentum  - Trade when VIX > SMA AND expanding vs 5 days ago

Functions:
    test_vix_filter()        - Full breakdown for one hypothesis
    sweep_hypotheses()       - Compare all hypotheses side by side
    plot_vix_filter()        - VIX chart with green/red shading
    score_filter_on_trades() - Score filter against existing trades CSV
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.patches as mpatches
from vix_filter import VIXFilter


# ─────────────────────────────────────────────────────────────────────────────
# Core tester
# ─────────────────────────────────────────────────────────────────────────────

def test_vix_filter(
    vix_file:         str,
    start_date:       str,
    end_date:         str,
    hypothesis:       str   = 'weekly_momentum',
    mode:             str   = 'gte',
    # threshold
    threshold:        float = 20.0,
    # sma / sma_revert / weekly_momentum / weekly_revert
    sma_period:       int   = 30,
    # rising_floor
    roc_period:       int   = 10,
    floor:            float = 15.0,
    # vol_contraction
    lookback:         int   = 20,
    max_drop_pct:     float = 15.0,
    # sma_revert
    gap_lookback:     int   = 3,
    elevation_window: int   = 10,
    # weekly_revert
    consec_down:      int   = 3,
    near_sma_buffer:  float = 5.0,
    # weekly_momentum
    roc_days:         int   = 5,
    consec_confirm:   int   = 1,
    verbose:          bool  = True,
) -> dict:
    """
    Test a VIX filter hypothesis over a specific date range.
    Returns a result dict usable by plot_vix_filter() and score_filter_on_trades().
    """
    vix = VIXFilter(
        vix_file=vix_file, hypothesis=hypothesis, mode=mode,
        threshold=threshold, sma_period=sma_period, roc_period=roc_period,
        floor=floor, lookback=lookback, max_drop_pct=max_drop_pct,
        gap_lookback=gap_lookback, elevation_window=elevation_window,
        consec_down=consec_down, near_sma_buffer=near_sma_buffer,
        roc_days=roc_days, consec_confirm=consec_confirm,
    )

    if vix.daily_vix is None:
        print("WARNING: VIX data failed to load.")
        return {}

    start = pd.Timestamp(start_date)
    end   = pd.Timestamp(end_date)

    range_series = vix.daily_vix[
        (vix.daily_vix.index >= start) &
        (vix.daily_vix.index <= end)
    ]
    range_series = range_series[range_series.index.dayofweek < 5]

    if len(range_series) == 0:
        print(f"WARNING: No VIX data found between {start_date} and {end_date}")
        return {}

    rows = []
    for ts, vix_val in range_series.items():
        d = ts.date()
        rows.append({
            'date':      d,
            'vix':       round(vix_val, 2),
            'regime':    vix.get_regime(ts),
            'tradeable': vix.is_tradeable(d),
        })

    df            = pd.DataFrame(rows)
    total         = len(df)
    tradeable     = int(df['tradeable'].sum())
    blocked       = total - tradeable
    direction     = '>=' if mode == 'gte' else '<='
    regime_counts = df['regime'].value_counts()
    regime_order  = ['LOW', 'MID', 'HIGH', 'FEAR']

    df['month'] = pd.to_datetime(df['date']).dt.to_period('M')
    monthly = df.groupby('month').agg(
        total     = ('tradeable', 'count'),
        tradeable = ('tradeable', 'sum'),
        avg_vix   = ('vix', 'mean'),
        min_vix   = ('vix', 'min'),
        max_vix   = ('vix', 'max'),
    )
    monthly['blocked']       = monthly['total'] - monthly['tradeable']
    monthly['pass_rate_pct'] = (monthly['tradeable'] / monthly['total'] * 100).round(1)

    if verbose:
        _print_results(
            df=df, total=total, tradeable=tradeable, blocked=blocked,
            direction=direction, start_date=start_date, end_date=end_date,
            hypothesis=hypothesis, mode=mode, threshold=threshold,
            sma_period=sma_period, roc_period=roc_period, floor=floor,
            lookback=lookback, max_drop_pct=max_drop_pct,
            gap_lookback=gap_lookback, elevation_window=elevation_window,
            consec_down=consec_down, near_sma_buffer=near_sma_buffer,
            roc_days=roc_days, consec_confirm=consec_confirm,
            monthly=monthly, regime_counts=regime_counts, regime_order=regime_order,
        )

    return {
        'vix_filter':     vix,
        'start_date':     start_date,
        'end_date':       end_date,
        'hypothesis':     hypothesis,
        'mode':           mode,
        'total_days':     total,
        'tradeable_days': tradeable,
        'blocked_days':   blocked,
        'pass_rate_pct':  round(tradeable / total * 100, 1) if total > 0 else 0.0,
        'avg_vix':        round(float(range_series.mean()), 2),
        'min_vix':        round(float(range_series.min()),  2),
        'max_vix':        round(float(range_series.max()),  2),
        'regime_counts':  regime_counts.to_dict(),
        'monthly_df':     monthly,
        'daily_df':       df,
    }


def _print_results(
    df, total, tradeable, blocked, direction,
    start_date, end_date, hypothesis, mode,
    threshold, sma_period, roc_period, floor,
    lookback, max_drop_pct, gap_lookback, elevation_window,
    consec_down, near_sma_buffer, roc_days, consec_confirm,
    monthly, regime_counts, regime_order,
):
    print("\n" + "=" * 70)
    print("  VIX FILTER TESTER")
    print("=" * 70)
    print(f"  Date range : {start_date}  ->  {end_date}")
    print(f"  Hypothesis : {hypothesis}")

    h = hypothesis
    if h == 'threshold':
        print(f"  Params     : VIX {direction} {threshold}")
    elif h == 'sma':
        d = 'above' if mode == 'gte' else 'below'
        print(f"  Params     : VIX {d} its {sma_period}-day SMA")
    elif h == 'rising_floor':
        print(f"  Params     : VIX rising over {roc_period} days AND VIX >= {floor}")
    elif h == 'vol_contraction':
        print(f"  Params     : VIX within {max_drop_pct}% of its {lookback}-day high  AND  VIX >= {floor}")
        print(f"  Logic      : blocks when vol is compressing from a peak")
    elif h == 'sma_revert':
        print(f"  Params     : sma={sma_period}  gap_lookback={gap_lookback}  elevation_window={elevation_window}")
        print(f"  Logic      : blocks when VIX gap vs SMA is shrinking (momentum fading)")
    elif h == 'weekly_revert':
        print(f"  Params     : sma={sma_period}  consec_down={consec_down}  near_sma_buffer={near_sma_buffer}")
        print(f"  Logic      : blocks days within {consec_down}+ consecutive red weekly candles above SMA")
    elif h == 'weekly_momentum':
        print(f"  Params     : sma={sma_period}  roc_days={roc_days}  consec_confirm={consec_confirm}")
        print(f"  Logic      : trade when VIX > {sma_period}-day SMA AND expanding vs {roc_days} days ago")
        print(f"  Blocks     : (1) VIX below SMA  (2) VIX above SMA but decreasing")

    print("=" * 70)

    pass_pct    = tradeable / total * 100 if total > 0 else 0
    blocked_pct = blocked   / total * 100 if total > 0 else 0

    print(f"\n  {'Total days in range':<28}: {total:>5}")
    print(f"  {'Tradeable (pass filter)':<28}: {tradeable:>5}  ({pass_pct:.1f}%)")
    print(f"  {'Blocked':<28}: {blocked:>5}  ({blocked_pct:.1f}%)")
    print(f"\n  {'VIX avg':<28}: {df['vix'].mean():.2f}")
    print(f"  {'VIX min':<28}: {df['vix'].min():.2f}")
    print(f"  {'VIX max':<28}: {df['vix'].max():.2f}")

    print(f"\n  {'─'*66}")
    print(f"  REGIME BREAKDOWN")
    print(f"  {'─'*66}")
    regime_ranges = {'LOW': '< 15', 'MID': '15-25', 'HIGH': '25-35', 'FEAR': '>= 35'}
    for r in regime_order:
        count = regime_counts.get(r, 0)
        pct   = count / total * 100 if total > 0 else 0
        bar   = '#' * int(pct / 2)
        print(f"  {r:<10} {count:>6} {pct:>6.1f}%  {bar:<30}  VIX {regime_ranges[r]}")

    print(f"\n  {'─'*66}")
    print(f"  MONTHLY BREAKDOWN")
    print(f"  {'─'*66}")
    print(f"  {'Month':<10} {'Total':>6} {'Pass':>6} {'Block':>6} {'Pass%':>7}  {'AvgVIX':>8}  Range")
    print(f"  {'─'*66}")
    for period, row in monthly.iterrows():
        print(
            f"  {str(period):<10} "
            f"{int(row['total']):>6} "
            f"{int(row['tradeable']):>6} "
            f"{int(row['blocked']):>6} "
            f"{row['pass_rate_pct']:>6.1f}%  "
            f"{row['avg_vix']:>8.2f}  "
            f"{row['min_vix']:.1f}-{row['max_vix']:.1f}"
        )
    print("=" * 70 + "\n")


# ─────────────────────────────────────────────────────────────────────────────
# Hypothesis sweeper
# ─────────────────────────────────────────────────────────────────────────────

def sweep_hypotheses(
    vix_file:   str,
    start_date: str,
    end_date:   str,
    mode:       str = 'gte',
) -> pd.DataFrame:
    """Compare all hypotheses with a range of params side by side."""
    configs = [
        # ── Baseline threshold ───────────────────────────────────────────────
        dict(hypothesis='threshold', threshold=15.0),
        dict(hypothesis='threshold', threshold=18.0),
        dict(hypothesis='threshold', threshold=20.0),
        dict(hypothesis='threshold', threshold=25.0),
        # ── SMA ──────────────────────────────────────────────────────────────
        dict(hypothesis='sma', sma_period=20),
        dict(hypothesis='sma', sma_period=30),
        # ── Rising floor ─────────────────────────────────────────────────────
        dict(hypothesis='rising_floor', roc_period=5,  floor=15.0),
        dict(hypothesis='rising_floor', roc_period=10, floor=15.0),
        # ── Vol contraction ──────────────────────────────────────────────────
        dict(hypothesis='vol_contraction', lookback=20, max_drop_pct=10.0, floor=15.0),
        dict(hypothesis='vol_contraction', lookback=20, max_drop_pct=15.0, floor=15.0),
        dict(hypothesis='vol_contraction', lookback=30, max_drop_pct=10.0, floor=15.0),
        dict(hypothesis='vol_contraction', lookback=30, max_drop_pct=15.0, floor=15.0),
        dict(hypothesis='vol_contraction', lookback=60, max_drop_pct=15.0, floor=15.0),
        # ── Weekly momentum ──────────────────────────────────────────────────
        dict(hypothesis='weekly_momentum', sma_period=30, roc_days=5, consec_confirm=1),
        dict(hypothesis='weekly_momentum', sma_period=30, roc_days=5, consec_confirm=2),
        dict(hypothesis='weekly_momentum', sma_period=30, roc_days=5, consec_confirm=3),
        dict(hypothesis='weekly_momentum', sma_period=20, roc_days=5, consec_confirm=1),
        dict(hypothesis='weekly_momentum', sma_period=20, roc_days=5, consec_confirm=2),
        # ── Weekly revert ────────────────────────────────────────────────────
        dict(hypothesis='weekly_revert', sma_period=20, consec_down=2, near_sma_buffer=5.0),
        dict(hypothesis='weekly_revert', sma_period=20, consec_down=3, near_sma_buffer=5.0),
        dict(hypothesis='weekly_revert', sma_period=30, consec_down=3, near_sma_buffer=5.0),
        # ── SMA revert ───────────────────────────────────────────────────────
        dict(hypothesis='sma_revert', sma_period=20, gap_lookback=3,  elevation_window=10),
        dict(hypothesis='sma_revert', sma_period=20, gap_lookback=5,  elevation_window=10),
        dict(hypothesis='sma_revert', sma_period=30, gap_lookback=3,  elevation_window=10),
    ]

    direction = '>=' if mode == 'gte' else '<='
    print("\n" + "=" * 82)
    print("  VIX HYPOTHESIS SWEEP")
    print("=" * 82)
    print(f"  Date range : {start_date}  ->  {end_date}  |  mode: '{mode}'")
    print("=" * 82)
    print(f"  {'Hypothesis':<18} {'Params':<36} {'Pass':>5} {'Block':>6} {'Pass%':>7}  Bar")
    print(f"  {'─'*80}")

    rows      = []
    bar_width = 20

    for cfg in configs:
        result = test_vix_filter(
            vix_file=vix_file, start_date=start_date,
            end_date=end_date, mode=mode, verbose=False, **cfg
        )
        if not result:
            continue

        h         = cfg['hypothesis']
        tradeable = result['tradeable_days']
        blocked   = result['blocked_days']
        total     = result['total_days']
        pct       = result['pass_rate_pct']
        bar       = '#' * int(pct / (100 / bar_width))

        param_parts = []
        if 'threshold'        in cfg: param_parts.append(f"thr={cfg['threshold']}")
        if 'sma_period'       in cfg: param_parts.append(f"sma={cfg['sma_period']}")
        if 'roc_period'       in cfg: param_parts.append(f"roc={cfg['roc_period']}")
        if 'floor'            in cfg: param_parts.append(f"floor={cfg['floor']}")
        if 'lookback'         in cfg: param_parts.append(f"lb={cfg['lookback']}")
        if 'max_drop_pct'     in cfg: param_parts.append(f"drop={cfg['max_drop_pct']}%")
        if 'gap_lookback'     in cfg: param_parts.append(f"glb={cfg['gap_lookback']}")
        if 'elevation_window' in cfg: param_parts.append(f"elev={cfg['elevation_window']}")
        if 'consec_down'      in cfg: param_parts.append(f"cd={cfg['consec_down']}")
        if 'near_sma_buffer'  in cfg: param_parts.append(f"buf={cfg['near_sma_buffer']}")
        if 'roc_days'         in cfg: param_parts.append(f"roc={cfg['roc_days']}")
        if 'consec_confirm'   in cfg: param_parts.append(f"cc={cfg['consec_confirm']}")
        param_str = '  '.join(param_parts)

        print(
            f"  {h:<18} {param_str:<36} "
            f"{tradeable:>5} {blocked:>6} {pct:>6.1f}%  {bar}"
        )

        row = {'hypothesis': h, 'mode': mode, 'total_days': total,
               'tradeable_days': tradeable, 'blocked_days': blocked,
               'pass_rate_pct': pct, 'avg_vix': result['avg_vix']}
        row.update(cfg)
        rows.append(row)

    print("=" * 82 + "\n")
    return pd.DataFrame(rows)


# ─────────────────────────────────────────────────────────────────────────────
# Filter quality scorer
# ─────────────────────────────────────────────────────────────────────────────

def score_filter_on_trades(
    trades_csv:       str,
    vix_file:         str,
    hypothesis:       str   = 'weekly_momentum',
    mode:             str   = 'gte',
    threshold:        float = 20.0,
    sma_period:       int   = 30,
    roc_period:       int   = 10,
    floor:            float = 15.0,
    lookback:         int   = 20,
    max_drop_pct:     float = 15.0,
    gap_lookback:     int   = 3,
    elevation_window: int   = 10,
    consec_down:      int   = 3,
    near_sma_buffer:  float = 5.0,
    roc_days:         int   = 5,
    consec_confirm:   int   = 1,
    plot:             bool  = True,
    save_path:        str   = None,
) -> dict:
    """
    Cross-reference an existing trades CSV against a VIX filter.
    Instantly scores filter quality without re-running the backtester.
    """
    trades = pd.read_csv(trades_csv, parse_dates=['date', 'entry_time', 'exit_time'])
    if len(trades) == 0:
        print("WARNING: No trades found in CSV.")
        return {}

    vix = VIXFilter(
        vix_file=vix_file, hypothesis=hypothesis, mode=mode,
        threshold=threshold, sma_period=sma_period, roc_period=roc_period,
        floor=floor, lookback=lookback, max_drop_pct=max_drop_pct,
        gap_lookback=gap_lookback, elevation_window=elevation_window,
        consec_down=consec_down, near_sma_buffer=near_sma_buffer,
        roc_days=roc_days, consec_confirm=consec_confirm,
    )

    trades['filter_pass'] = trades['date'].apply(
        lambda d: vix.is_tradeable(pd.Timestamp(d).date())
    )
    trades['win'] = trades['pnl_eur'] > 0

    passed  = trades[trades['filter_pass']]
    blocked = trades[~trades['filter_pass']]

    def stats(df):
        if len(df) == 0:
            return dict(trades=0, wins=0, losses=0, win_rate=0,
                        total_pnl=0, avg_pnl=0, avg_win=0, avg_loss=0,
                        profit_factor=0)
        wins   = df[df['pnl_eur'] > 0]
        losses = df[df['pnl_eur'] < 0]
        pf     = (abs(wins['pnl_eur'].sum()) / abs(losses['pnl_eur'].sum())
                  if len(losses) > 0 and losses['pnl_eur'].sum() != 0
                  else float('inf'))
        return dict(
            trades=len(df), wins=len(wins), losses=len(losses),
            win_rate=len(wins)/len(df)*100,
            total_pnl=df['pnl_eur'].sum(), avg_pnl=df['pnl_eur'].mean(),
            avg_win=wins['pnl_eur'].mean() if len(wins) > 0 else 0,
            avg_loss=losses['pnl_eur'].mean() if len(losses) > 0 else 0,
            profit_factor=pf,
        )

    all_stats     = stats(trades)
    passed_stats  = stats(passed)
    blocked_stats = stats(blocked)

    quality_score = 0
    if len(passed) > 0 and len(blocked) > 0:
        wr_improvement = passed_stats['win_rate'] - blocked_stats['win_rate']
        pf_improvement = (passed_stats['profit_factor'] - blocked_stats['profit_factor']
                          if blocked_stats['profit_factor'] != float('inf') else 0)
        pnl_saved      = -blocked_stats['total_pnl']
        wr_score  = min(max(wr_improvement * 2, 0), 40)
        pf_score  = min(max(pf_improvement * 10, 0), 30)
        pnl_score = min(max(pnl_saved / abs(all_stats['total_pnl']) * 30, 0), 30) if all_stats['total_pnl'] != 0 else 0
        quality_score = int(wr_score + pf_score + pnl_score)

    _print_score_results(hypothesis, trades, passed, blocked,
                         all_stats, passed_stats, blocked_stats, quality_score)

    if plot:
        _plot_score_results(trades, passed, blocked, all_stats, passed_stats,
                            blocked_stats, quality_score, hypothesis, save_path)

    return {
        'all': all_stats, 'passed': passed_stats, 'blocked': blocked_stats,
        'quality_score': quality_score, 'trades_df': trades,
        'passed_df': passed, 'blocked_df': blocked, 'vix_filter': vix,
    }


def _print_score_results(hypothesis, trades, passed, blocked,
                          all_stats, passed_stats, blocked_stats, quality_score):
    print("\n" + "=" * 70)
    print("  FILTER QUALITY SCORER")
    print("=" * 70)
    print(f"  Hypothesis : {hypothesis}")
    print(f"  Trades     : {len(trades)} total  |  Passed: {len(passed)}  |  Blocked: {len(blocked)}")
    print()
    print(f"  {'Metric':<22} {'ALL':>10} {'PASSED':>10} {'BLOCKED':>10}")
    print(f"  {'─'*54}")

    def fmt_pf(v):
        return 'inf' if v == float('inf') else f"{v:.2f}"

    rows = [
        ('Trades',        all_stats['trades'],       passed_stats['trades'],       blocked_stats['trades']),
        ('Win Rate %',    f"{all_stats['win_rate']:.1f}%",   f"{passed_stats['win_rate']:.1f}%",   f"{blocked_stats['win_rate']:.1f}%"),
        ('Total P&L',     f"€{all_stats['total_pnl']:,.0f}", f"€{passed_stats['total_pnl']:,.0f}", f"€{blocked_stats['total_pnl']:,.0f}"),
        ('Avg P&L/trade', f"€{all_stats['avg_pnl']:,.0f}",  f"€{passed_stats['avg_pnl']:,.0f}",  f"€{blocked_stats['avg_pnl']:,.0f}"),
        ('Profit Factor', fmt_pf(all_stats['profit_factor']), fmt_pf(passed_stats['profit_factor']), fmt_pf(blocked_stats['profit_factor'])),
    ]
    for label, a, p, b in rows:
        print(f"  {label:<22} {str(a):>10} {str(p):>10} {str(b):>10}")

    wr_diff   = passed_stats['win_rate'] - blocked_stats['win_rate']
    pnl_saved = -blocked_stats['total_pnl']
    print(f"\n  {'─'*54}")
    print(f"  Win rate delta        : {wr_diff:+.1f}pp (passed vs blocked)")
    print(f"  P&L in blocked trades : €{blocked_stats['total_pnl']:,.0f}  ", end="")
    print("(filter saves these losses)" if pnl_saved > 0 else "(filter removes good trades)")

    verdict = ("EXCELLENT" if quality_score >= 70 else
               "GOOD"      if quality_score >= 50 else
               "MARGINAL"  if quality_score >= 30 else "POOR")
    print(f"\n  Quality Score : {quality_score}/100  ->  {verdict}")
    print("=" * 70 + "\n")


def _plot_score_results(trades, passed, blocked, all_stats, passed_stats,
                         blocked_stats, quality_score, hypothesis, save_path):
    fig, axes = plt.subplots(2, 3, figsize=(20, 10))
    fig.suptitle(
        f"Filter Quality — hypothesis='{hypothesis}'  |  Score: {quality_score}/100",
        fontsize=14, fontweight='bold'
    )
    colors = {'passed': '#27ae60', 'blocked': '#e74c3c', 'all': '#2c3e50'}

    # Win rate
    ax = axes[0, 0]
    vals = [all_stats['win_rate'], passed_stats['win_rate'], blocked_stats['win_rate']]
    bars = ax.bar(['All', 'Passed', 'Blocked'], vals,
                  color=[colors['all'], colors['passed'], colors['blocked']],
                  width=0.5, edgecolor='white')
    ax.axhline(50, color='gray', linestyle='--', linewidth=1, alpha=0.6)
    for bar, v in zip(bars, vals):
        ax.text(bar.get_x()+bar.get_width()/2, bar.get_height()+0.5,
                f"{v:.1f}%", ha='center', fontsize=11, fontweight='bold')
    ax.set_title('Win Rate', fontweight='bold')
    ax.set_ylim(0, 100)
    ax.grid(True, alpha=0.3, axis='y')

    # Total P&L
    ax = axes[0, 1]
    vals = [all_stats['total_pnl'], passed_stats['total_pnl'], blocked_stats['total_pnl']]
    bar_cols = [colors['all'],
                colors['passed'] if passed_stats['total_pnl'] >= 0 else colors['blocked'],
                colors['passed'] if blocked_stats['total_pnl'] < 0 else colors['blocked']]
    bars = ax.bar(['All', 'Passed', 'Blocked'], vals, color=bar_cols, width=0.5, edgecolor='white')
    ax.axhline(0, color='black', linewidth=0.8)
    for bar, v in zip(bars, vals):
        ax.text(bar.get_x()+bar.get_width()/2,
                bar.get_height() + 200 if v >= 0 else bar.get_height() - 1500,
                f"€{v:,.0f}", ha='center', fontsize=10, fontweight='bold')
    ax.set_title('Total P&L', fontweight='bold')
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"€{x:,.0f}"))
    ax.grid(True, alpha=0.3, axis='y')

    # Profit factor
    ax = axes[0, 2]
    raw_pfs = [all_stats['profit_factor'], passed_stats['profit_factor'], blocked_stats['profit_factor']]
    vals    = [min(v, 5) for v in raw_pfs]
    bars = ax.bar(['All', 'Passed', 'Blocked'], vals,
                  color=[colors['all'], colors['passed'], colors['blocked']],
                  width=0.5, edgecolor='white')
    ax.axhline(1.0, color='gray', linestyle='--', linewidth=1, alpha=0.6)
    for bar, v, rv in zip(bars, vals, raw_pfs):
        label = 'inf' if rv == float('inf') else f"{rv:.2f}"
        ax.text(bar.get_x()+bar.get_width()/2, bar.get_height()+0.05,
                label, ha='center', fontsize=11, fontweight='bold')
    ax.set_title('Profit Factor', fontweight='bold')
    ax.set_ylim(0, max(vals)*1.3+0.5)
    ax.grid(True, alpha=0.3, axis='y')

    # Avg P&L per trade
    ax = axes[1, 0]
    vals = [all_stats['avg_pnl'], passed_stats['avg_pnl'], blocked_stats['avg_pnl']]
    bar_cols2 = [colors['all'] if v >= 0 else colors['blocked'] for v in vals]
    bars = ax.bar(['All', 'Passed', 'Blocked'], vals, color=bar_cols2, width=0.5, edgecolor='white')
    ax.axhline(0, color='black', linewidth=0.8)
    for bar, v in zip(bars, vals):
        ax.text(bar.get_x()+bar.get_width()/2,
                bar.get_height()+20 if v >= 0 else bar.get_height()-80,
                f"€{v:,.0f}", ha='center', fontsize=10, fontweight='bold')
    ax.set_title('Avg P&L per Trade', fontweight='bold')
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"€{x:,.0f}"))
    ax.grid(True, alpha=0.3, axis='y')

    # Monthly P&L passed vs blocked
    ax = axes[1, 1]
    trades['month'] = pd.to_datetime(trades['date']).dt.to_period('M')
    mp = passed.groupby(pd.to_datetime(passed['date']).dt.to_period('M'))['pnl_eur'].sum()
    mb = blocked.groupby(pd.to_datetime(blocked['date']).dt.to_period('M'))['pnl_eur'].sum()
    all_months  = sorted(set(mp.index) | set(mb.index))
    month_dates = [m.to_timestamp() for m in all_months]
    p_vals = [mp.get(m, 0) for m in all_months]
    b_vals = [mb.get(m, 0) for m in all_months]
    ax.bar([d - pd.Timedelta(days=7) for d in month_dates], p_vals,
           width=12, color=colors['passed'], alpha=0.8, label='Passed')
    ax.bar([d + pd.Timedelta(days=7) for d in month_dates], b_vals,
           width=12, color=colors['blocked'], alpha=0.8, label='Blocked')
    ax.axhline(0, color='black', linewidth=0.8)
    ax.set_title('Monthly P&L: Passed vs Blocked', fontweight='bold')
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"€{x:,.0f}"))
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha='right', fontsize=8)
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3, axis='y')

    # Per-instrument win rate
    ax = axes[1, 2]
    instruments = sorted(trades['instrument'].unique())
    x     = np.arange(len(instruments))
    width = 0.35
    p_wr  = [passed[passed['instrument']==i]['win'].mean()*100
             if len(passed[passed['instrument']==i]) > 0 else 0 for i in instruments]
    b_wr  = [blocked[blocked['instrument']==i]['win'].mean()*100
             if len(blocked[blocked['instrument']==i]) > 0 else 0 for i in instruments]
    ax.bar(x-width/2, p_wr, width, color=colors['passed'], alpha=0.85, label='Passed')
    ax.bar(x+width/2, b_wr, width, color=colors['blocked'], alpha=0.85, label='Blocked')
    ax.axhline(50, color='gray', linestyle='--', linewidth=1, alpha=0.6)
    ax.set_title('Win Rate by Instrument', fontweight='bold')
    ax.set_ylabel('Win Rate %')
    ax.set_xticks(x)
    ax.set_xticklabels(instruments, rotation=45, ha='right', fontsize=9)
    ax.set_ylim(0, 100)
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3, axis='y')

    plt.tight_layout()
    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches='tight', facecolor='white')
        print(f"  Chart saved: {save_path}")
    plt.show()


# ─────────────────────────────────────────────────────────────────────────────
# VIX overlay chart
# ─────────────────────────────────────────────────────────────────────────────

def plot_vix_filter(result: dict, save_path: str = None, show: bool = True) -> None:
    """Plot VIX curve with green/red shading and signal overlays."""
    if not result:
        print("WARNING: empty result, nothing to plot.")
        return

    df         = result['daily_df'].copy()
    vix_filter = result['vix_filter']
    hypothesis = result['hypothesis']
    start_date = result['start_date']
    end_date   = result['end_date']

    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)

    fig, (ax_vix, ax_bar) = plt.subplots(
        2, 1, figsize=(18, 9),
        gridspec_kw={'height_ratios': [4, 1], 'hspace': 0.08},
        sharex=True
    )

    # Background shading
    df['group'] = (df['tradeable'] != df['tradeable'].shift()).cumsum()
    for _, grp in df.groupby('group'):
        x_start = grp['date'].iloc[0]
        x_end   = grp['date'].iloc[-1] + pd.Timedelta(days=1)
        color   = '#d4edda' if grp['tradeable'].iloc[0] else '#f8d7da'
        ax_vix.axvspan(x_start, x_end, color=color, alpha=0.5, linewidth=0)
        ax_bar.axvspan(x_start, x_end, color=color, alpha=0.5, linewidth=0)

    # VIX line
    ax_vix.plot(df['date'], df['vix'], color='#2c3e50', linewidth=1.8,
                label='VIX (lagged)', zorder=3)

    # Signal overlays + build param_parts for title
    param_parts = [f"hypothesis='{hypothesis}'"]
    raw = vix_filter._daily_vix_raw

    if raw is not None:

        if hypothesis == 'threshold':
            ax_vix.axhline(y=vix_filter.threshold, color='#c0392b', linewidth=1.2,
                           linestyle=':', label=f'Threshold ({vix_filter.threshold})')
            param_parts.append(f"threshold={vix_filter.threshold}")

        elif hypothesis == 'sma':
            sma = raw.rolling(vix_filter.sma_period).mean()
            sma_df = _to_date_df(sma, 'sma', start_date, end_date)
            ax_vix.plot(sma_df['date'], sma_df['sma'], color='#e67e22',
                        linewidth=1.5, linestyle='--',
                        label=f'{vix_filter.sma_period}-day SMA', zorder=4)
            param_parts.append(f"sma_period={vix_filter.sma_period}")

        elif hypothesis == 'rising_floor':
            ax_vix.axhline(y=vix_filter.floor, color='#8e44ad', linewidth=1.2,
                           linestyle=':', label=f'Floor ({vix_filter.floor})')
            param_parts.append(f"roc_period={vix_filter.roc_period}")
            param_parts.append(f"floor={vix_filter.floor}")

        elif hypothesis == 'vol_contraction':
            roll_high = raw.rolling(vix_filter.lookback).max()
            rh_df = _to_date_df(roll_high, 'rolling_high', start_date, end_date)
            ax_vix.plot(rh_df['date'], rh_df['rolling_high'], color='#e74c3c',
                        linewidth=1.2, linestyle='--',
                        label=f'{vix_filter.lookback}-day rolling high', zorder=4)
            rh_df['drop_line'] = rh_df['rolling_high'] * (1 - vix_filter.max_drop_pct/100)
            ax_vix.plot(rh_df['date'], rh_df['drop_line'], color='#c0392b',
                        linewidth=1.0, linestyle=':',
                        label=f'Max drop line ({vix_filter.max_drop_pct}%)', zorder=4)
            ax_vix.axhline(y=vix_filter.floor, color='#8e44ad', linewidth=1.0,
                           linestyle=':', alpha=0.7, label=f'Floor ({vix_filter.floor})')
            param_parts += [f"lookback={vix_filter.lookback}",
                            f"max_drop={vix_filter.max_drop_pct}%",
                            f"floor={vix_filter.floor}"]

        elif hypothesis == 'sma_revert':
            sma = raw.rolling(vix_filter.sma_period).mean()
            sma_df = _to_date_df(sma, 'sma', start_date, end_date)
            ax_vix.plot(sma_df['date'], sma_df['sma'], color='#e67e22',
                        linewidth=1.8, linestyle='--',
                        label=f'{vix_filter.sma_period}-day SMA', zorder=4)
            merged = pd.merge(df[['date', 'vix']], sma_df, on='date', how='inner')
            ax_vix.fill_between(merged['date'], merged['vix'], merged['sma'],
                                where=(merged['vix'] > merged['sma']),
                                color='#e67e22', alpha=0.12, label='VIX-SMA gap')
            param_parts += [f"sma={vix_filter.sma_period}",
                            f"gap_lookback={vix_filter.gap_lookback}",
                            f"elevation_window={vix_filter.elevation_window}"]

        elif hypothesis == 'weekly_revert':
            weekly   = raw.resample('W-FRI').last().dropna()
            w_sma    = weekly.rolling(vix_filter.sma_period).mean()
            w_sma_df = _to_date_df(w_sma, 'weekly_sma', start_date, end_date)
            ax_vix.step(w_sma_df['date'], w_sma_df['weekly_sma'], color='#e67e22',
                        linewidth=2.0, linestyle='--', where='post',
                        label=f'Weekly {vix_filter.sma_period}-period SMA', zorder=4)
            w_close_df = _to_date_df(weekly, 'weekly_close', start_date, end_date)
            ax_vix.scatter(w_close_df['date'], w_close_df['weekly_close'],
                           color='#2c3e50', s=25, zorder=5, label='Weekly close')
            param_parts += [f"sma={vix_filter.sma_period}",
                            f"consec_down={vix_filter.consec_down}",
                            f"buffer={vix_filter.near_sma_buffer}"]

        elif hypothesis == 'weekly_momentum':
            sma = raw.rolling(vix_filter.sma_period).mean()
            sma_df = _to_date_df(sma, 'sma', start_date, end_date)
            ax_vix.plot(sma_df['date'], sma_df['sma'], color='#e67e22',
                        linewidth=2.0, linestyle='--',
                        label=f'{vix_filter.sma_period}-day SMA (floor)', zorder=4)
            merged = pd.merge(df[['date', 'vix']], sma_df, on='date', how='inner')
            ax_vix.fill_between(merged['date'], merged['vix'], merged['sma'],
                                where=(merged['vix'] > merged['sma']),
                                color='#e67e22', alpha=0.10, label='Above SMA zone')
            param_parts += [f"sma={vix_filter.sma_period}",
                            f"roc_days={vix_filter.roc_days}",
                            f"consec_confirm={vix_filter.consec_confirm}"]

    # Regime bands
    for y_lo, y_hi, col in [(0,15,'#eafaf1'),(15,25,'#fefefe'),
                             (25,35,'#fef9e7'),(35,80,'#fdf2f8')]:
        ax_vix.axhspan(y_lo, y_hi, color=col, alpha=0.15, linewidth=0, zorder=0)

    # Right axis regime labels
    ax_right = ax_vix.twinx()
    ax_right.set_ylim(ax_vix.get_ylim())
    ax_right.set_yticks([7.5, 20, 30, 57.5])
    ax_right.set_yticklabels(['LOW (<15)', 'MID (15-25)', 'HIGH (25-35)', 'FEAR (>35)'],
                              fontsize=8, color='#7f8c8d')
    ax_right.tick_params(right=False)

    # Monthly pass rate bar
    df['month'] = df['date'].dt.to_period('M')
    monthly = df.groupby('month').agg(total=('tradeable','count'),
                                       tradeable=('tradeable','sum'))
    monthly['pass_pct']    = monthly['tradeable'] / monthly['total'] * 100
    monthly['blocked_pct'] = 100 - monthly['pass_pct']
    month_dates = [m.to_timestamp() for m in monthly.index]
    ax_bar.bar(month_dates, monthly['pass_pct'],    width=20, color='#27ae60', alpha=0.7)
    ax_bar.bar(month_dates, monthly['blocked_pct'], width=20, color='#e74c3c', alpha=0.7,
               bottom=monthly['pass_pct'])
    ax_bar.axhline(50, color='gray', linewidth=0.8, linestyle='--', alpha=0.5)
    ax_bar.set_ylabel('Monthly\nPass %', fontsize=9)
    ax_bar.set_ylim(0, 100)
    ax_bar.set_yticks([0, 50, 100])

    # Title & legend
    total     = result['total_days']
    tradeable = result['tradeable_days']
    blocked   = result['blocked_days']
    pct       = result['pass_rate_pct']

    ax_vix.set_title(
        f"VIX Filter — {', '.join(param_parts)}\n"
        f"{start_date} -> {end_date}   |   "
        f"Tradeable: {tradeable}/{total} ({pct}%)   Blocked: {blocked} ({100-pct:.1f}%)",
        fontsize=12, fontweight='bold', pad=12
    )
    ax_vix.set_ylabel('VIX Level', fontsize=11)
    ax_vix.set_ylim(bottom=0)
    ax_vix.grid(True, alpha=0.25, linestyle='--')

    pass_patch    = mpatches.Patch(color='#d4edda', label=f'Tradeable ({tradeable} days)')
    blocked_patch = mpatches.Patch(color='#f8d7da', label=f'Blocked ({blocked} days)')
    handles, labels_leg = ax_vix.get_legend_handles_labels()
    ax_vix.legend(handles + [pass_patch, blocked_patch],
                  labels_leg + [f'Tradeable ({tradeable} days)', f'Blocked ({blocked} days)'],
                  loc='upper right', fontsize=9, framealpha=0.9)

    ax_bar.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
    ax_bar.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    plt.setp(ax_bar.xaxis.get_majorticklabels(), rotation=45, ha='right', fontsize=9)

    plt.tight_layout()
    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches='tight', facecolor='white')
        print(f"\n  Chart saved: {save_path}")
    if show:
        plt.show()
    else:
        plt.close()


def _to_date_df(series, col_name, start_date, end_date):
    """Helper: convert a Series to a filtered date DataFrame for plotting."""
    df = series.reset_index()
    df.columns = ['date', col_name]
    df['date'] = pd.to_datetime(df['date'])
    return df[(df['date'] >= pd.Timestamp(start_date)) &
              (df['date'] <= pd.Timestamp(end_date))]


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":

    # ── CONFIG — edit these ───────────────────────────────────────────────────
    VIX_FILE   = 'VX_1min.txt'
    START_DATE = '2020-01-01'
    END_DATE   = '2024-12-31'
    MODE       = 'gte'
    TRADES_CSV = f'portfolio_trades_{START_DATE}_to_{END_DATE}.csv'
    # ─────────────────────────────────────────────────────────────────────────

    # ── 1. Test one specific hypothesis ──────────────────────────────────────
    result = test_vix_filter(
        vix_file       = VIX_FILE,
        start_date     = START_DATE,
        end_date       = END_DATE,
        hypothesis     = 'weekly_momentum',
        sma_period     = 30,
        roc_days       = 5,
        consec_confirm = 1,
        mode           = MODE,
        verbose        = True,
    )

    # ── 2. Plot the filter ────────────────────────────────────────────────────
    plot_vix_filter(
        result    = result,
        save_path = f'vix_filter_{result["hypothesis"]}_{START_DATE}_{END_DATE}.png',
        show      = True,
    )

    # ── 3. Compare all hypotheses side by side ────────────────────────────────
    hypothesis_df = sweep_hypotheses(
        vix_file   = VIX_FILE,
        start_date = START_DATE,
        end_date   = END_DATE,
        mode       = MODE,
    )
    hypothesis_df.to_csv('vix_hypothesis_sweep.csv', index=False)
    print("Saved: vix_hypothesis_sweep.csv")

    # ── 4. Score filter against unfiltered trade history ──────────────────────
    score = score_filter_on_trades(
        trades_csv     = TRADES_CSV,
        vix_file       = VIX_FILE,
        hypothesis     = 'weekly_momentum',
        sma_period     = 30,
        roc_days       = 5,
        consec_confirm = 1,
        mode           = MODE,
        plot           = True,
        save_path      = f'filter_quality_weekly_momentum_{START_DATE}_{END_DATE}.png',
    )