# SQ60: Multi-Timeframe Swing Strategy Research

A year-long research project building and testing a systematic futures/FX
trading strategy. The core idea is an opening-range breakout gated by a
multi-timeframe swing-detection framework (SQ60). Two further hypotheses
are tested on top of it: whether conditioning on the VIX regime helps, and
whether Wyckoff distribution theory actually holds up in the data.

## The research questions

**1. Does a rule-based swing/trend framework produce a tradeable edge?**

`core/sq60_signals.py` implements SQ60, a timeframe-agnostic swing
detector. It identifies confirmed swing highs and lows, filters them into
a strict alternating high-low pattern, and looks for 4-swing
uptrend/downtrend structures. `core/backtester.py` wraps this into an
opening-range-breakout strategy: trade in the direction of the confirmed
trend at a fixed daily check time, sized by margin and risk-per-trade
constraints across futures and FX contracts.

**2. Does multi-timeframe confluence improve on the single-timeframe version?**

The same file's `WeightedConfluenceChecker` scores agreement across the
15-minute, 1-hour, and 4-hour SQ60 trends into a single confluence score.
That score scales position size: full size only when all three timeframes
agree, scaled down or blocked when they conflict. `core/portfolio_backtester.py`
runs this across a multi-instrument, multi-currency portfolio
(`ChronologicalPortfolioBacktester`), with proper FX conversion so P&L
aggregates correctly in one account currency.

**3. Does conditioning on the VIX regime help?**

`research/vix_filter.py` tests this with 12 competing hypotheses for what
"high vol" should mean, rather than one arbitrary threshold: a fixed
level, above or below a rolling SMA, rising momentum, a spike-then-revert
pattern, persistence over N days, percentile rank, and several more
targeted at specific regimes like "VIX elevated but compressing from a
peak." Every hypothesis applies a strict 1-day lag, so there is no
lookahead bias. `research/vix_filter_tester.py` scores each hypothesis
against a date range without needing a full backtest run.

**4. Does Wyckoff distribution theory hold up?**

`research/utad_strategy.py` implements a Wyckoff-style "Upthrust After
Distribution" short: a climactic up-day (close more than 1x its 14-day
ATR) shorted at the next open, with an ATR-based stop/target and a 3-day
time exit. It runs in two modes and compares them. `phase` restricts
trades to historically labeled distribution windows (Q4 2018, COVID 2020,
etc., defined per asset in `core/asset_configs.py`). `sweep` fires on
every valid signal across the full price history. Comparing the win rate
inside vs. outside those windows is the actual test of the theory.

## Repository structure

```
core/
  data_loader.py          - loads and resamples minute-bar CSV data
  contracts.py             - futures/FX contract specs (margin, tick size,
                             trading hours) for 15 instruments
  asset_configs.py         - equity configs + labeled distribution-phase
                             windows, used by the UTAD research
  sq60_signals.py          - SQ60Detector: the core swing/trend engine
  backtester.py            - single-instrument strategy + multi-timeframe
                             confluence scoring
  portfolio_backtester.py  - multi-instrument, multi-currency portfolio
                             backtester
entry_points/
  run_backtest.py          - interactive CLI: pick a contract, run a
                             single-instrument backtest
research/
  vix_filter.py            - VIX regime gate, 12 hypotheses
  vix_filter_tester.py     - scores a VIX hypothesis against a date range
  utad_strategy.py         - Wyckoff distribution-phase short strategy,
                             phase vs. sweep comparison
```

## Running it

Single-instrument backtest:

```bash
python entry_points/run_backtest.py
```

Prompts for a contract symbol, date range, timeframe, and whether to
enable multi-timeframe confluence, then prints results and saves a
balance chart and an interactive candlestick chart.

Portfolio backtest (runs standalone):

```bash
python core/portfolio_backtester.py
```

Edit `run_chronological_portfolio()`'s arguments, or import and call it
directly, to change the instrument list, date range, or starting balance.

VIX/UTAD research scripts are run directly and take their parameters at
the top of each file, or through the `VIXFilter(...)` and phase/sweep
mode arguments described in their docstrings.

## Setup

```bash
pip install pandas numpy matplotlib plotly seaborn
```

Data files (minute-bar CSVs per instrument) are not included in this
repository. Point `data_file` in `contracts.py` / `asset_configs.py` at
your own local data.
