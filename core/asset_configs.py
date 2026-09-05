"""
asset_configs.py
================
Add new assets here. The backtester engine reads this dict at runtime.

instrument_type:
    "futures" → CME-style margin (small % of notional)
    "equity"  → Reg T short margin (50% initial, 25% maintenance)

distribution_phases:
    List of (label, start_date, end_date) strings.
    Dates are inclusive and must match data availability.
"""

ASSET_CONFIGS = {
    # ── FUTURES ───────────────────────────────────────────────────────────────
    "ES": {
        "data_file"              : "ES_SP500_Mini_Futures_1min.txt",
        "instrument_type"        : "futures",
        "point_value"            : 50,
        "tick_size"              : 0.25,
        "initial_margin_pct"     : 5.0,
        "maintenance_margin_pct" : 4.0,
        "distribution_phases"    : [
            ("Aug 2015",   "2015-07-01", "2015-09-30"),
            ("Jan 2018",   "2018-01-01", "2018-02-28"),
            ("Q4 2018",    "2018-09-01", "2018-12-31"),
            ("COVID 2020", "2020-01-15", "2020-03-15"),
            ("2021-2022",  "2021-10-01", "2022-01-28"),
            ("Apr 2022",   "2022-03-01", "2022-05-31"),
            ("Late 2023",  "2023-07-01", "2023-10-31"),
        ],
    },

    # ── A ─────────────────────────────────────────────────────────────────────
    "AAPL": {
        "data_file"              : "AAPL_full_1min.txt",
        "instrument_type"        : "equity",
        "point_value"            : 1,
        "tick_size"              : 0.01,
        "initial_margin_pct"     : 50.0,
        "maintenance_margin_pct" : 25.0,
        "distribution_phases"    : [
            ("AAPL 2000 Top",  "2000-01-01", "2000-04-30"),
            ("AAPL 2007-2008", "2007-11-01", "2008-03-31"),
            ("AAPL Q4 2018",   "2018-10-01", "2018-12-31"),
            ("AAPL COVID",     "2020-01-15", "2020-03-15"),
            ("AAPL 2021-2022", "2021-11-01", "2022-03-31"),
        ],
    },

    "AMZN": {
        "data_file"              : "AMZN_full_1min.txt",
        "instrument_type"        : "equity",
        "point_value"            : 1,
        "tick_size"              : 0.01,
        "initial_margin_pct"     : 50.0,
        "maintenance_margin_pct" : 25.0,
        "distribution_phases"    : [
            ("AMZN 2000 Top",  "2000-01-01", "2000-06-30"),
            ("AMZN 2007-2008", "2007-10-01", "2008-03-31"),
            ("AMZN Q4 2018",   "2018-09-01", "2018-12-31"),
            ("AMZN COVID",     "2020-01-15", "2020-03-15"),
            ("AMZN 2021-2022", "2021-11-01", "2022-06-30"),
        ],
    },

    "ADBE": {
        "data_file"              : "ADBE_full_1min.txt",
        "instrument_type"        : "equity",
        "point_value"            : 1,
        "tick_size"              : 0.01,
        "initial_margin_pct"     : 50.0,
        "maintenance_margin_pct" : 25.0,
        "distribution_phases"    : [
            ("ADBE 2000 Top",  "2000-01-01", "2000-06-30"),
            ("ADBE 2007-2008", "2007-10-01", "2008-03-31"),
            ("ADBE Q4 2018",   "2018-09-01", "2018-12-31"),
            ("ADBE COVID",     "2020-01-15", "2020-03-15"),
            ("ADBE 2021-2022", "2021-11-01", "2022-06-30"),
        ],
    },

    "AMD": {
        "data_file"              : "AMD_full_1min.txt",
        "instrument_type"        : "equity",
        "point_value"            : 1,
        "tick_size"              : 0.01,
        "initial_margin_pct"     : 50.0,
        "maintenance_margin_pct" : 25.0,
        "distribution_phases"    : [
            ("AMD 2000 Top",  "2000-01-01", "2000-06-30"),
            ("AMD 2018 Peak", "2018-09-01", "2018-12-31"),
            ("AMD COVID",     "2020-01-15", "2020-03-15"),
            ("AMD 2021-2022", "2021-11-01", "2022-06-30"),
            ("AMD Late 2023", "2023-07-01", "2023-10-31"),
        ],
    },

    "AVGO": {
        "data_file"              : "AVGO_full_1min.txt",
        "instrument_type"        : "equity",
        "point_value"            : 1,
        "tick_size"              : 0.01,
        "initial_margin_pct"     : 50.0,
        "maintenance_margin_pct" : 25.0,
        "distribution_phases"    : [
            ("AVGO Q4 2018",   "2018-09-01", "2018-12-31"),
            ("AVGO COVID",     "2020-01-15", "2020-03-15"),
            ("AVGO 2021-2022", "2021-11-01", "2022-06-30"),
            ("AVGO Late 2023", "2023-07-01", "2023-10-31"),
        ],
    },

    "ASML": {
        "data_file"              : "ASML_full_1min.txt",
        "instrument_type"        : "equity",
        "point_value"            : 1,
        "tick_size"              : 0.01,
        "initial_margin_pct"     : 50.0,
        "maintenance_margin_pct" : 25.0,
        "distribution_phases"    : [
            ("ASML Q4 2018",   "2018-09-01", "2018-12-31"),
            ("ASML COVID",     "2020-01-15", "2020-03-15"),
            ("ASML 2021-2022", "2021-11-01", "2022-06-30"),
            ("ASML Late 2023", "2023-07-01", "2023-10-31"),
        ],
    },

    "AMAT": {
        "data_file"              : "AMAT_full_1min.txt",
        "instrument_type"        : "equity",
        "point_value"            : 1,
        "tick_size"              : 0.01,
        "initial_margin_pct"     : 50.0,
        "maintenance_margin_pct" : 25.0,
        "distribution_phases"    : [
            ("AMAT 2000 Top",  "2000-01-01", "2000-06-30"),
            ("AMAT 2007-2008", "2007-10-01", "2008-03-31"),
            ("AMAT Q4 2018",   "2018-09-01", "2018-12-31"),
            ("AMAT COVID",     "2020-01-15", "2020-03-15"),
            ("AMAT 2021-2022", "2021-11-01", "2022-06-30"),
        ],
    },

    # ── M ─────────────────────────────────────────────────────────────────────
    "MSFT": {
        "data_file"              : "MSFT_full_1min.txt",
        "instrument_type"        : "equity",
        "point_value"            : 1,
        "tick_size"              : 0.01,
        "initial_margin_pct"     : 50.0,
        "maintenance_margin_pct" : 25.0,
        "distribution_phases"    : [
            ("MSFT 2000 Top",  "2000-01-01", "2000-06-30"),
            ("MSFT 2007-2008", "2007-10-01", "2008-03-31"),
            ("MSFT Q4 2018",   "2018-09-01", "2018-12-31"),
            ("MSFT COVID",     "2020-01-15", "2020-03-15"),
            ("MSFT 2021-2022", "2021-11-01", "2022-06-30"),
        ],
    },

    "META": {
        "data_file"              : "META_full_1min.txt",
        "instrument_type"        : "equity",
        "point_value"            : 1,
        "tick_size"              : 0.01,
        "initial_margin_pct"     : 50.0,
        "maintenance_margin_pct" : 25.0,
        "distribution_phases"    : [
            ("META Q4 2018",   "2018-09-01", "2018-12-31"),
            ("META COVID",     "2020-01-15", "2020-03-15"),
            ("META 2021-2022", "2021-09-01", "2022-06-30"),
            ("META Late 2023", "2023-07-01", "2023-10-31"),
        ],
    },

    "MCD": {
        "data_file"              : "MCD_full_1min.txt",
        "instrument_type"        : "equity",
        "point_value"            : 1,
        "tick_size"              : 0.01,
        "initial_margin_pct"     : 50.0,
        "maintenance_margin_pct" : 25.0,
        "distribution_phases"    : [
            ("MCD 2007-2008",  "2007-10-01", "2008-03-31"),
            ("MCD Q4 2018",    "2018-09-01", "2018-12-31"),
            ("MCD COVID",      "2020-01-15", "2020-03-15"),
            ("MCD 2021-2022",  "2021-11-01", "2022-06-30"),
        ],
    },

    "MA": {
        "data_file"              : "MA_full_1min.txt",
        "instrument_type"        : "equity",
        "point_value"            : 1,
        "tick_size"              : 0.01,
        "initial_margin_pct"     : 50.0,
        "maintenance_margin_pct" : 25.0,
        "distribution_phases"    : [
            ("MA 2007-2008",  "2007-10-01", "2008-03-31"),
            ("MA Q4 2018",    "2018-09-01", "2018-12-31"),
            ("MA COVID",      "2020-01-15", "2020-03-15"),
            ("MA 2021-2022",  "2021-11-01", "2022-06-30"),
        ],
    },

    "MMM": {
        "data_file"              : "MMM_full_1min.txt",
        "instrument_type"        : "equity",
        "point_value"            : 1,
        "tick_size"              : 0.01,
        "initial_margin_pct"     : 50.0,
        "maintenance_margin_pct" : 25.0,
        "distribution_phases"    : [
            ("MMM 2007-2008", "2007-10-01", "2008-03-31"),
            ("MMM Q4 2018",   "2018-09-01", "2018-12-31"),
            ("MMM COVID",     "2020-01-15", "2020-03-15"),
            ("MMM 2021-2022", "2021-11-01", "2022-06-30"),
        ],
    },

    # ── N ─────────────────────────────────────────────────────────────────────
    "NVDA": {
        "data_file"              : "NVDA_full_1min.txt",
        "instrument_type"        : "equity",
        "point_value"            : 1,
        "tick_size"              : 0.01,
        "initial_margin_pct"     : 50.0,
        "maintenance_margin_pct" : 25.0,
        "distribution_phases"    : [
            ("NVDA 2000 Top",  "2000-01-01", "2000-06-30"),
            ("NVDA Q4 2018",   "2018-09-01", "2018-12-31"),
            ("NVDA COVID",     "2020-01-15", "2020-03-15"),
            ("NVDA 2021-2022", "2021-11-01", "2022-06-30"),
            ("NVDA Late 2023", "2023-07-01", "2023-10-31"),
        ],
    },

    "NFLX": {
        "data_file"              : "NFLX_full_1min.txt",
        "instrument_type"        : "equity",
        "point_value"            : 1,
        "tick_size"              : 0.01,
        "initial_margin_pct"     : 50.0,
        "maintenance_margin_pct" : 25.0,
        "distribution_phases"    : [
            ("NFLX Q4 2018",   "2018-09-01", "2018-12-31"),
            ("NFLX COVID",     "2020-01-15", "2020-03-15"),
            ("NFLX 2021-2022", "2021-11-01", "2022-06-30"),
            ("NFLX Late 2023", "2023-07-01", "2023-10-31"),
        ],
    },

    "NKE": {
        "data_file"              : "NKE_full_1min.txt",
        "instrument_type"        : "equity",
        "point_value"            : 1,
        "tick_size"              : 0.01,
        "initial_margin_pct"     : 50.0,
        "maintenance_margin_pct" : 25.0,
        "distribution_phases"    : [
            ("NKE 2007-2008", "2007-10-01", "2008-03-31"),
            ("NKE Q4 2018",   "2018-09-01", "2018-12-31"),
            ("NKE COVID",     "2020-01-15", "2020-03-15"),
            ("NKE 2021-2022", "2021-11-01", "2022-06-30"),
        ],
    },

    # ── G ─────────────────────────────────────────────────────────────────────
    "GOOGL": {
        "data_file"              : "GOOGL_full_1min.txt",
        "instrument_type"        : "equity",
        "point_value"            : 1,
        "tick_size"              : 0.01,
        "initial_margin_pct"     : 50.0,
        "maintenance_margin_pct" : 25.0,
        "distribution_phases"    : [
            ("GOOGL 2007-2008", "2007-10-01", "2008-03-31"),
            ("GOOGL Q4 2018",   "2018-09-01", "2018-12-31"),
            ("GOOGL COVID",     "2020-01-15", "2020-03-15"),
            ("GOOGL 2021-2022", "2021-11-01", "2022-06-30"),
            ("GOOGL Late 2023", "2023-07-01", "2023-10-31"),
        ],
    },

    "GS": {
        "data_file"              : "GS_full_1min.txt",
        "instrument_type"        : "equity",
        "point_value"            : 1,
        "tick_size"              : 0.01,
        "initial_margin_pct"     : 50.0,
        "maintenance_margin_pct" : 25.0,
        "distribution_phases"    : [
            ("GS 2007-2008", "2007-10-01", "2008-03-31"),
            ("GS Q4 2018",   "2018-09-01", "2018-12-31"),
            ("GS COVID",     "2020-01-15", "2020-03-15"),
            ("GS 2021-2022", "2021-11-01", "2022-06-30"),
        ],
    },

    "GE": {
        "data_file"              : "GE_full_1min.txt",
        "instrument_type"        : "equity",
        "point_value"            : 1,
        "tick_size"              : 0.01,
        "initial_margin_pct"     : 50.0,
        "maintenance_margin_pct" : 25.0,
        "distribution_phases"    : [
            ("GE 2000 Top",  "2000-01-01", "2000-06-30"),
            ("GE 2007-2008", "2007-10-01", "2008-03-31"),
            ("GE Q4 2018",   "2018-09-01", "2018-12-31"),
            ("GE COVID",     "2020-01-15", "2020-03-15"),
        ],
    },

    # ── T ─────────────────────────────────────────────────────────────────────
    "TSLA": {
        "data_file"              : "TSLA_full_1min.txt",
        "instrument_type"        : "equity",
        "point_value"            : 1,
        "tick_size"              : 0.01,
        "initial_margin_pct"     : 50.0,
        "maintenance_margin_pct" : 25.0,
        "distribution_phases"    : [
            ("TSLA Q4 2018",   "2018-09-01", "2018-12-31"),
            ("TSLA COVID",     "2020-01-15", "2020-03-15"),
            ("TSLA 2021-2022", "2021-11-01", "2022-06-30"),
            ("TSLA Late 2023", "2023-07-01", "2023-10-31"),
        ],
    },

    "TSM": {
        "data_file"              : "TSM_full_1min.txt",
        "instrument_type"        : "equity",
        "point_value"            : 1,
        "tick_size"              : 0.01,
        "initial_margin_pct"     : 50.0,
        "maintenance_margin_pct" : 25.0,
        "distribution_phases"    : [
            ("TSM 2007-2008", "2007-10-01", "2008-03-31"),
            ("TSM Q4 2018",   "2018-09-01", "2018-12-31"),
            ("TSM COVID",     "2020-01-15", "2020-03-15"),
            ("TSM 2021-2022", "2021-11-01", "2022-06-30"),
        ],
    },
}