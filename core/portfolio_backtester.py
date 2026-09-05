"""
CHRONOLOGICAL PORTFOLIO BACKTESTER - FUTURES & FX
Supports both futures and FX with proper currency conversion
"""

import pandas as pd
import numpy as np
from datetime import datetime, time, timedelta
from data_loader import load_and_create_resampled
from sq60_signals import SQ60Detector
from backtester import WeightedConfluenceChecker
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.gridspec import GridSpec
import seaborn as sns

plt.style.use('seaborn-v0_8-darkgrid')
sns.set_palette("husl")


class CurrencyConverter:
    """Handles real-time currency conversion for multi-currency portfolio"""
    
    def __init__(self, account_currency='EUR'):
        self.account_currency = account_currency
        self.rate_cache = {}
        self.eurusd_data = None
        self.usdjpy_data = None
        
    def load_fx_rates(self, eurusd_file=None, usdjpy_file=None):
        """Load FX rate data for conversions"""
        if eurusd_file:
            try:
                eurusd_minute, _ = load_and_create_resampled(eurusd_file, '1H')
                self.eurusd_data = eurusd_minute['close']
                print(f"  ✓ EUR/USD rates loaded: {len(self.eurusd_data):,} bars")
            except:
                print(f"  ⚠ EUR/USD data not found, using fixed rate 1.10")
                
        if usdjpy_file:
            try:
                usdjpy_minute, _ = load_and_create_resampled(usdjpy_file, '1H')
                self.usdjpy_data = usdjpy_minute['close']
                print(f"  ✓ USD/JPY rates loaded: {len(self.usdjpy_data):,} bars")
            except:
                print(f"  ⚠ USD/JPY data not found, using fixed rate 150.0")
    
    def get_rate(self, from_currency, to_currency, timestamp):
        """Get exchange rate at specific timestamp"""
        if from_currency == to_currency:
            return 1.0
            
        cache_key = f"{from_currency}/{to_currency}@{timestamp}"
        if cache_key in self.rate_cache:
            return self.rate_cache[cache_key]
        
        if from_currency == 'EUR' and to_currency == 'USD':
            if self.eurusd_data is not None:
                rate = self.eurusd_data.asof(timestamp)
                if pd.notna(rate):
                    self.rate_cache[cache_key] = rate
                    return rate
            return 1.10
            
        if from_currency == 'USD' and to_currency == 'EUR':
            if self.eurusd_data is not None:
                rate = self.eurusd_data.asof(timestamp)
                if pd.notna(rate):
                    rate = 1.0 / rate
                    self.rate_cache[cache_key] = rate
                    return rate
            return 1.0 / 1.10
        
        if from_currency == 'USD' and to_currency == 'JPY':
            if self.usdjpy_data is not None:
                rate = self.usdjpy_data.asof(timestamp)
                if pd.notna(rate):
                    self.rate_cache[cache_key] = rate
                    return rate
            return 150.0
            
        if from_currency == 'JPY' and to_currency == 'USD':
            if self.usdjpy_data is not None:
                rate = self.usdjpy_data.asof(timestamp)
                if pd.notna(rate):
                    rate = 1.0 / rate
                    self.rate_cache[cache_key] = rate
                    return rate
            return 1.0 / 150.0
        
        if from_currency == 'JPY' and to_currency == 'EUR':
            jpy_to_usd = self.get_rate('JPY', 'USD', timestamp)
            usd_to_eur = self.get_rate('USD', 'EUR', timestamp)
            rate = jpy_to_usd * usd_to_eur
            self.rate_cache[cache_key] = rate
            return rate
            
        return 1.0
    
    def convert(self, amount, from_currency, to_currency, timestamp):
        """Convert amount from one currency to another"""
        rate = self.get_rate(from_currency, to_currency, timestamp)
        return amount * rate


class InstrumentTracker:
    """Tracks state for one instrument (futures or FX)"""
    
    def __init__(self, name, config, shared_balance, currency_converter, start_date=None, end_date=None):
        self.name = name
        self.config = config
        self.shared_balance = shared_balance
        self.currency_converter = currency_converter
        
        print(f"  [{name}] Loading data...")
        self.minute_data, self.resampled_data = load_and_create_resampled(config['data_file'], '1H')
        
        if start_date:
            start_date = pd.to_datetime(start_date)
            self.minute_data = self.minute_data[self.minute_data.index >= start_date]
            self.resampled_data = self.resampled_data[self.resampled_data.index >= start_date]
        if end_date:
            end_date = pd.to_datetime(end_date)
            self.minute_data = self.minute_data[self.minute_data.index <= end_date]
            self.resampled_data = self.resampled_data[self.resampled_data.index <= end_date]
        
        print(f"  [{name}] ✓ {len(self.minute_data):,} minutes, {len(self.resampled_data):,} 1H bars")
        
        self.sq60 = SQ60Detector(timeframe='1H')
        
        _, self.resampled_4h = load_and_create_resampled(config['data_file'], '4H')
        if start_date:
            self.resampled_4h = self.resampled_4h[self.resampled_4h.index >= start_date]
        if end_date:
            self.resampled_4h = self.resampled_4h[self.resampled_4h.index <= end_date]
        self.sq60_4h = SQ60Detector(timeframe='4H')
        
        self.confluence_checker = WeightedConfluenceChecker(self.minute_data, verbose=False)
        
        self.current_position = None
        self.trades = []
        self.traded_days = set()
        self.trend_at_check_by_date = {}
        
        self.minute_data['session_date'] = self.minute_data.index.map(self.get_trading_session_date)
        self.daily_minute_groups = dict(list(self.minute_data.groupby('session_date')))
        
        self.last_processed_4h_idx = -1
        self.last_processed_minute_ts = None
    
    def get_trading_session_date(self, timestamp):
        """Determine trading session date"""
        current_time = timestamp.time()
        current_date = timestamp.date()
        
        if not self.config.get('trading_end_next_day', False):
            return current_date
        else:
            session_start_time = time(self.config['or_start_hour'], self.config['or_start_minute'])
            if current_time < session_start_time:
                return current_date - timedelta(days=1)
            else:
                return current_date
    
    def calculate_pip_value_eur(self, current_price, timestamp):
        """Calculate pip value in EUR (account currency)"""
        inst_type = self.config.get('instrument_type', 'FUTURE')
        
        if inst_type == 'FUTURE':
            pip_value_eur = self.config['point_value']
            return pip_value_eur
            
        elif inst_type == 'FX':
            contract_size = self.config.get('contract_size', 100000)
            pip_size = self.config.get('pip_size', 0.0001)
            base_currency = self.config.get('base_currency', 'EUR')
            quote_currency = self.config.get('quote_currency', 'USD')
            
            # EUR/USD: profit currency = USD (quote)
            if base_currency == 'EUR' and quote_currency == 'USD':
                pip_value_usd = pip_size * contract_size
                pip_value_eur = self.currency_converter.convert(
                    pip_value_usd, 'USD', 'EUR', timestamp
                )
                return pip_value_eur
            
            # USD/JPY: profit currency = JPY (quote)
            elif base_currency == 'USD' and quote_currency == 'JPY':
                pip_value_jpy = pip_size * contract_size
                pip_value_eur = self.currency_converter.convert(
                    pip_value_jpy, 'JPY', 'EUR', timestamp
                )
                return pip_value_eur
            
            else:
                return self.config.get('point_value', 10)
        
        return self.config.get('point_value', 10)
    
    def calculate_contract_value_eur(self, price, timestamp):
        """Calculate notional contract value in EUR"""
        inst_type = self.config.get('instrument_type', 'FUTURE')
        
        if inst_type == 'FUTURE':
            return price * self.config['point_value']
            
        elif inst_type == 'FX':
            contract_size = self.config.get('contract_size', 100000)
            base_currency = self.config.get('base_currency', 'EUR')
            quote_currency = self.config.get('quote_currency', 'USD')
            
            if base_currency == 'EUR':
                notional_eur = contract_size
                return notional_eur
            elif base_currency == 'USD':
                notional_usd = contract_size
                notional_eur = self.currency_converter.convert(
                    notional_usd, 'USD', 'EUR', timestamp
                )
                return notional_eur
            else:
                return contract_size
        
        return price * self.config.get('point_value', 25)
    
    def calculate_or(self, day_data):
        """Calculate Opening Range"""
        or_start_minutes = self.config['or_start_hour'] * 60 + self.config['or_start_minute']
        or_end_minutes = self.config['or_end_hour'] * 60 + self.config['or_end_minute']
        
        day_data = day_data.copy()
        day_data['minutes_since_midnight'] = day_data.index.hour * 60 + day_data.index.minute
        
        or_crosses_midnight = or_end_minutes < or_start_minutes
        
        if or_crosses_midnight:
            or_data = day_data[
                (day_data['minutes_since_midnight'] >= or_start_minutes) |
                (day_data['minutes_since_midnight'] < or_end_minutes)
            ]
        else:
            or_data = day_data[
                (day_data['minutes_since_midnight'] >= or_start_minutes) &
                (day_data['minutes_since_midnight'] < or_end_minutes)
            ]
        
        if len(or_data) == 0:
            return None, None
        
        or_high = or_data['high'].max()
        or_low = or_data['low'].min()
        
        if (or_high - or_low) < self.config['min_or_range']:
            return None, None
        
        return or_high, or_low
    
    def is_trend_confirmed(self, current_time):
        """Check if trend is confirmed"""
        if self.sq60.trend == "OFF":
            return False
        
        if len(self.sq60.swing_points) < 4:
            return False
        
        last_4_swings = self.sq60.swing_points[-4:]
        confirmation_bars = 7
        bar_duration = pd.Timedelta(hours=1)
        
        for swing_ts, swing_price, swing_type in last_4_swings:
            confirm_time = swing_ts + (bar_duration * confirmation_bars)
            if confirm_time > current_time:
                return False
        
        return True
    
    def is_in_trading_window(self, timestamp):
        """Check if in trading window"""
        hour = timestamp.hour
        minute = timestamp.minute
        
        current_time = hour * 60 + minute
        start_time = self.config['trading_start_hour'] * 60 + self.config['trading_start_minute']
        end_time = self.config['trading_end_hour'] * 60 + self.config['trading_end_minute']
        
        if self.config.get('trading_end_next_day', False):
            return current_time >= start_time or current_time <= end_time
        else:
            return start_time <= current_time <= end_time
    
    def is_time_stop(self, timestamp):
        """Check if time stop"""
        return (timestamp.hour == self.config['trading_end_hour'] and 
                timestamp.minute == self.config['trading_end_minute'])
    
    def calculate_position_size(self, entry, stop, multiplier, timestamp):
        """Calculate position size (supports fractional lots for FX)"""
        inst_type = self.config.get('instrument_type', 'FUTURE')
        
        risk_per_point = abs(entry - stop)
        pips_at_risk = risk_per_point / self.config.get('pip_size', 1.0)
        
        pip_value_eur = self.calculate_pip_value_eur(entry, timestamp)
        risk_per_lot = pips_at_risk * pip_value_eur
        
        max_risk_eur = self.shared_balance['balance'] * 0.02
        base_size = max_risk_eur / risk_per_lot if risk_per_lot > 0 else 0
        
        size_with_multiplier = base_size * multiplier
        
        contract_value_eur = self.calculate_contract_value_eur(entry, timestamp)
        initial_margin_per_unit = contract_value_eur * (self.config.get('initial_margin_pct', 10.0) / 100)
        
        available_for_margin = self.shared_balance['balance'] * 0.80 - self.shared_balance['margin_in_use']
        
        if available_for_margin <= 0:
            return 0.0
        
        size_by_margin = available_for_margin / initial_margin_per_unit if initial_margin_per_unit > 0 else 0
        
        final_size = min(size_with_multiplier, size_by_margin)
        
        if inst_type == 'FUTURE':
            final_size = int(final_size)
            final_size = max(0, final_size)
        else:
            final_size = round(final_size, 2)
            final_size = max(0.01, final_size) if final_size > 0 else 0.0
        
        return final_size


class ChronologicalPortfolioBacktester:
    """Chronological portfolio backtester with FX support"""
    
    def __init__(self, instruments_config, starting_balance=100000, 
                 start_date=None, end_date=None, verbose=False,
                 vix_filter=None):  # ← ADDED: optional vix_filter parameter
        
        self.starting_balance = starting_balance
        self.shared_balance = {
            'balance': starting_balance,
            'peak_balance': starting_balance,
            'max_drawdown_eur': 0,
            'max_drawdown_pct': 0,
            'margin_in_use': 0.0
        }
        
        self.verbose = verbose
        self.start_date = start_date
        self.end_date = end_date
        self.vix_filter = vix_filter  # ← ADDED: store the filter
        
        print("\n" + "="*80)
        print("CHRONOLOGICAL PORTFOLIO BACKTESTER - FUTURES & FX")
        print("="*80)
        print(f"Starting Balance: €{starting_balance:,.0f}")
        print(f"Date Range: {start_date or 'ALL'} to {end_date or 'ALL'}")
        # ── ADDED: print VIX filter status on startup ──────────────────────
        if self.vix_filter is not None:
            s = self.vix_filter.summary()
            direction = '≥' if s['mode'] == 'gte' else '≤'
            print(f"VIX Filter: ACTIVE — trade only when VIX {direction} {s['threshold']} ({s['tradeable_pct']}% of days pass)")
        else:
            print(f"VIX Filter: DISABLED")
        # ───────────────────────────────────────────────────────────────────
        print("="*80)
        
        print(f"\n💱 Initializing Currency Converter...")
        self.currency_converter = CurrencyConverter(account_currency='EUR')
        
        print(f"\n📊 Initializing {len(instruments_config)} instruments...")
        self.instruments = {}
        
        for config in instruments_config:
            tracker = InstrumentTracker(
                name=config['name'],
                config=config,
                shared_balance=self.shared_balance,
                currency_converter=self.currency_converter,
                start_date=start_date,
                end_date=end_date
            )
            self.instruments[config['name']] = tracker
            
            # Smart loading: If this instrument IS a currency pair we need, use its data!
            if config['name'] == 'EURUSD' and config.get('instrument_type') == 'FX':
                print(f"  ✓ Using EURUSD data for currency conversion")
                self.currency_converter.eurusd_data = tracker.minute_data['close']
            elif config['name'] == 'USDJPY' and config.get('instrument_type') == 'FX':
                print(f"  ✓ Using USDJPY data for currency conversion")
                self.currency_converter.usdjpy_data = tracker.minute_data['close']
        
        # Check if we still need to load any missing FX data
        if self.currency_converter.eurusd_data is None:
            print(f"  ⚠ EURUSD not in portfolio, using fixed rate 1.10")
        if self.currency_converter.usdjpy_data is None:
            print(f"  ⚠ USDJPY not in portfolio, using fixed rate 150.0")
        
        self.all_trades = []
        self.balance_history = []
    
    def update_drawdown(self):
        """Update max drawdown"""
        if self.shared_balance['balance'] > self.shared_balance['peak_balance']:
            self.shared_balance['peak_balance'] = self.shared_balance['balance']
        
        dd_eur = self.shared_balance['peak_balance'] - self.shared_balance['balance']
        dd_pct = (dd_eur / self.shared_balance['peak_balance']) * 100 if self.shared_balance['peak_balance'] > 0 else 0
        
        if dd_eur > self.shared_balance['max_drawdown_eur']:
            self.shared_balance['max_drawdown_eur'] = dd_eur
            self.shared_balance['max_drawdown_pct'] = dd_pct
    
    def run(self):
        """Run chronological backtest"""
        
        print("\n⚙️  Building unified timeline...")
        
        all_bars = []
        for name, tracker in self.instruments.items():
            for bar_ts, bar_data in tracker.resampled_data.iterrows():
                all_bars.append((bar_ts, name, bar_data))
        
        all_bars.sort(key=lambda x: x[0])
        print(f"   Total bars: {len(all_bars):,}")
        
        print("\n🚀 Running backtest...\n")
        
        last_pct = 0
        
        for bar_idx, (bar_ts, inst_name, bar_data) in enumerate(all_bars):
            tracker = self.instruments[inst_name]
            
            pct = int((bar_idx / len(all_bars)) * 100)
            if pct >= last_pct + 10:
                print(f"   {pct}% - {bar_ts.date()} - Balance: €{self.shared_balance['balance']:,.0f}")
                last_pct = pct
            
            tracker.sq60.update(
                timestamp=bar_ts,
                open_price=bar_data['open'],
                high=bar_data['high'],
                low=bar_data['low'],
                close=bar_data['close']
            )
            
            bars_4h_to_process = tracker.resampled_4h[tracker.resampled_4h.index <= bar_ts]
            
            for idx, (bar_4h_ts, bar_4h_data) in enumerate(bars_4h_to_process.iterrows()):
                if idx > tracker.last_processed_4h_idx:
                    tracker.sq60_4h.update(
                        timestamp=bar_4h_ts,
                        open_price=bar_4h_data['open'],
                        high=bar_4h_data['high'],
                        low=bar_4h_data['low'],
                        close=bar_4h_data['close']
                    )
                    tracker.last_processed_4h_idx = idx
            
            bar_duration = pd.Timedelta(hours=1)
            bar_start = bar_ts + bar_duration
            bar_end = bar_start + bar_duration
            
            minute_bars = tracker.minute_data[
                (tracker.minute_data.index >= bar_start) & 
                (tracker.minute_data.index < bar_end)
            ]
            
            for minute_ts, minute_bar in minute_bars.iterrows():
                tracker.sq60.check_minute_trigger(minute_ts, minute_bar['close'])
                
                if tracker.last_processed_minute_ts is None or minute_ts > tracker.last_processed_minute_ts:
                    tracker.sq60_4h.check_minute_trigger(minute_ts, minute_bar['close'])
                    tracker.last_processed_minute_ts = minute_ts
                
                current_balance_with_unrealized = self.shared_balance['balance']
                
                if tracker.current_position:
                    pos = tracker.current_position
                    
                    if pos['direction'] == 'LONG':
                        price_change = minute_bar['close'] - pos['entry_price']
                    else:
                        price_change = pos['entry_price'] - minute_bar['close']
                    
                    pips = price_change / tracker.config.get('pip_size', 1.0)
                    pip_value_eur = tracker.calculate_pip_value_eur(minute_bar['close'], minute_ts)
                    unrealized_pnl = pips * pip_value_eur * pos['size']
                    current_balance_with_unrealized += unrealized_pnl
                
                self.balance_history.append({
                    'timestamp': minute_ts,
                    'balance': current_balance_with_unrealized,
                    'instrument': inst_name
                })
                
                self.update_drawdown()
                
                session_date = tracker.get_trading_session_date(minute_ts)
                
                if (minute_ts.hour == tracker.config['trend_check_hour'] and 
                    minute_ts.minute == tracker.config['trend_check_minute'] and 
                    session_date not in tracker.traded_days):

                    # ── ADDED: VIX gate — skip day entirely if VIX doesn't pass ──
                    if self.vix_filter is not None and not self.vix_filter.is_tradeable(session_date):
                        if self.verbose:
                            print(f"[{inst_name}] {session_date} — VIX {self.vix_filter.get_vix(session_date):.1f} < {self.vix_filter.threshold} → BLOCKED")
                        continue
                    # ────────────────────────────────────────────────────────────
                    
                    if session_date in tracker.daily_minute_groups:
                        day_data = tracker.daily_minute_groups[session_date]
                        or_high, or_low = tracker.calculate_or(day_data)
                        
                        if or_high is not None:
                            trend_at_check = tracker.sq60.trend
                            is_confirmed = tracker.is_trend_confirmed(minute_ts)
                            
                            tracker.trend_at_check_by_date[session_date] = {
                                'trend': trend_at_check,
                                'confirmed': is_confirmed,
                                'or_high': or_high,
                                'or_low': or_low
                            }
                            
                            if is_confirmed and trend_at_check in ['SQ_LONG', 'SQ_SHORT']:
                                trend_4h = tracker.sq60_4h.get_trend_state()
                                confluence_result = tracker.confluence_checker.calculate_confluence(
                                    minute_ts, trend_at_check, trend_4h
                                )
                                
                                tracker.trend_at_check_by_date[session_date]['multiplier'] = confluence_result['multiplier']
                                tracker.trend_at_check_by_date[session_date]['should_trade_mtf'] = confluence_result['should_trade']
                                tracker.trend_at_check_by_date[session_date]['mtf_reason'] = confluence_result['reason']
                            else:
                                tracker.trend_at_check_by_date[session_date]['multiplier'] = 1.0
                                tracker.trend_at_check_by_date[session_date]['should_trade_mtf'] = True
                
                if tracker.is_in_trading_window(minute_ts):
                    if session_date in tracker.trend_at_check_by_date and session_date not in tracker.traded_days:
                        snapshot = tracker.trend_at_check_by_date[session_date]
                        
                        trend_check = snapshot['trend']
                        confirmed_check = snapshot['confirmed']
                        or_high = snapshot['or_high']
                        or_low = snapshot['or_low']
                        
                        if confirmed_check and trend_check in ['SQ_LONG', 'SQ_SHORT'] and tracker.current_position is None:
                            if trend_check == 'SQ_LONG' and minute_bar['close'] > or_high:
                                entry = minute_bar['close']
                                stop = or_low
                                or_range = or_high - or_low
                                
                                entry_distance = entry - or_high
                                if entry_distance > or_range * 0.5:
                                    continue
                                
                                target = entry + (or_range * tracker.config['profit_target_multiplier'])
                                
                                multiplier = snapshot.get('multiplier', 1.0)
                                should_trade_mtf = snapshot.get('should_trade_mtf', True)
                                
                                if not should_trade_mtf:
                                    continue
                                
                                size = tracker.calculate_position_size(entry, stop, int(multiplier), minute_ts)
                                
                                if size == 0:
                                    continue
                                
                                contract_value_eur = tracker.calculate_contract_value_eur(entry, minute_ts)
                                initial_margin = contract_value_eur * (tracker.config.get('initial_margin_pct', 10.0) / 100) * size
                                pct_balance_used = (initial_margin / self.shared_balance['balance']) * 100
                                
                                if pct_balance_used > 80:
                                    continue
                                
                                self.shared_balance['margin_in_use'] += initial_margin
                                
                                tracker.current_position = {
                                    'entry_time': minute_ts,
                                    'entry_price': entry,
                                    'direction': 'LONG',
                                    'stop': stop,
                                    'target': target,
                                    'size': size,
                                    'multiplier': multiplier,
                                    'instrument': inst_name,
                                    'initial_margin': initial_margin
                                }
                                
                                if self.verbose:
                                    inst_type = tracker.config.get('instrument_type', 'FUTURE')
                                    size_label = f"{size:.2f} lots" if inst_type == 'FX' else f"{size} contracts"
                                    print(f"[{inst_name}] {minute_ts.strftime('%Y-%m-%d %H:%M')} - LONG @ {entry:.4f}, {size_label}, {multiplier}x")
                            
                            elif trend_check == 'SQ_SHORT' and minute_bar['close'] < or_low:
                                entry = minute_bar['close']
                                stop = or_high
                                or_range = or_high - or_low
                                
                                entry_distance = or_low - entry
                                if entry_distance > or_range * 0.5:
                                    continue
                                
                                target = entry - (or_range * tracker.config['profit_target_multiplier'])
                                
                                multiplier = snapshot.get('multiplier', 1.0)
                                should_trade_mtf = snapshot.get('should_trade_mtf', True)
                                
                                if not should_trade_mtf:
                                    continue
                                
                                size = tracker.calculate_position_size(entry, stop, int(multiplier), minute_ts)
                                
                                if size == 0:
                                    continue
                                
                                contract_value_eur = tracker.calculate_contract_value_eur(entry, minute_ts)
                                initial_margin = contract_value_eur * (tracker.config.get('initial_margin_pct', 10.0) / 100) * size
                                pct_balance_used = (initial_margin / self.shared_balance['balance']) * 100
                                
                                if pct_balance_used > 80:
                                    continue
                                
                                self.shared_balance['margin_in_use'] += initial_margin
                                
                                tracker.current_position = {
                                    'entry_time': minute_ts,
                                    'entry_price': entry,
                                    'direction': 'SHORT',
                                    'stop': stop,
                                    'target': target,
                                    'size': size,
                                    'multiplier': multiplier,
                                    'instrument': inst_name,
                                    'initial_margin': initial_margin
                                }
                                
                                if self.verbose:
                                    inst_type = tracker.config.get('instrument_type', 'FUTURE')
                                    size_label = f"{size:.2f} lots" if inst_type == 'FX' else f"{size} contracts"
                                    print(f"[{inst_name}] {minute_ts.strftime('%Y-%m-%d %H:%M')} - SHORT @ {entry:.4f}, {size_label}, {multiplier}x")
                
                if tracker.current_position:
                    pos = tracker.current_position
                    exit_reason = None
                    exit_price = None
                    
                    if pos['direction'] == 'LONG':
                        price_change = minute_bar['close'] - pos['entry_price']
                    else:
                        price_change = pos['entry_price'] - minute_bar['close']
                    
                    pips = price_change / tracker.config.get('pip_size', 1.0)
                    pip_value_eur = tracker.calculate_pip_value_eur(minute_bar['close'], minute_ts)
                    unrealized_pnl = pips * pip_value_eur * pos['size']
                    
                    current_equity = self.shared_balance['balance'] + unrealized_pnl
                    
                    contract_value_eur = tracker.calculate_contract_value_eur(pos['entry_price'], pos['entry_time'])
                    maintenance_margin = contract_value_eur * 0.075 * pos['size']
                    
                    if current_equity < maintenance_margin:
                        exit_price = minute_bar['close']
                        exit_reason = 'Margin Call'
                    
                    if exit_price is None:
                        if pos['direction'] == 'LONG':
                            target_hit = minute_bar['high'] >= pos['target']
                            stop_hit = minute_bar['low'] <= pos['stop']
                            
                            if target_hit and stop_hit:
                                distance_to_stop = abs(pos['entry_price'] - pos['stop'])
                                distance_to_target = abs(pos['target'] - pos['entry_price'])
                                
                                if distance_to_stop <= distance_to_target:
                                    exit_price = pos['stop']
                                    exit_reason = 'Stop Loss'
                                else:
                                    exit_price = pos['target']
                                    exit_reason = 'Target'
                            elif target_hit:
                                exit_price = pos['target']
                                exit_reason = 'Target'
                            elif stop_hit:
                                exit_price = pos['stop']
                                exit_reason = 'Stop Loss'
                        else:
                            target_hit = minute_bar['low'] <= pos['target']
                            stop_hit = minute_bar['high'] >= pos['stop']
                            
                            if target_hit and stop_hit:
                                distance_to_stop = abs(pos['stop'] - pos['entry_price'])
                                distance_to_target = abs(pos['entry_price'] - pos['target'])
                                
                                if distance_to_stop <= distance_to_target:
                                    exit_price = pos['stop']
                                    exit_reason = 'Stop Loss'
                                else:
                                    exit_price = pos['target']
                                    exit_reason = 'Target'
                            elif target_hit:
                                exit_price = pos['target']
                                exit_reason = 'Target'
                            elif stop_hit:
                                exit_price = pos['stop']
                                exit_reason = 'Stop Loss'
                        
                        if tracker.is_time_stop(minute_ts) and exit_price is None:
                            exit_price = minute_bar['close']
                            exit_reason = 'Time Stop'
                    
                    if exit_price:
                        if pos['direction'] == 'LONG':
                            price_change = exit_price - pos['entry_price']
                        else:
                            price_change = pos['entry_price'] - exit_price
                        
                        pips = price_change / tracker.config.get('pip_size', 1.0)
                        pip_value_eur = tracker.calculate_pip_value_eur(exit_price, minute_ts)
                        pnl_eur = pips * pip_value_eur * pos['size']
                        
                        self.shared_balance['balance'] += pnl_eur
                        
                        margin_to_release = pos.get('initial_margin', 0)
                        self.shared_balance['margin_in_use'] -= margin_to_release
                        
                        if self.shared_balance['margin_in_use'] < 0:
                            self.shared_balance['margin_in_use'] = 0
                        
                        self.update_drawdown()
                        
                        trade_record = {
                            'date': pos['entry_time'].date(),
                            'entry_time': pos['entry_time'],
                            'exit_time': minute_ts,
                            'instrument': inst_name,
                            'direction': pos['direction'],
                            'entry_price': pos['entry_price'],
                            'exit_price': exit_price,
                            'stop': pos['stop'],
                            'target': pos['target'],
                            'size': pos['size'],
                            'pips': pips,
                            'pnl_eur': pnl_eur,
                            'balance': self.shared_balance['balance'],
                            'exit_reason': exit_reason,
                            'multiplier': pos['multiplier'],
                            'margin_used': margin_to_release
                        }
                        
                        tracker.trades.append(trade_record)
                        self.all_trades.append(trade_record)
                        tracker.traded_days.add(session_date)
                        
                        if self.verbose:
                            status = "✓" if pnl_eur > 0 else "✗"
                            print(f"[{inst_name}] {minute_ts.strftime('%Y-%m-%d %H:%M')} - {status} {exit_reason} @ {exit_price:.4f}, P&L: €{pnl_eur:+,.0f}, Balance: €{self.shared_balance['balance']:,.0f}")
                        
                        tracker.current_position = None
        
        print("\n✓ Backtest complete!")

        # ── ADDED: VIX filter end-of-run summary ──────────────────────────────
        if self.vix_filter is not None:
            s = self.vix_filter.summary()
            direction = '≥' if s['mode'] == 'gte' else '≤'
            print(f"\n🔎 VIX Filter Summary:")
            print(f"   Threshold      : VIX {direction} {s['threshold']}")
            print(f"   Tradeable days : {s['tradeable_days']:,} / {s['total_days']:,} ({s['tradeable_pct']}%)")
            print(f"   Blocked days   : {s['blocked_days']:,}")
            print(f"   VIX range      : {s['vix_min']} – {s['vix_max']}  (mean {s['vix_mean']})")
        # ─────────────────────────────────────────────────────────────────────

        return self.get_results()
    
    def get_results(self):
        """Compile results"""
        
        trades_df = pd.DataFrame(self.all_trades) if self.all_trades else pd.DataFrame()
        
        results = {
            'starting_balance': self.starting_balance,
            'final_balance': self.shared_balance['balance'],
            'return_pct': ((self.shared_balance['balance'] - self.starting_balance) / self.starting_balance) * 100,
            'max_drawdown_eur': self.shared_balance['max_drawdown_eur'],
            'max_drawdown_pct': self.shared_balance['max_drawdown_pct'],
            'all_trades': trades_df,
            'balance_history': pd.DataFrame(self.balance_history),
            'by_instrument': {}
        }
        
        if len(trades_df) > 0:
            wins = trades_df[trades_df['pnl_eur'] > 0]
            losses = trades_df[trades_df['pnl_eur'] < 0]
            
            results['total_trades'] = len(trades_df)
            results['wins'] = len(wins)
            results['losses'] = len(losses)
            results['win_rate'] = (len(wins) / len(trades_df)) * 100 if len(trades_df) > 0 else 0
            
            results['avg_win'] = wins['pnl_eur'].mean() if len(wins) > 0 else 0
            results['avg_loss'] = losses['pnl_eur'].mean() if len(losses) > 0 else 0
            
            if len(losses) > 0 and losses['pnl_eur'].sum() != 0:
                results['profit_factor'] = abs(wins['pnl_eur'].sum() / losses['pnl_eur'].sum())
            else:
                results['profit_factor'] = float('inf') if len(wins) > 0 else 0
            
            if self.start_date and self.end_date:
                start = pd.to_datetime(self.start_date)
                end = pd.to_datetime(self.end_date)
                trading_days = (end - start).days
                years = trading_days / 365.25
                
                if years > 0 and results['final_balance'] > 0 and self.starting_balance > 0:
                    results['cagr'] = (((results['final_balance'] / self.starting_balance) ** (1 / years)) - 1) * 100
                    results['trading_years'] = years
                else:
                    results['cagr'] = 0
                    results['trading_years'] = 0
            
            if 'multiplier' in trades_df.columns:
                for mult in sorted(trades_df['multiplier'].unique()):
                    mult_trades = trades_df[trades_df['multiplier'] == mult]
                    mult_wins = mult_trades[mult_trades['pnl_eur'] > 0]
                    
                    results[f'trades_{mult}x'] = len(mult_trades)
                    results[f'win_rate_{mult}x'] = (len(mult_wins) / len(mult_trades)) * 100 if len(mult_trades) > 0 else 0
                    results[f'pnl_{mult}x'] = mult_trades['pnl_eur'].sum()
                    results[f'avg_pnl_{mult}x'] = mult_trades['pnl_eur'].mean()
        
        for name, tracker in self.instruments.items():
            if tracker.trades:
                inst_trades = pd.DataFrame(tracker.trades)
                wins = inst_trades[inst_trades['pnl_eur'] > 0]
                losses = inst_trades[inst_trades['pnl_eur'] < 0]
                
                results['by_instrument'][name] = {
                    'trades': len(tracker.trades),
                    'wins': len(wins),
                    'losses': len(losses),
                    'win_rate': (len(wins) / len(tracker.trades)) * 100,
                    'total_pnl': inst_trades['pnl_eur'].sum(),
                    'avg_win': wins['pnl_eur'].mean() if len(wins) > 0 else 0,
                    'avg_loss': losses['pnl_eur'].mean() if len(losses) > 0 else 0,
                    'profit_factor': abs(wins['pnl_eur'].sum() / losses['pnl_eur'].sum()) if len(losses) > 0 and losses['pnl_eur'].sum() != 0 else float('inf')
                }
            else:
                results['by_instrument'][name] = {
                    'trades': 0,
                    'wins': 0,
                    'losses': 0,
                    'win_rate': 0,
                    'total_pnl': 0,
                    'avg_win': 0,
                    'avg_loss': 0,
                    'profit_factor': 0
                }
        
        return results
    
    def calculate_advanced_metrics(self, results):
        """Calculate performance metrics"""
        
        metrics = {}
        
        if len(results['all_trades']) == 0:
            return metrics
        
        trades_df = results['all_trades']
        returns = trades_df['pnl_eur'] / results['starting_balance']
        
        if len(returns) > 1:
            returns_std = returns.std()
            if returns_std > 0:
                sharpe = (returns.mean() / returns_std) * np.sqrt(252)
                metrics['sharpe_ratio'] = sharpe
            else:
                metrics['sharpe_ratio'] = 0
        else:
            metrics['sharpe_ratio'] = 0
        
        negative_returns = returns[returns < 0]
        if len(negative_returns) > 0:
            downside_std = negative_returns.std()
            if downside_std > 0:
                sortino = (returns.mean() / downside_std) * np.sqrt(252)
                metrics['sortino_ratio'] = sortino
            else:
                metrics['sortino_ratio'] = 0
        else:
            metrics['sortino_ratio'] = float('inf')
        
        if 'cagr' in results and results['max_drawdown_pct'] > 0:
            metrics['calmar_ratio'] = results['cagr'] / results['max_drawdown_pct']
        else:
            metrics['calmar_ratio'] = 0
        
        wins = trades_df[trades_df['pnl_eur'] > 0]
        losses = trades_df[trades_df['pnl_eur'] < 0]
        
        if len(losses) > 0:
            metrics['win_loss_ratio'] = abs(wins['pnl_eur'].mean() / losses['pnl_eur'].mean())
        else:
            metrics['win_loss_ratio'] = float('inf')
        
        metrics['expectancy'] = trades_df['pnl_eur'].mean()
        
        trades_sorted = trades_df.sort_values('entry_time')
        trades_sorted['is_win'] = trades_sorted['pnl_eur'] > 0
        
        max_consec_wins = 0
        max_consec_losses = 0
        current_wins = 0
        current_losses = 0
        
        for is_win in trades_sorted['is_win']:
            if is_win:
                current_wins += 1
                current_losses = 0
                max_consec_wins = max(max_consec_wins, current_wins)
            else:
                current_losses += 1
                current_wins = 0
                max_consec_losses = max(max_consec_losses, current_losses)
        
        metrics['max_consecutive_wins'] = max_consec_wins
        metrics['max_consecutive_losses'] = max_consec_losses
        
        net_profit = results['final_balance'] - results['starting_balance']
        if results['max_drawdown_eur'] > 0:
            metrics['recovery_factor'] = net_profit / results['max_drawdown_eur']
        else:
            metrics['recovery_factor'] = float('inf')
        
        trades_df['duration'] = (trades_df['exit_time'] - trades_df['entry_time']).dt.total_seconds() / 3600
        metrics['avg_trade_duration_hours'] = trades_df['duration'].mean()
        
        if 'trading_years' in results and results['trading_years'] > 0:
            metrics['trades_per_month'] = len(trades_df) / (results['trading_years'] * 12)
        
        return metrics
    
    def plot_simple_balance_chart(self, results, save_path='performance_dashboard.png'):
        """Create comprehensive dashboard with balance, metrics, and breakdown"""
        
        if len(results['balance_history']) == 0:
            print("No balance history to plot")
            return
        
        fig = plt.figure(figsize=(20, 12))
        gs = GridSpec(3, 3, figure=fig, hspace=0.35, wspace=0.4)
        
        # 1. BALANCE CHART (top, full width)
        ax_balance = fig.add_subplot(gs[0:2, :])
        
        balance_df = results['balance_history'].copy()
        balance_df = balance_df.set_index('timestamp')
        balance_hourly = balance_df['balance'].resample('H').last().ffill()
        
        ax_balance.plot(balance_hourly.index, balance_hourly.values, 
                linewidth=2.5, color='#2E86AB', label='Portfolio Balance', zorder=3)
        
        starting = results['starting_balance']
        ax_balance.axhline(y=starting, color='gray', linestyle='--', 
                   linewidth=1.5, alpha=0.6, label=f'Starting: €{starting:,.0f}')
        
        ax_balance.fill_between(balance_hourly.index, balance_hourly.values, starting, 
                                where=(balance_hourly.values >= starting), 
                                color='green', alpha=0.1, interpolate=True)
        ax_balance.fill_between(balance_hourly.index, balance_hourly.values, starting, 
                                where=(balance_hourly.values < starting), 
                                color='red', alpha=0.1, interpolate=True)
        
        final = results['final_balance']
        final_date = balance_hourly.index[-1]
        ax_balance.plot(final_date, final, 'o', color='red', markersize=14, zorder=5)
        
        return_pct = results['return_pct']
        color = 'green' if return_pct > 0 else 'red'
        ax_balance.annotate(f'€{final:,.0f}\n({return_pct:+.1f}%)', 
                   xy=(final_date, final),
                   xytext=(30, 30), 
                   textcoords='offset points',
                   fontsize=14, 
                   fontweight='bold',
                   color=color,
                   bbox=dict(boxstyle='round,pad=0.8', facecolor='white', edgecolor=color, linewidth=2),
                   arrowprops=dict(arrowstyle='->', connectionstyle='arc3,rad=0', lw=2, color=color))
        
        ax_balance.set_title('Portfolio Performance Dashboard', 
                    fontsize=20, fontweight='bold', pad=20)
        
        ax_balance.set_xlabel('Date', fontsize=12, fontweight='bold')
        ax_balance.set_ylabel('Balance (€)', fontsize=12, fontweight='bold')
        ax_balance.grid(True, alpha=0.3, linestyle='--')
        ax_balance.legend(loc='upper left', fontsize=11, framealpha=0.95)
        
        ax_balance.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'€{x:,.0f}'))
        ax_balance.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
        ax_balance.xaxis.set_major_locator(mdates.MonthLocator(interval=1))
        plt.setp(ax_balance.xaxis.get_majorticklabels(), rotation=45, ha='right')
        
        # 2. KEY METRICS TABLE (bottom left)
        ax_metrics = fig.add_subplot(gs[2, 0])
        ax_metrics.axis('off')
        
        metrics_data = []
        metrics_data.append(['Starting Balance', f'€{results["starting_balance"]:,.0f}'])
        metrics_data.append(['Final Balance', f'€{results["final_balance"]:,.0f}'])
        metrics_data.append(['Total Return', f'{results["return_pct"]:+.2f}%'])
        
        if 'cagr' in results:
            metrics_data.append(['CAGR', f'{results["cagr"]:.2f}%'])
        
        metrics_data.append(['Max Drawdown', f'{results["max_drawdown_pct"]:.2f}%'])
        
        if 'total_trades' in results:
            metrics_data.append(['Total Trades', f'{results["total_trades"]}'])
            metrics_data.append(['Win Rate', f'{results["win_rate"]:.1f}%'])
            
            pf = results.get('profit_factor', 0)
            pf_str = f'{pf:.2f}' if pf != float('inf') else '∞'
            metrics_data.append(['Profit Factor', pf_str])
        
        table = ax_metrics.table(cellText=metrics_data,
                                colWidths=[0.6, 0.4],
                                cellLoc='left',
                                loc='center',
                                bbox=[0, 0, 1, 1])
        
        table.auto_set_font_size(False)
        table.set_fontsize(11)
        table.scale(1, 2.5)
        
        for i in range(len(metrics_data)):
            cell = table[(i, 0)]
            cell.set_facecolor('#E8E8E8')
            cell.set_text_props(weight='bold')
            
            cell_val = table[(i, 1)]
            if 'Return' in metrics_data[i][0] or 'CAGR' in metrics_data[i][0]:
                val_str = metrics_data[i][1].replace('%', '').replace('€', '').replace(',', '')
                try:
                    val = float(val_str)
                    if val > 0:
                        cell_val.set_facecolor('#D4EDDA')
                        cell_val.set_text_props(color='green', weight='bold')
                    elif val < 0:
                        cell_val.set_facecolor('#F8D7DA')
                        cell_val.set_text_props(color='red', weight='bold')
                except:
                    pass
        
        ax_metrics.set_title('Key Metrics', fontsize=14, fontweight='bold', pad=10)
        
        # 3. INSTRUMENT BREAKDOWN PIE CHART (bottom middle)
        ax_pie = fig.add_subplot(gs[2, 1])
        
        if len(results['all_trades']) > 0:
            inst_pnl = {}
            for name, stats in results['by_instrument'].items():
                if stats['total_pnl'] != 0:
                    inst_pnl[name] = stats['total_pnl']
            
            if inst_pnl:
                colors_pie = ['#2E86AB', '#A23B72', '#F18F01', '#C73E1D', '#6A994E', '#BC4B51']
                
                sorted_inst = sorted(inst_pnl.items(), key=lambda x: abs(x[1]), reverse=True)
                labels = []
                values = []
                colors_used = []
                
                for i, (name, pnl) in enumerate(sorted_inst):
                    labels.append(f'{name}\n€{pnl:,.0f}')
                    values.append(abs(pnl))
                    
                    if pnl > 0:
                        colors_used.append(colors_pie[i % len(colors_pie)])
                    else:
                        colors_used.append('#F8D7DA')
                
                wedges, texts, autotexts = ax_pie.pie(
                    values, 
                    labels=labels, 
                    autopct=lambda pct: f'{pct:.1f}%' if pct > 3 else '',
                    colors=colors_used,
                    startangle=90,
                    textprops={'fontsize': 9, 'weight': 'bold'}
                )
                
                for autotext in autotexts:
                    autotext.set_color('white')
                    autotext.set_fontsize(8)
                    autotext.set_weight('bold')
                
                ax_pie.set_title('P&L Contribution\n(by magnitude)', fontsize=13, fontweight='bold', pad=10)
            else:
                ax_pie.text(0.5, 0.5, 'No P&L Data', ha='center', va='center', fontsize=12)
                ax_pie.set_title('P&L Contribution', fontsize=14, fontweight='bold', pad=10)
        else:
            ax_pie.text(0.5, 0.5, 'No Trades', ha='center', va='center', fontsize=12)
            ax_pie.set_title('P&L Contribution', fontsize=14, fontweight='bold', pad=10)
        
        # 4. INSTRUMENT PERFORMANCE TABLE (bottom right)
        ax_inst = fig.add_subplot(gs[2, 2])
        ax_inst.axis('off')
        
        if len(results['by_instrument']) > 0:
            inst_data = []
            inst_data.append(['Inst', 'Trades', 'Win%', 'P&L'])
            
            sorted_inst = sorted(results['by_instrument'].items(), 
                                key=lambda x: x[1]['total_pnl'], reverse=True)
            
            for name, stats in sorted_inst:
                pnl_str = f'€{stats["total_pnl"]:,.0f}'
                inst_data.append([
                    name,
                    str(stats['trades']),
                    f'{stats["win_rate"]:.0f}%',
                    pnl_str
                ])
            
            table2 = ax_inst.table(cellText=inst_data,
                                  colWidths=[0.3, 0.2, 0.2, 0.3],
                                  cellLoc='center',
                                  loc='center',
                                  bbox=[0, 0, 1, 1])
            
            table2.auto_set_font_size(False)
            table2.set_fontsize(10)
            table2.scale(1, 2.2)
            
            for j in range(4):
                cell = table2[(0, j)]
                cell.set_facecolor('#2E86AB')
                cell.set_text_props(color='white', weight='bold')
            
            for i in range(1, len(inst_data)):
                pnl_val = results['by_instrument'][inst_data[i][0]]['total_pnl']
                
                for j in range(4):
                    cell = table2[(i, j)]
                    if j == 3:
                        if pnl_val > 0:
                            cell.set_facecolor('#D4EDDA')
                            cell.set_text_props(color='green', weight='bold')
                        elif pnl_val < 0:
                            cell.set_facecolor('#F8D7DA')
                            cell.set_text_props(color='red', weight='bold')
                    else:
                        if i % 2 == 0:
                            cell.set_facecolor('#F8F9FA')
            
            ax_inst.set_title('Performance by Instrument', fontsize=14, fontweight='bold', pad=10)
        else:
            ax_inst.text(0.5, 0.5, 'No Data', ha='center', va='center', fontsize=12)
            ax_inst.set_title('Performance by Instrument', fontsize=14, fontweight='bold', pad=10)
        
        plt.tight_layout()
        plt.savefig(save_path, dpi=300, bbox_inches='tight', facecolor='white')
        print(f"\n✓ Saved dashboard: {save_path}")
        plt.close()
    
    def print_viability_assessment(self, results, metrics):
        """Print system viability assessment"""
        
        print("\n" + "="*100)
        print(" "*35 + "SYSTEM VIABILITY ASSESSMENT")
        print("="*100)
        
        score = 0
        max_score = 100
        
        if results['return_pct'] > 50:
            score += 20
            profit_msg = "✓ EXCELLENT: Strong returns (>50%)"
        elif results['return_pct'] > 20:
            score += 15
            profit_msg = "✓ GOOD: Positive returns (20-50%)"
        elif results['return_pct'] > 0:
            score += 10
            profit_msg = "⚠ MARGINAL: Barely profitable (0-20%)"
        else:
            profit_msg = "✗ POOR: Negative returns"
        
        sharpe = metrics.get('sharpe_ratio', 0)
        if sharpe > 2:
            score += 20
            sharpe_msg = "✓ EXCELLENT: Sharpe >2"
        elif sharpe > 1:
            score += 15
            sharpe_msg = "✓ GOOD: Sharpe >1"
        elif sharpe > 0.5:
            score += 10
            sharpe_msg = "⚠ ACCEPTABLE: Sharpe 0.5-1"
        else:
            sharpe_msg = "✗ POOR: Sharpe <0.5"
        
        max_dd = results['max_drawdown_pct']
        if max_dd < 10:
            score += 20
            dd_msg = "✓ EXCELLENT: Max DD <10%"
        elif max_dd < 20:
            score += 15
            dd_msg = "✓ GOOD: Max DD 10-20%"
        elif max_dd < 30:
            score += 10
            dd_msg = "⚠ ACCEPTABLE: Max DD 20-30%"
        else:
            dd_msg = "✗ POOR: Max DD >30%"
        
        win_rate = results.get('win_rate', 0)
        if win_rate > 60:
            score += 20
            wr_msg = "✓ EXCELLENT: Win Rate >60%"
        elif win_rate > 50:
            score += 15
            wr_msg = "✓ GOOD: Win Rate >50%"
        elif win_rate > 40:
            score += 10
            wr_msg = "⚠ ACCEPTABLE: Win Rate 40-50%"
        else:
            wr_msg = "✗ POOR: Win Rate <40%"
        
        pf = results.get('profit_factor', 0)
        if pf > 2:
            score += 20
            pf_msg = "✓ EXCELLENT: Profit Factor >2"
        elif pf > 1.5:
            score += 15
            pf_msg = "✓ GOOD: Profit Factor >1.5"
        elif pf > 1:
            score += 10
            pf_msg = "⚠ ACCEPTABLE: Profit Factor >1"
        else:
            pf_msg = "✗ POOR: Profit Factor <1"
        
        print(f"\n  {profit_msg}")
        print(f"  {sharpe_msg}")
        print(f"  {dd_msg}")
        print(f"  {wr_msg}")
        print(f"  {pf_msg}")
        
        print(f"\n  {'='*96}")
        print(f"  OVERALL SCORE: {score}/{max_score} ({score/max_score*100:.0f}%)")
        print(f"  {'='*96}")
        
        if score >= 85:
            verdict = "🚀 HIGHLY VIABLE - Excellent system"
        elif score >= 70:
            verdict = "✓ VIABLE - Good system"
        elif score >= 50:
            verdict = "⚠ MARGINAL - Needs optimization"
        else:
            verdict = "✗ NOT VIABLE - Major overhaul needed"
        
        print(f"\n  VERDICT: {verdict}")
        print("="*100 + "\n")


def run_chronological_portfolio(starting_balance=100000, start_date='2020-01-01', 
                                end_date='2024-12-31', verbose=False,
                                vix_filter=None):  # ← ADDED: optional vix_filter parameter
    """Run chronological portfolio backtest with FX support"""
    
    INSTRUMENTS = [
        {
            'name': 'FDAX',
            'data_file': 'FDAX_1min.txt',
            'instrument_type': 'FUTURE',
            'point_value': 25.0,
            'pip_size': 1.0,
            'initial_margin_pct': 10.0,
            'maintenance_margin_pct': 7.5,
            'or_start_hour': 2,
            'or_start_minute': 0,
            'or_end_hour': 3,
            'or_end_minute': 0,
            'trend_check_hour': 3,
            'trend_check_minute': 0,
            'trading_start_hour': 3,
            'trading_start_minute': 0,
            'trading_end_hour': 10,
            'trading_end_minute': 0,
            'trading_end_next_day': False,
            'min_or_range': 10.0,
            'profit_target_multiplier': 2.0
        },
        {
            'name': 'ES',
            'data_file': 'ES_SP500_Mini_Futures_1min.txt',
            'instrument_type': 'FUTURE',
            'point_value': 50.0,
            'pip_size': 1.0,
            'initial_margin_pct': 10.0,
            'maintenance_margin_pct': 7.5,
            'or_start_hour': 8,
            'or_start_minute': 45,
            'or_end_hour': 9,
            'or_end_minute': 30,
            'trend_check_hour': 9,
            'trend_check_minute': 30,
            'trading_start_hour': 9,
            'trading_start_minute': 30,
            'trading_end_hour': 13,
            'trading_end_minute': 0,
            'trading_end_next_day': False,
            'min_or_range': 8.0,
            'profit_target_multiplier': 1.0
        },
        {
            'name': 'NKD',
            'data_file': 'NKD_1min.txt',
            'instrument_type': 'FUTURE',
            'point_value': 5.0,
            'pip_size': 1.0,
            'initial_margin_pct': 10.0,
            'maintenance_margin_pct': 7.5,
            'or_start_hour': 8,
            'or_start_minute': 45,
            'or_end_hour': 9,
            'or_end_minute': 30,
            'trend_check_hour': 9,
            'trend_check_minute': 30,
            'trading_start_hour': 9,
            'trading_start_minute': 30,
            'trading_end_hour': 13,
            'trading_end_minute': 0,
            'trading_end_next_day': False,
            'min_or_range': 50.0,
            'profit_target_multiplier': 1.0
        },
        {
            'name': 'GC',
            'data_file': 'GC_1min.txt',
            'instrument_type': 'FUTURE',
            'point_value': 100.0,
            'pip_size': 1.0,
            'initial_margin_pct': 8.0,
            'maintenance_margin_pct': 6.0,
            'or_start_hour': 8,
            'or_start_minute': 30,
            'or_end_hour': 9,
            'or_end_minute': 30,
            'trend_check_hour': 9,
            'trend_check_minute': 30,
            'trading_start_hour': 9,
            'trading_start_minute': 30,
            'trading_end_hour': 13,
            'trading_end_minute': 0,
            'trading_end_next_day': False,
            'min_or_range': 2.0,
            'profit_target_multiplier': 1.0
        },
        {
            'name': 'NG',
            'data_file': 'NG_1min.txt',
            'instrument_type': 'FUTURE',
            'point_value': 10000.0,
            'pip_size': 1.0,
            'initial_margin_pct': 10.0,
            'maintenance_margin_pct': 7.5,
            'or_start_hour': 8,
            'or_start_minute': 45,
            'or_end_hour': 9,
            'or_end_minute': 30,
            'trend_check_hour': 9,
            'trend_check_minute': 30,
            'trading_start_hour': 9,
            'trading_start_minute': 30,
            'trading_end_hour': 13,
            'trading_end_minute': 0,
            'trading_end_next_day': False,
            'min_or_range': 0.05,
            'profit_target_multiplier': 2.0
        },
        {
            'name': 'CL',
            'data_file': 'CL_1min.txt',
            'instrument_type': 'FUTURE',
            'point_value': 1000.0,
            'pip_size': 1.0,
            'initial_margin_pct': 10.0,
            'maintenance_margin_pct': 7.5,
            'or_start_hour': 8,
            'or_start_minute': 45,
            'or_end_hour': 9,
            'or_end_minute': 30,
            'trend_check_hour': 9,
            'trend_check_minute': 30,
            'trading_start_hour': 9,
            'trading_start_minute': 30,
            'trading_end_hour': 13,
            'trading_end_minute': 0,
            'trading_end_next_day': False,
            'min_or_range': 0.50,
            'profit_target_multiplier': 1.5
        },
        {
            'name': 'RTY',
            'data_file': 'RTY_1min.txt',
            'instrument_type': 'FUTURE',
            'point_value': 50.0,
            'pip_size': 0.10,
            'initial_margin_pct': 10.0,
            'maintenance_margin_pct': 7.5,
            'or_start_hour': 8,
            'or_start_minute': 45,
            'or_end_hour': 9,
            'or_end_minute': 30,
            'trend_check_hour': 9,
            'trend_check_minute': 30,
            'trading_start_hour': 9,
            'trading_start_minute': 30,
            'trading_end_hour': 13,
            'trading_end_minute': 0,
            'trading_end_next_day': False,
            'min_or_range': 3.0,
            'profit_target_multiplier': 1.2
        },
        {
            'name': 'SI',
            'data_file': 'SI_1min.txt',
            'instrument_type': 'FUTURE',
            'point_value': 5000.0,
            'pip_size': 0.005,
            'initial_margin_pct': 12.0,
            'maintenance_margin_pct': 9.0,
            'or_start_hour': 8,
            'or_start_minute': 30,
            'or_end_hour': 9,
            'or_end_minute': 30,
            'trend_check_hour': 9,
            'trend_check_minute': 30,
            'trading_start_hour': 9,
            'trading_start_minute': 30,
            'trading_end_hour': 13,
            'trading_end_minute': 0,
            'trading_end_next_day': False,
            'min_or_range': 0.10,
            'profit_target_multiplier': 1.8
        },
        {
            'name': 'PL',
            'data_file': 'PL_1min.txt',
            'instrument_type': 'FUTURE',
            'point_value': 50.0,
            'pip_size': 0.10,
            'initial_margin_pct': 12.0,
            'maintenance_margin_pct': 9.0,
            'or_start_hour': 8,
            'or_start_minute': 30,
            'or_end_hour': 9,
            'or_end_minute': 30,
            'trend_check_hour': 9,
            'trend_check_minute': 30,
            'trading_start_hour': 9,
            'trading_start_minute': 30,
            'trading_end_hour': 13,
            'trading_end_minute': 0,
            'trading_end_next_day': False,
            'min_or_range': 5.0,
            'profit_target_multiplier': 1.5
        },
        {
            'name': 'MME',
            'data_file': 'MME_1min.txt',
            'instrument_type': 'FUTURE',
            'point_value': 50.0,
            'pip_size': 0.10,
            'initial_margin_pct': 10.0,
            'maintenance_margin_pct': 7.5,
            'or_start_hour': 8,
            'or_start_minute': 45,
            'or_end_hour': 9,
            'or_end_minute': 30,
            'trend_check_hour': 9,
            'trend_check_minute': 30,
            'trading_start_hour': 9,
            'trading_start_minute': 30,
            'trading_end_hour': 13,
            'trading_end_minute': 0,
            'trading_end_next_day': False,
            'min_or_range': 4.0,
            'profit_target_multiplier': 1.0
        },
        {
            'name': 'EURUSD',
            'data_file': 'EURUSD_1min.txt',
            'instrument_type': 'FX',
            'contract_size': 100000,
            'pip_size': 0.0001,
            'base_currency': 'EUR',
            'quote_currency': 'USD',
            'initial_margin_pct': 2.0,
            'maintenance_margin_pct': 1.5,
            'or_start_hour': 18,
            'or_start_minute': 0,
            'or_end_hour': 19,
            'or_end_minute': 0,
            'trend_check_hour': 19,
            'trend_check_minute': 0,
            'trading_start_hour': 19,
            'trading_start_minute': 0,
            'trading_end_hour': 23,
            'trading_end_minute': 0,
            'trading_end_next_day': False,
            'min_or_range': 0.0020,
            'profit_target_multiplier': 1.5
        },
        {
            'name': 'USDJPY',
            'data_file': 'USDJPY_1min.txt',
            'instrument_type': 'FX',
            'contract_size': 100000,
            'pip_size': 0.01,
            'base_currency': 'USD',
            'quote_currency': 'JPY',
            'initial_margin_pct': 2.0,
            'maintenance_margin_pct': 1.5,
            'or_start_hour': 18,
            'or_start_minute': 0,
            'or_end_hour': 19,
            'or_end_minute': 0,
            'trend_check_hour': 19,
            'trend_check_minute': 0,
            'trading_start_hour': 19,
            'trading_start_minute': 0,
            'trading_end_hour': 23,
            'trading_end_minute': 0,
            'trading_end_next_day': False,
            'min_or_range': 0.20,
            'profit_target_multiplier': 2.0
        }
    ]
    
    backtester = ChronologicalPortfolioBacktester(
        instruments_config=INSTRUMENTS,
        starting_balance=starting_balance,
        start_date=start_date,
        end_date=end_date,
        verbose=verbose,
        vix_filter=vix_filter  # ← ADDED: passed through to backtester
    )
    
    results = backtester.run()
    
    print("\n" + "="*80)
    print("PORTFOLIO RESULTS")
    print("="*80)
    
    print(f"\n💰 Performance:")
    print(f"  Starting: €{results['starting_balance']:,.0f}")
    print(f"  Final: €{results['final_balance']:,.0f}")
    print(f"  Return: {results['return_pct']:+.2f}%")
    print(f"  Max DD: {results['max_drawdown_pct']:.2f}%")
    
    if 'cagr' in results:
        print(f"\n📈 Time-Adjusted:")
        print(f"  CAGR: {results['cagr']:.2f}%")
    
    if len(results['all_trades']) > 0:
        print(f"\n📊 Trades:")
        print(f"  Total: {results['total_trades']}")
        print(f"  Win Rate: {results['win_rate']:.1f}%")
        if results['profit_factor'] != float('inf'):
            print(f"  Profit Factor: {results['profit_factor']:.2f}")
        
        print(f"\n📈 By Instrument:")
        print(f"  {'Instrument':<12} {'Trades':>8} {'Win%':>8} {'P&L':>15} {'PF':>8}")
        print(f"  {'-'*12} {'-'*8} {'-'*8} {'-'*15} {'-'*8}")
        
        total_pnl = 0
        for name in sorted(results['by_instrument'].keys()):
            stats = results['by_instrument'][name]
            total_pnl += stats['total_pnl']
            pf = f"{stats['profit_factor']:.2f}" if stats['profit_factor'] != float('inf') else "∞"
            print(f"  {name:<12} {stats['trades']:>8} {stats['win_rate']:>7.1f}% €{stats['total_pnl']:>13,.0f} {pf:>8}")
        
        print(f"\n💰 Contribution:")
        sorted_pnl = sorted(results['by_instrument'].items(), key=lambda x: x[1]['total_pnl'], reverse=True)
        for name, stats in sorted_pnl:
            pct = (stats['total_pnl'] / total_pnl * 100) if total_pnl != 0 else 0
            print(f"  {name:<12} €{stats['total_pnl']:>12,.0f}  ({pct:>6.1f}%)")
        
        trades_filename = f'portfolio_trades_{start_date}_to_{end_date}.csv'
        results['all_trades'].to_csv(trades_filename, index=False)
        print(f"\n✓ Saved: {trades_filename}")
    
    print("\n" + "="*80)
    print("GENERATING ANALYSIS")
    print("="*80)
    
    metrics = backtester.calculate_advanced_metrics(results)
    
    print(f"\n📊 Risk-Adjusted:")
    print(f"  Sharpe:     {metrics.get('sharpe_ratio', 0):>8.2f}")
    print(f"  Sortino:    {metrics.get('sortino_ratio', 0):>8.2f}")
    print(f"  Calmar:     {metrics.get('calmar_ratio', 0):>8.2f}")

    # Generate dashboard named by timeframe
    dashboard_name = f'performance_dashboard_{start_date}_to_{end_date}.png'
    backtester.plot_simple_balance_chart(results, dashboard_name)
    backtester.print_viability_assessment(results, metrics)
    
    print("="*80 + "\n")
    
    return results, backtester


if __name__ == "__main__":
    from vix_filter import VIXFilter

    # ── Without VIX filter — original behaviour, nothing changes ─────────────
    # results, backtester = run_chronological_portfolio(
    #     starting_balance=100000,
    #     start_date='2024-01-01',
    #     end_date='2024-12-31',
    #     verbose=True
    # )

    # ── With VIX filter: only trade when VIX >= 20 ───────────────────────────
    vix = VIXFilter(
        vix_file   = 'VX_1min.txt',
        hypothesis = 'rising_floor',
        roc_period = 10,
        floor      = 15.0,
    )

    results, backtester = run_chronological_portfolio(
        starting_balance=100000,
        start_date='2016-01-01',
        end_date='2016-12-31',
        verbose=True,
        vix_filter=None
    )