import pandas as pd
import numpy as np
from datetime import datetime, time, timedelta
import matplotlib.pyplot as plt
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import warnings
import webbrowser
import os
warnings.filterwarnings('ignore')

def setup_chrome_browser():
    """Setup Chrome as the default browser for Plotly charts"""
    chrome_path = None
    
    possible_paths = [
        r'C:\Program Files\Google\Chrome\Application\chrome.exe',
        r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
        os.path.expanduser(r'~\AppData\Local\Google\Chrome\Application\chrome.exe')
    ]
    
    for path in possible_paths:
        if os.path.exists(path):
            chrome_path = path
            break
    
    if chrome_path:
        webbrowser.register('chrome', None, webbrowser.BackgroundBrowser(chrome_path))
        import plotly.io as pio
        pio.renderers.default = "browser"
        print(f"✓ Chrome configured at: {chrome_path}")
        return True
    else:
        print("⚠ Chrome not found, using system default browser")
        import plotly.io as pio
        pio.renderers.default = "browser"
        return False

setup_chrome_browser()

from data_loader import load_and_create_resampled
from sq60_signals import SQ60Detector


class WeightedConfluenceChecker:
    """
    Weighted multi-timeframe confluence scoring system
    
    Uses weighted combination of 15min, 1H, 4H trends:
    C = (1 × s_15min) + (2 × s_1h) + (3 × s_4h)
    
    where s_i ∈ {-1, 0, +1}:
    - +1 = bullish (SQ_LONG)
    - -1 = bearish (SQ_SHORT)
    -  0 = neutral (OFF)
    
    Maps |C| to risk multiplier:
    - |C| = 0-1: 0.5× (weak/conflict)
    - |C| = 2-3: 1.0× (moderate)
    - |C| = 4-5: 1.5× (strong)
    - |C| = 6:   2.0× (perfect alignment)
    """
    
    def __init__(self, minute_data, verbose=False):
        """
        Args:
            minute_data: Full minute-level DataFrame
            verbose: Print debug info
        """
        self.minute_data = minute_data
        self.verbose = verbose
        
        # Cache for 15min checks
        self.cache_15min = {}
        
        # Cache for alignment checks
        self.alignment_cache = {}
    
    def get_trend_value(self, trend_state):
        """
        Convert trend state to numeric value
        
        Args:
            trend_state: 'SQ_LONG', 'SQ_SHORT', or 'OFF'
        
        Returns:
            +1, -1, or 0
        """
        if trend_state == 'SQ_LONG':
            return +1
        elif trend_state == 'SQ_SHORT':
            return -1
        else:  # 'OFF'
            return 0
    
    def check_15min_trend(self, timestamp):
        """
        Check 15min trend state at given timestamp
        Uses 3-day lookback to build temporary detector
        
        Args:
            timestamp: Current timestamp
        
        Returns:
            tuple: (trend_value, detail) where trend_value is -1, 0, or +1
        """
        # Check cache
        if timestamp in self.cache_15min:
            return self.cache_15min[timestamp]
        
        # 288 bars = 3 days
        lookback_bars = 288
        timeframe = '15T'
        minutes = 15
        lookback_duration = pd.Timedelta(minutes=minutes * lookback_bars)
        
        # Get data window
        start_time = timestamp - lookback_duration
        window_data = self.minute_data[(self.minute_data.index >= start_time) & 
                                       (self.minute_data.index <= timestamp)]
        
        if len(window_data) == 0:
            result = (0, "no_data")
            self.cache_15min[timestamp] = result
            return result
        
        try:
            # Resample to 15min
            resampled = window_data.resample(timeframe).agg({
                'open': 'first',
                'high': 'max',
                'low': 'min',
                'close': 'last',
                'volume': 'sum'
            }).dropna()
            
            if len(resampled) < 10:
                result = (0, f"insufficient_bars_{len(resampled)}")
                self.cache_15min[timestamp] = result
                return result
            
            # Create detector
            detector = SQ60Detector(timeframe=timeframe, lookback_bars=lookback_bars)
            
            # Feed bars
            for bar_ts, bar_data in resampled.iterrows():
                detector.update(
                    timestamp=bar_ts,
                    open_price=bar_data['open'],
                    high=bar_data['high'],
                    low=bar_data['low'],
                    close=bar_data['close']
                )
            
            # Process remaining minutes
            last_bar_end = resampled.index[-1]
            bar_duration = pd.Timedelta(minutes=15)
            bar_start = last_bar_end + bar_duration
            minute_bars = window_data[(window_data.index >= bar_start) & 
                                     (window_data.index <= timestamp)]
            
            for minute_ts, minute_bar in minute_bars.iterrows():
                detector.check_minute_trigger(minute_ts, minute_bar['close'])
            
            # Get trend and convert to value
            trend = detector.get_trend_state()
            value = self.get_trend_value(trend)
            
            result = (value, f"trend={trend}")
            self.cache_15min[timestamp] = result
            return result
            
        except Exception as e:
            result = (0, f"error_{str(e)[:20]}")
            self.cache_15min[timestamp] = result
            return result
    
    def calculate_confluence(self, timestamp, trend_1h, trend_4h):
        """
        Calculate weighted confluence score and risk multiplier
        
        Args:
            timestamp: Current timestamp (for 15min check)
            trend_1h: 1H trend state ('SQ_LONG', 'SQ_SHORT', 'OFF')
            trend_4h: 4H trend state ('SQ_LONG', 'SQ_SHORT', 'OFF')
        
        Returns:
            dict: {
                'confluence': -6 to +6,
                'multiplier': 0.5 to 2.0,
                'should_trade': bool,
                'direction': 'LONG', 'SHORT', or None,
                'details': {...}
            }
        """
        # Get trend values
        s_1h = self.get_trend_value(trend_1h)
        s_4h = self.get_trend_value(trend_4h)
        
        # Check 15min (on-demand)
        s_15min, detail_15min = self.check_15min_trend(timestamp)
        
        # Calculate weighted confluence
        # New scheme: 1H is primary (weight=3), 15min and 4H are secondary (weight=1 each)
        C = (1 * s_15min) + (3 * s_1h) + (1 * s_4h)
        abs_C = abs(C)
        
        # Map |C| to multiplier
        # Only trade when 1H is trending (|C| >= 3)
        if abs_C < 3:
            multiplier = 0.0  # Don't trade - 1H not trending
        elif abs_C == 3:
            multiplier = 1.0  # 1H only
        elif abs_C == 4:
            multiplier = 2.0  # 1H + one other timeframe
        elif abs_C >= 5:
            multiplier = 3.0  # 1H + both other timeframes (perfect alignment)
        else:
            multiplier = 0.0
        
        # Determine direction and whether to trade
        if C > 0:
            direction = 'LONG'
        elif C < 0:
            direction = 'SHORT'
        else:
            direction = None
        
        # Trading rules:
        # 1. Only trade when 1H is active (|C| >= 3)
        # 2. Don't trade if C = 0 (no signal)
        # 3. Optional: Don't trade against 4H if you want strict 4H filter
        should_trade = True
        reason = "OK"
        
        if C == 0:
            should_trade = False
            reason = "NO_SIGNAL"
        elif abs_C < 3:
            should_trade = False
            reason = "1H_NOT_TRENDING"
        # Uncomment below if you want to enforce 4H alignment when 4H is active
        # elif s_4h != 0 and s_4h != (1 if C > 0 else -1):
        #     should_trade = False
        #     reason = "AGAINST_4H"
        
        result = {
            'confluence': C,
            'abs_confluence': abs_C,
            'multiplier': multiplier,
            'should_trade': should_trade,
            'direction': direction,
            'reason': reason,
            'details': {
                '15min': f"{'LONG' if s_15min > 0 else 'SHORT' if s_15min < 0 else 'OFF'} ({detail_15min})",
                '1H': f"{'LONG' if s_1h > 0 else 'SHORT' if s_1h < 0 else 'OFF'}",
                '4H': f"{'LONG' if s_4h > 0 else 'SHORT' if s_4h < 0 else 'OFF'}",
                's_15min': s_15min,
                's_1h': s_1h,
                's_4h': s_4h
            }
        }
        
        return result


class ProperSQ60ORBWithPlots:
    """
    Multi-timeframe SQ60+ORB Strategy
    - Runs 1H SQ60 full-time as main trend detector
    - On 1H entry signal: checks 15min and 4H trend alignment
    - Position sizing: 1x (1H only), 2x (1H + one other), 3x (all aligned)
    """
    
    def __init__(self, data_file='FDAX_1min.txt', 
                 starting_balance=100000,
                 risk_per_trade_pct=2.0,
                 profit_target_multiplier=2.0,
                 min_or_range=10,
                 start_date=None, 
                 end_date=None,
                 verbose=True,
                 initial_margin_pct=10.0,
                 maintenance_margin_pct=7.5,
                 point_value=25,
                 pip_size=1.0,
                 instrument_name='FDAX',
                 instrument_type='FUTURE',
                 contract_size=None,
                 or_start_hour=2,
                 or_start_minute=0,
                 or_end_hour=3,
                 or_end_minute=0,
                 trend_check_hour=3,
                 trend_check_minute=0,
                 trading_start_hour=3,
                 trading_start_minute=0,
                 trading_end_hour=10,
                 trading_end_minute=30,
                 trading_end_next_day=False,
                 main_timeframe='1H',
                 enable_multi_timeframe=True):
        
        self.starting_balance = starting_balance
        self.current_balance = starting_balance
        self.risk_per_trade_pct = risk_per_trade_pct
        self.profit_target_multiplier = profit_target_multiplier
        self.min_or_range = min_or_range
        self.verbose = verbose
        self.instrument_name = instrument_name
        self.instrument_type = instrument_type
        self.main_timeframe = main_timeframe
        self.enable_multi_timeframe = enable_multi_timeframe
        
        self.initial_margin_pct = initial_margin_pct
        self.maintenance_margin_pct = maintenance_margin_pct
        self.point_value = point_value
        self.pip_size = pip_size
        self.contract_size = contract_size if contract_size else 1
        
        self.or_start_hour = or_start_hour
        self.or_start_minute = or_start_minute
        self.or_end_hour = or_end_hour
        self.or_end_minute = or_end_minute
        self.trend_check_hour = trend_check_hour
        self.trend_check_minute = trend_check_minute
        self.trading_start_hour = trading_start_hour
        self.trading_start_minute = trading_start_minute
        self.trading_end_hour = trading_end_hour
        self.trading_end_minute = trading_end_minute
        self.trading_end_next_day = trading_end_next_day
        
        self.peak_balance = starting_balance
        self.max_drawdown_eur = 0
        self.max_drawdown_pct = 0
        self.balance_history = []
        
        print(f"Loading data for {instrument_name} on {main_timeframe} timeframe...")
        self.minute_data, self.resampled_data = load_and_create_resampled(data_file, main_timeframe)
        
        if start_date:
            start_date = pd.to_datetime(start_date)
            self.minute_data = self.minute_data[self.minute_data.index >= start_date]
            self.resampled_data = self.resampled_data[self.resampled_data.index >= start_date]
        if end_date:
            end_date = pd.to_datetime(end_date)
            self.minute_data = self.minute_data[self.minute_data.index <= end_date]
            self.resampled_data = self.resampled_data[self.resampled_data.index <= end_date]
        
        self.start_date = self.resampled_data.index.min()
        self.end_date = self.resampled_data.index.max()
        
        print(f"Data: {len(self.minute_data):,} minutes, {len(self.resampled_data):,} {main_timeframe} bars")
        print(f"Range: {self.minute_data.index.min().date()} to {self.minute_data.index.max().date()}")
        
        # Main 1H SQ60 detector (runs full-time)
        self.sq60 = SQ60Detector(timeframe=main_timeframe)
        
        # Initialize multi-timeframe components
        if self.enable_multi_timeframe:
            # 4H detector runs continuously (cheap - only ~2.5k bars for 5 years)
            print(f"✓ Creating 4H detector (continuous)...")
            _, self.resampled_4h = load_and_create_resampled(data_file, '4H')
            
            # Apply same date filters to 4H data
            if start_date:
                self.resampled_4h = self.resampled_4h[self.resampled_4h.index >= start_date]
            if end_date:
                self.resampled_4h = self.resampled_4h[self.resampled_4h.index <= end_date]
            
            self.sq60_4h = SQ60Detector(timeframe='4H')
            print(f"  4H bars: {len(self.resampled_4h):,}")
            
            # Weighted confluence checker (checks 15min on-demand, combines with 1H/4H)
            self.confluence_checker = WeightedConfluenceChecker(self.minute_data, verbose=verbose)
            print(f"✓ Weighted confluence system enabled (15min + 1H + 4H)")
            print(f"  Multipliers: 0.5× (weak) → 1.0× (moderate) → 1.5× (strong) → 2.0× (perfect)")
        else:
            self.sq60_4h = None
            self.confluence_checker = None
            print(f"⚠ Multi-timeframe alignment disabled (1H only)")
        
        self.trades = []
        self.current_position = None
        self.daily_summaries = []
        
        self.or_levels = []
        self.trend_markers = []
        self.trade_annotations = []
        
        self.traded_days = set()
        
        self.trend_at_check_by_date = {}
        
        # Track multiplier distribution (now includes fractional values)
        self.multiplier_stats = {}
    
    def calculate_pip_value(self, current_price):
        """Calculate pip value dynamically based on instrument type"""
        if self.instrument_type == 'FUTURE':
            return self.point_value
        elif self.instrument_type == 'FX':
            if 'JPY' in self.instrument_name:
                pip_value = (self.pip_size * self.contract_size) / current_price
            else:
                pip_value = self.pip_size * self.contract_size
            return pip_value
        return self.point_value
    
    def get_trading_session_date(self, timestamp):
        """Determine which trading session a timestamp belongs to"""
        current_time = timestamp.time()
        current_date = timestamp.date()
        
        if not self.trading_end_next_day:
            return current_date
        else:
            session_start_time = time(self.or_start_hour, self.or_start_minute)
            
            if current_time < session_start_time:
                return current_date - timedelta(days=1)
            else:
                return current_date
    
    def calculate_contract_value(self, price):
        """Calculate the notional value of one contract"""
        if self.instrument_type == 'FUTURE':
            return price * self.point_value
        elif self.instrument_type == 'FX':
            return self.contract_size
        else:
            return price * self.point_value
    
    def calculate_margin_required(self, price, contracts):
        """Calculate initial and maintenance margin requirements"""
        contract_value = self.calculate_contract_value(price)
        
        initial_margin_per_contract = contract_value * (self.initial_margin_pct / 100)
        maintenance_margin_per_contract = contract_value * (self.maintenance_margin_pct / 100)
        
        initial_margin = initial_margin_per_contract * contracts
        maintenance_margin = maintenance_margin_per_contract * contracts
        
        return initial_margin, maintenance_margin
    
    def calculate_position_size(self, entry, stop, multiplier=1):
        """
        Calculate position size with multi-timeframe alignment multiplier
        
        Args:
            entry: Entry price
            stop: Stop loss price
            multiplier: 1, 2, or 3 based on trend alignment
        
        Returns:
            contracts: Number of contracts to trade
        """
        risk_per_point = abs(entry - stop)
        pips_at_risk = risk_per_point / self.pip_size
        
        pip_value_at_entry = self.calculate_pip_value(entry)
        risk_per_contract = pips_at_risk * pip_value_at_entry
        
        # Base risk calculation (for 1x)
        max_risk_eur = self.current_balance * (self.risk_per_trade_pct / 100)
        base_contracts = int(max_risk_eur / risk_per_contract)
        
        # Apply alignment multiplier
        contracts = base_contracts * multiplier
        
        # Check margin constraints
        initial_margin_per_contract, _ = self.calculate_margin_required(entry, 1)
        available_for_margin = self.current_balance * 0.80
        contracts_by_margin = int(available_for_margin / initial_margin_per_contract)
        
        # Take minimum of risk-based and margin-based
        contracts = min(contracts, contracts_by_margin)
        
        # Ensure at least 1 contract
        contracts = max(1, contracts)
        
        return contracts
    
    def check_margin_call(self, current_price):
        """Check if current position would trigger a margin call"""
        if self.current_position is None:
            return False, 0, 0
        
        pos = self.current_position
        
        if pos['direction'] == 'LONG':
            price_change = current_price - pos['entry_price']
        else:
            price_change = pos['entry_price'] - current_price
        
        pips = price_change / self.pip_size
        
        pip_value = self.calculate_pip_value(current_price)
        unrealized_pnl = pips * pip_value * pos['contracts']
        
        current_equity = self.current_balance + unrealized_pnl
        _, maintenance_margin = self.calculate_margin_required(pos['entry_price'], pos['contracts'])
        
        if current_equity < maintenance_margin:
            return True, current_equity, maintenance_margin
        
        return False, current_equity, maintenance_margin
    
    def update_drawdown(self, current_equity=None):
        """Track maximum drawdown"""
        balance_to_check = current_equity if current_equity is not None else self.current_balance
        
        if balance_to_check > self.peak_balance:
            self.peak_balance = balance_to_check
        
        drawdown_eur = self.peak_balance - balance_to_check
        drawdown_pct = (drawdown_eur / self.peak_balance) * 100 if self.peak_balance > 0 else 0
        
        if drawdown_eur > self.max_drawdown_eur:
            self.max_drawdown_eur = drawdown_eur
            self.max_drawdown_pct = drawdown_pct
    
    def is_in_trading_window(self, timestamp):
        """Check if timestamp is within trading window"""
        hour = timestamp.hour
        minute = timestamp.minute
        
        current_time = hour * 60 + minute
        start_time = self.trading_start_hour * 60 + self.trading_start_minute
        end_time = self.trading_end_hour * 60 + self.trading_end_minute
        
        if self.trading_end_next_day:
            return current_time >= start_time or current_time <= end_time
        else:
            return start_time <= current_time <= end_time
    
    def is_time_stop(self, timestamp):
        """Check if this is the exact time stop moment"""
        return (timestamp.hour == self.trading_end_hour and 
                timestamp.minute == self.trading_end_minute)
    
    def calculate_or(self, day_data):
        """Calculate Opening Range"""
        or_start_minutes = self.or_start_hour * 60 + self.or_start_minute
        or_end_minutes = self.or_end_hour * 60 + self.or_end_minute
        
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
        
        if (or_high - or_low) < self.min_or_range:
            return None, None
        
        return or_high, or_low
    
    def is_trend_confirmed(self, current_time):
        """Check if current trend is fully confirmed"""
        if self.sq60.trend == "OFF":
            return False
        
        if len(self.sq60.swing_points) < 4:
            return False
        
        last_4_swings = self.sq60.swing_points[-4:]
        confirmation_bars = 7
        
        if self.main_timeframe.endswith('T') or self.main_timeframe.endswith('min'):
            minutes = int(self.main_timeframe.replace('T', '').replace('min', ''))
            bar_duration = pd.Timedelta(minutes=minutes)
        elif self.main_timeframe.endswith('H'):
            hours = int(self.main_timeframe.replace('H', ''))
            bar_duration = pd.Timedelta(hours=hours)
        else:
            bar_duration = pd.Timedelta(hours=1)
        
        for swing_ts, swing_price, swing_type in last_4_swings:
            confirm_time = swing_ts + (bar_duration * confirmation_bars)
            
            if confirm_time > current_time:
                if self.verbose:
                    bars_remaining = (confirm_time - current_time) / bar_duration
                    print(f"    ⚠ Pattern not confirmed yet - {swing_type} at {swing_ts.strftime('%m-%d %H:%M')} needs {bars_remaining:.1f} more bars")
                return False
        
        return True
    
    def run(self):
        print("\n" + "="*80)
        print(f"WEIGHTED CONFLUENCE SQ60 + ORB STRATEGY - {self.instrument_name}")
        print("="*80)
        
        print(f"\n🎯 Weighted Confluence Configuration:")
        print(f"   Main Timeframe: {self.main_timeframe}")
        if self.enable_multi_timeframe:
            print(f"   Confluence System: ENABLED (15min + 1H + 4H)")
            print(f"   Scoring: C = (1×15min) + (2×1H) + (3×4H)")
            print(f"   Position Sizing: 0.5× → 1.0× → 1.5× → 2.0×")
        else:
            print(f"   Confluence System: DISABLED (single timeframe only)")
        
        print(f"\n💰 Risk Management:")
        print(f"   Instrument Type: {self.instrument_type}")
        
        if self.instrument_type == 'FUTURE':
            print(f"   Point Value: €{self.point_value} (FIXED)")
        elif self.instrument_type == 'FX':
            print(f"   Contract Size: {self.contract_size:,} units")
            print(f"   Pip Value: DYNAMIC (varies with price)")
        
        print(f"   Initial Margin: {self.initial_margin_pct}% of contract value")
        print(f"   Maintenance Margin: {self.maintenance_margin_pct}% of contract value")
        print(f"   Pip Size: {self.pip_size}")
        print(f"   Risk per Trade: {self.risk_per_trade_pct}% of balance (base)")
        if self.enable_multi_timeframe:
            print(f"      → 1x alignment: {self.risk_per_trade_pct}% risk")
            print(f"      → 2x alignment: {self.risk_per_trade_pct * 2}% risk")
            print(f"      → 3x alignment: {self.risk_per_trade_pct * 3}% risk")
        print(f"   Starting Balance: €{self.starting_balance:,.0f}")
        
        print(f"\n⏰ Trading Hours:")
        print(f"   Opening Range: {self.or_start_hour:02d}:{self.or_start_minute:02d} - {self.or_end_hour:02d}:{self.or_end_minute:02d}")
        print(f"   Trend Check: {self.trend_check_hour:02d}:{self.trend_check_minute:02d}")
        if self.trading_end_next_day:
            print(f"   Trading Window: {self.trading_start_hour:02d}:{self.trading_start_minute:02d} - {self.trading_end_hour:02d}:{self.trading_end_minute:02d} (next day)")
        else:
            print(f"   Trading Window: {self.trading_start_hour:02d}:{self.trading_start_minute:02d} - {self.trading_end_hour:02d}:{self.trading_end_minute:02d}")
        
        self.minute_data['session_date'] = self.minute_data.index.map(self.get_trading_session_date)
        daily_minute_groups = dict(list(self.minute_data.groupby('session_date')))
        
        print("\nProcessing data chronologically...")
        print(f"⚠ CRITICAL RULE: Only the trend at {self.trend_check_hour:02d}:{self.trend_check_minute:02d} determines if we can trade that session\n")
        
        current_session = None
        day_info = None
        
        # Track which 4H bar we've processed (to avoid re-processing)
        last_processed_4h_idx = -1
        last_processed_minute_ts = None  # Track last minute we fed to 4H detector
        
        for bar_idx, (bar_ts, bar_data) in enumerate(self.resampled_data.iterrows()):
            # Update main 1H detector
            self.sq60.update(
                timestamp=bar_ts,
                open_price=bar_data['open'],
                high=bar_data['high'],
                low=bar_data['low'],
                close=bar_data['close']
            )
            
            # Update 4H detector if enabled (runs continuously)
            if self.enable_multi_timeframe and self.sq60_4h is not None:
                # First, process any new 4H bars
                bars_to_process = self.resampled_4h[self.resampled_4h.index <= bar_ts]
                
                for idx, (bar_4h_ts, bar_4h_data) in enumerate(bars_to_process.iterrows()):
                    if idx > last_processed_4h_idx:
                        self.sq60_4h.update(
                            timestamp=bar_4h_ts,
                            open_price=bar_4h_data['open'],
                            high=bar_4h_data['high'],
                            low=bar_4h_data['low'],
                            close=bar_4h_data['close']
                        )
                        last_processed_4h_idx = idx
            
            if self.main_timeframe.endswith('T') or self.main_timeframe.endswith('min'):
                minutes = int(self.main_timeframe.replace('T', '').replace('min', ''))
                bar_duration = pd.Timedelta(minutes=minutes)
            elif self.main_timeframe.endswith('H'):
                hours = int(self.main_timeframe.replace('H', ''))
                bar_duration = pd.Timedelta(hours=hours)
            else:
                bar_duration = pd.Timedelta(hours=1)
            
            bar_start = bar_ts + bar_duration
            bar_end = bar_start + bar_duration
            minute_bars = self.minute_data[
                (self.minute_data.index >= bar_start) & 
                (self.minute_data.index < bar_end)
            ]
            
            for minute_ts, minute_bar in minute_bars.iterrows():
                minute_trend = self.sq60.check_minute_trigger(minute_ts, minute_bar['close'])
                
                # CRITICAL FIX: Update 4H detector with ALL minutes chronologically
                # Only process minutes we haven't seen yet
                if self.enable_multi_timeframe and self.sq60_4h is not None:
                    if last_processed_minute_ts is None or minute_ts > last_processed_minute_ts:
                        self.sq60_4h.check_minute_trigger(minute_ts, minute_bar['close'])
                        last_processed_minute_ts = minute_ts
                
                current_balance_with_unrealized = self.current_balance
                
                if self.current_position:
                    pos = self.current_position
                    
                    if pos['direction'] == 'LONG':
                        price_change = minute_bar['close'] - pos['entry_price']
                    else:
                        price_change = pos['entry_price'] - minute_bar['close']
                    
                    pips = price_change / self.pip_size
                    pip_value = self.calculate_pip_value(minute_bar['close'])
                    unrealized_pnl = pips * pip_value * pos['contracts']
                    current_balance_with_unrealized += unrealized_pnl
                
                self.balance_history.append({
                    'timestamp': minute_ts,
                    'balance': current_balance_with_unrealized
                })
                
                self.update_drawdown(current_balance_with_unrealized)
                
                session_date = self.get_trading_session_date(minute_ts)
                
                if session_date != current_session:
                    if day_info and current_session:
                        self.daily_summaries.append(day_info)
                    
                    current_session = session_date
                    day_info = {
                        'date': current_session,
                        'trend_at_check': None,
                        'or_high': None,
                        'or_low': None,
                        'trend_confirmed': False,
                        'traded': False,
                        'trade_details': None
                    }
                
                if (minute_ts.hour == self.trend_check_hour and 
                    minute_ts.minute == self.trend_check_minute and 
                    current_session not in self.traded_days):
                    
                    if current_session in daily_minute_groups:
                        day_data = daily_minute_groups[current_session]
                        or_high, or_low = self.calculate_or(day_data)
                        
                        if or_high is not None:
                            trend_at_check = self.sq60.trend
                            is_confirmed = self.is_trend_confirmed(minute_ts)
                            
                            self.trend_at_check_by_date[current_session] = {
                                'trend': trend_at_check,
                                'confirmed': is_confirmed,
                                'or_high': or_high,
                                'or_low': or_low
                            }
                            
                            # 🎯 WEIGHTED CONFLUENCE: Check all timeframes NOW (at 03:00) and store for the day
                            if self.enable_multi_timeframe and is_confirmed and trend_at_check in ['SQ_LONG', 'SQ_SHORT']:
                                # Get 4H trend (already running continuously)
                                trend_4h = self.sq60_4h.get_trend_state()
                                
                                # Calculate weighted confluence
                                confluence_result = self.confluence_checker.calculate_confluence(
                                    minute_ts, trend_at_check, trend_4h
                                )
                                
                                # Store confluence results for this trading day
                                self.trend_at_check_by_date[current_session]['confluence'] = confluence_result['confluence']
                                self.trend_at_check_by_date[current_session]['multiplier'] = confluence_result['multiplier']
                                self.trend_at_check_by_date[current_session]['should_trade_mtf'] = confluence_result['should_trade']
                                self.trend_at_check_by_date[current_session]['mtf_reason'] = confluence_result['reason']
                                
                                if self.verbose:
                                    print(f"         🎯 Weighted Confluence at {minute_ts.strftime('%H:%M')}:")
                                    print(f"            15min: {confluence_result['details']['15min']}")
                                    print(f"            1H: {confluence_result['details']['1H']}")
                                    print(f"            4H: {confluence_result['details']['4H']}")
                                    print(f"            Score: C = {confluence_result['confluence']} (|C| = {confluence_result['abs_confluence']})")
                                    print(f"            → Multiplier: {confluence_result['multiplier']}×")
                                    if not confluence_result['should_trade']:
                                        print(f"            ⚠️  Trade blocked: {confluence_result['reason']}")
                            elif not self.enable_multi_timeframe:
                                # Single timeframe - always 1x
                                self.trend_at_check_by_date[current_session]['multiplier'] = 1.0
                                self.trend_at_check_by_date[current_session]['should_trade_mtf'] = True
                            else:
                                # Trend not confirmed or OFF - no confluence check needed
                                self.trend_at_check_by_date[current_session]['multiplier'] = 1.0
                                self.trend_at_check_by_date[current_session]['should_trade_mtf'] = True
                            
                            day_info['trend_at_check'] = trend_at_check
                            day_info['or_high'] = or_high
                            day_info['or_low'] = or_low
                            day_info['trend_confirmed'] = is_confirmed
                            
                            if is_confirmed and trend_at_check in ['SQ_LONG', 'SQ_SHORT']:
                                self.or_levels.append({
                                    'date': current_session,
                                    'or_high': or_high,
                                    'or_low': or_low,
                                    'trend': trend_at_check,
                                    'timestamp_start': minute_ts.replace(hour=self.or_start_hour, minute=self.or_start_minute),
                                    'timestamp_end': minute_ts.replace(hour=self.trading_end_hour, minute=self.trading_end_minute)
                                })
                                
                                self.trend_markers.append({
                                    'timestamp': minute_ts,
                                    'trend': trend_at_check,
                                    'price': (or_high + or_low) / 2
                                })
                            
                            if self.verbose:
                                or_range = or_high - or_low
                                print(f"\n{current_session}:")
                                print(f"  {self.trend_check_hour:02d}:{self.trend_check_minute:02d} - Trend: {trend_at_check} {'✓ CONFIRMED' if is_confirmed else '✗ NOT CONFIRMED'}")
                                print(f"         OR: {or_low:.2f} - {or_high:.2f} (range: {or_range:.2f})")
                                
                                if is_confirmed and trend_at_check in ['SQ_LONG', 'SQ_SHORT']:
                                    print(f"         → ✓ Ready to trade {trend_at_check} today")
                                elif not is_confirmed and trend_at_check != 'OFF':
                                    print(f"         → Pattern needs more confirmation, no trading today")
                                else:
                                    print(f"         → No valid trend, no trading today")
                
                if self.is_in_trading_window(minute_ts):
                    
                    if current_session in self.trend_at_check_by_date and current_session not in self.traded_days:
                        snapshot = self.trend_at_check_by_date[current_session]
                        
                        trend_check = snapshot['trend']
                        confirmed_check = snapshot['confirmed']
                        or_high = snapshot['or_high']
                        or_low = snapshot['or_low']
                        
                        if confirmed_check and trend_check in ['SQ_LONG', 'SQ_SHORT']:
                            if self.current_position is None:
                                if trend_check == 'SQ_LONG' and minute_bar['close'] > or_high:
                                    entry = minute_bar['close']
                                    stop = or_low
                                    or_range = or_high - or_low
                                    
                                    entry_distance_from_or = entry - or_high
                                    if entry_distance_from_or > or_range * 0.5:
                                        if self.verbose:
                                            print(f"  {minute_ts.strftime('%H:%M')} - ⚠ Skipping LONG: Entry {entry:.1f} too far from OR_HIGH {or_high:.1f}")
                                        continue
                                    
                                    target = entry + (or_range * self.profit_target_multiplier)
                                    
                                    # 🎯 WEIGHTED CONFLUENCE: Use multiplier determined at 03:00 trend check
                                    multiplier = snapshot.get('multiplier', 1.0)
                                    should_trade_mtf = snapshot.get('should_trade_mtf', True)
                                    
                                    # Check if multi-timeframe blocks the trade
                                    if not should_trade_mtf:
                                        mtf_reason = snapshot.get('mtf_reason', 'UNKNOWN')
                                        if self.verbose:
                                            print(f"  {minute_ts.strftime('%H:%M')} - ⚠ Trade blocked by confluence: {mtf_reason}")
                                        continue
                                    
                                    contracts = self.calculate_position_size(entry, stop, multiplier)
                                    
                                    initial_margin, maintenance_margin = self.calculate_margin_required(entry, contracts)
                                    pct_balance_used = (initial_margin / self.current_balance) * 100
                                    
                                    if pct_balance_used > 80:
                                        if self.verbose:
                                            print(f"  {minute_ts.strftime('%H:%M')} - ⚠ Skipping trade: Margin requirement ({pct_balance_used:.1f}%) exceeds 80% of balance")
                                        continue
                                    
                                    pip_value_at_entry = self.calculate_pip_value(entry)
                                    
                                    # ✅ CREATE confluence_details from snapshot
                                    if self.enable_multi_timeframe and 'confluence' in snapshot:
                                        confluence_score = snapshot.get('confluence', 0)
                                        confluence_details = [
                                            f"C={confluence_score}",
                                            f"Mult={multiplier}x"
                                        ]
                                    else:
                                        confluence_details = [self.main_timeframe]
                                    
                                    self.current_position = {
                                        'entry_time': minute_ts,
                                        'entry_price': entry,
                                        'direction': 'LONG',
                                        'stop': stop,
                                        'target': target,
                                        'contracts': contracts,
                                        'sq60': trend_check,
                                        'or_high': or_high,
                                        'or_low': or_low,
                                        'or_range': or_range,
                                        'initial_margin': initial_margin,
                                        'maintenance_margin': maintenance_margin,
                                        'pct_balance_used': pct_balance_used,
                                        'pip_value_at_entry': pip_value_at_entry,
                                        'multiplier': multiplier,
                                        'confluence_details': confluence_details
                                    }
                                    
                                    # Track multiplier stats
                                    mult_key = f'{multiplier}x'
                                    self.multiplier_stats[mult_key] = self.multiplier_stats.get(mult_key, 0) + 1
                                    
                                    if self.verbose:
                                        risk_points = entry - stop
                                        risk_pips = risk_points / self.pip_size
                                        reward_points = target - entry
                                        reward_pips = reward_points / self.pip_size
                                        actual_rr = reward_pips / risk_pips if risk_pips > 0 else 0
                                        
                                        print(f"  {minute_ts.strftime('%H:%M')} - LONG entry at {entry:.2f}")
                                        print(f"           OR: {or_low:.2f} - {or_high:.2f} (range: {or_range:.1f})")
                                        print(f"           🎯 Alignment: {multiplier}x ({', '.join(confluence_details)})")
                                        print(f"           Stop: {stop:.2f} (risk: {risk_pips:.1f} pips)")
                                        print(f"           Target: {target:.2f} (reward: {reward_pips:.1f} pips)")
                                        print(f"           R:R = 1:{actual_rr:.2f}")
                                        print(f"           Contracts: {contracts} (base × {multiplier})")
                                        print(f"           Pip value @ {entry:.2f}: ${pip_value_at_entry:.2f}")
                                        print(f"           Initial margin: €{initial_margin:,.0f} ({pct_balance_used:.1f}% of balance)")
                                        print(f"           Total risk: {self.risk_per_trade_pct * multiplier:.1f}% of balance")
                                
                                elif trend_check == 'SQ_SHORT' and minute_bar['close'] < or_low:
                                    entry = minute_bar['close']
                                    stop = or_high
                                    or_range = or_high - or_low
                                    
                                    entry_distance_from_or = or_low - entry
                                    if entry_distance_from_or > or_range * 0.5:
                                        if self.verbose:
                                            print(f"  {minute_ts.strftime('%H:%M')} - ⚠ Skipping SHORT: Entry {entry:.1f} too far from OR_LOW {or_low:.1f}")
                                        continue
                                    
                                    target = entry - (or_range * self.profit_target_multiplier)
                                    
                                    # 🎯 WEIGHTED CONFLUENCE: Use multiplier determined at 03:00 trend check
                                    multiplier = snapshot.get('multiplier', 1.0)
                                    should_trade_mtf = snapshot.get('should_trade_mtf', True)
                                    
                                    # Check if multi-timeframe blocks the trade
                                    if not should_trade_mtf:
                                        mtf_reason = snapshot.get('mtf_reason', 'UNKNOWN')
                                        if self.verbose:
                                            print(f"  {minute_ts.strftime('%H:%M')} - ⚠ Trade blocked by confluence: {mtf_reason}")
                                        continue
                                    
                                    contracts = self.calculate_position_size(entry, stop, multiplier)
                                    
                                    initial_margin, maintenance_margin = self.calculate_margin_required(entry, contracts)
                                    pct_balance_used = (initial_margin / self.current_balance) * 100
                                    
                                    if pct_balance_used > 80:
                                        if self.verbose:
                                            print(f"  {minute_ts.strftime('%H:%M')} - ⚠ Skipping trade: Margin requirement ({pct_balance_used:.1f}%) exceeds 80% of balance")
                                        continue
                                    
                                    pip_value_at_entry = self.calculate_pip_value(entry)
                                    
                                    # ✅ CREATE confluence_details from snapshot
                                    if self.enable_multi_timeframe and 'confluence' in snapshot:
                                        confluence_score = snapshot.get('confluence', 0)
                                        confluence_details = [
                                            f"C={confluence_score}",
                                            f"Mult={multiplier}x"
                                        ]
                                    else:
                                        confluence_details = [self.main_timeframe]
                                    
                                    self.current_position = {
                                        'entry_time': minute_ts,
                                        'entry_price': entry,
                                        'direction': 'SHORT',
                                        'stop': stop,
                                        'target': target,
                                        'contracts': contracts,
                                        'sq60': trend_check,
                                        'or_high': or_high,
                                        'or_low': or_low,
                                        'or_range': or_range,
                                        'initial_margin': initial_margin,
                                        'maintenance_margin': maintenance_margin,
                                        'pct_balance_used': pct_balance_used,
                                        'pip_value_at_entry': pip_value_at_entry,
                                        'multiplier': multiplier,
                                        'confluence_details': confluence_details
                                    }
                                    
                                    # Track multiplier stats
                                    mult_key = f'{multiplier}x'
                                    self.multiplier_stats[mult_key] = self.multiplier_stats.get(mult_key, 0) + 1
                                    
                                    if self.verbose:
                                        risk_points = stop - entry
                                        risk_pips = risk_points / self.pip_size
                                        reward_points = entry - target
                                        reward_pips = reward_points / self.pip_size
                                        actual_rr = reward_pips / risk_pips if risk_pips > 0 else 0
                                        
                                        print(f"  {minute_ts.strftime('%H:%M')} - SHORT entry at {entry:.2f}")
                                        print(f"           OR: {or_low:.2f} - {or_high:.2f} (range: {or_range:.1f})")
                                        print(f"           🎯 Alignment: {multiplier}x ({', '.join(confluence_details)})")
                                        print(f"           Stop: {stop:.2f} (risk: {risk_pips:.1f} pips)")
                                        print(f"           Target: {target:.2f} (reward: {reward_pips:.1f} pips)")
                                        print(f"           R:R = 1:{actual_rr:.2f}")
                                        print(f"           Contracts: {contracts} (base × {multiplier})")
                                        print(f"           Pip value @ {entry:.2f}: ${pip_value_at_entry:.2f}")
                                        print(f"           Initial margin: €{initial_margin:,.0f} ({pct_balance_used:.1f}% of balance)")
                                        print(f"           Total risk: {self.risk_per_trade_pct * multiplier:.1f}% of balance")
                
                if self.current_position:
                    margin_call, current_equity, maint_margin = self.check_margin_call(minute_bar['close'])
                    
                    if margin_call:
                        pos = self.current_position
                        exit_price = minute_bar['close']
                        
                        if pos['direction'] == 'LONG':
                            price_change = exit_price - pos['entry_price']
                        else:
                            price_change = pos['entry_price'] - exit_price
                        
                        pips = price_change / self.pip_size
                        pip_value_at_exit = self.calculate_pip_value(exit_price)
                        pnl_eur = pips * pip_value_at_exit * pos['contracts']
                        
                        self.current_balance += pnl_eur
                        self.update_drawdown()
                        
                        if self.verbose:
                            print(f"  {minute_ts.strftime('%H:%M')} - ⚠ MARGIN CALL!")
                            print(f"           Equity: €{current_equity:,.0f} < Maintenance: €{maint_margin:,.0f}")
                            print(f"           Forced exit at {exit_price:.2f}")
                            print(f"           P&L: €{pnl_eur:+,.0f}")
                        
                        trade_record = {
                            'date': pos['entry_time'].date(),
                            'entry_time': pos['entry_time'],
                            'exit_time': minute_ts,
                            'direction': pos['direction'],
                            'entry_price': pos['entry_price'],
                            'exit_price': exit_price,
                            'stop': pos['stop'],
                            'target': pos['target'],
                            'points': price_change,
                            'pips': pips,
                            'contracts': pos['contracts'],
                            'pnl_eur': pnl_eur,
                            'balance': self.current_balance,
                            'exit_reason': 'Margin Call',
                            'sq60': pos['sq60'],
                            'or_high': pos['or_high'],
                            'or_low': pos['or_low'],
                            'or_range': pos.get('or_range', pos['or_high'] - pos['or_low']),
                            'initial_margin': pos['initial_margin'],
                            'maintenance_margin': pos['maintenance_margin'],
                            'pct_balance_used': pos['pct_balance_used'],
                            'pip_value_at_entry': pos.get('pip_value_at_entry', self.point_value),
                            'pip_value_at_exit': pip_value_at_exit,
                            'multiplier': pos.get('multiplier', 1),
                            'confluence_details': ','.join(pos.get('confluence_details', [self.main_timeframe]))
                        }
                        
                        self.trades.append(trade_record)
                        self.traded_days.add(current_session)
                        self.current_position = None
                        continue
                
                if self.current_position:
                    pos = self.current_position
                    exit_reason = None
                    exit_price = None
                    
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
                    
                    elif pos['direction'] == 'SHORT':
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
                    
                    if self.is_time_stop(minute_ts) and exit_price is None:
                        exit_price = minute_bar['close']
                        exit_reason = f'Time Stop ({self.trading_end_hour:02d}:{self.trading_end_minute:02d})'
                    
                    if exit_price:
                        if pos['direction'] == 'LONG':
                            price_change = exit_price - pos['entry_price']
                        else:
                            price_change = pos['entry_price'] - exit_price
                        
                        pips = price_change / self.pip_size
                        pip_value_at_exit = self.calculate_pip_value(exit_price)
                        pnl_eur = pips * pip_value_at_exit * pos['contracts']
                        
                        self.current_balance += pnl_eur
                        self.update_drawdown()
                        
                        trade_record = {
                            'date': pos['entry_time'].date(),
                            'entry_time': pos['entry_time'],
                            'exit_time': minute_ts,
                            'direction': pos['direction'],
                            'entry_price': pos['entry_price'],
                            'exit_price': exit_price,
                            'stop': pos['stop'],
                            'target': pos['target'],
                            'points': price_change,
                            'pips': pips,
                            'contracts': pos['contracts'],
                            'pnl_eur': pnl_eur,
                            'balance': self.current_balance,
                            'exit_reason': exit_reason,
                            'sq60': pos['sq60'],
                            'or_high': pos['or_high'],
                            'or_low': pos['or_low'],
                            'or_range': pos.get('or_range', pos['or_high'] - pos['or_low']),
                            'initial_margin': pos['initial_margin'],
                            'maintenance_margin': pos['maintenance_margin'],
                            'pct_balance_used': pos['pct_balance_used'],
                            'pip_value_at_entry': pos.get('pip_value_at_entry', self.point_value),
                            'pip_value_at_exit': pip_value_at_exit,
                            'multiplier': pos.get('multiplier', 1),
                            'confluence_details': ','.join(pos.get('confluence_details', [self.main_timeframe]))
                        }
                        
                        self.trades.append(trade_record)
                        
                        self.trade_annotations.append({
                            'entry_time': pos['entry_time'],
                            'exit_time': minute_ts,
                            'entry_price': pos['entry_price'],
                            'exit_price': exit_price,
                            'stop': pos['stop'],
                            'target': pos['target'],
                            'direction': pos['direction'],
                            'pnl_eur': pnl_eur,
                            'exit_reason': exit_reason,
                            'sq60_trend': pos['sq60'],
                            'multiplier': pos.get('multiplier', 1)
                        })
                        
                        day_info['traded'] = True
                        day_info['trade_details'] = trade_record
                        self.traded_days.add(current_session)
                        
                        if self.verbose:
                            status = "✓" if pnl_eur > 0 else "✗"
                            print(f"  {minute_ts.strftime('%H:%M')} - {status} {exit_reason} at {exit_price:.2f}")
                            print(f"           P&L: €{pnl_eur:+,.0f} ({pips:+.2f} pips) | Balance: €{self.current_balance:,.0f}")
                        
                        self.current_position = None
        
        if day_info and current_session:
            self.daily_summaries.append(day_info)
        
        return self.get_results()
    
    def get_results(self):
        trades_df = pd.DataFrame(self.trades) if self.trades else pd.DataFrame()
        
        summaries_df = pd.DataFrame(self.daily_summaries)
        total_days = len(summaries_df)
        days_with_trend_on = len(summaries_df[summaries_df['trend_at_check'].isin(['SQ_LONG', 'SQ_SHORT'])])
        days_with_trend_confirmed = len(summaries_df[summaries_df['trend_confirmed'] == True])
        days_traded = len(summaries_df[summaries_df['traded'] == True])
        
        results = {
            'trades': trades_df,
            'daily_summaries': summaries_df,
            'balance_history': pd.DataFrame(self.balance_history),
            'starting_balance': self.starting_balance,
            'final_balance': self.current_balance,
            'return_pct': ((self.current_balance - self.starting_balance) / self.starting_balance) * 100,
            'max_drawdown_eur': self.max_drawdown_eur,
            'max_drawdown_pct': self.max_drawdown_pct,
            'total_trading_days': total_days,
            'days_with_trend_on': days_with_trend_on,
            'days_with_trend_confirmed': days_with_trend_confirmed,
            'days_trend_unconfirmed': days_with_trend_on - days_with_trend_confirmed,
            'days_traded': days_traded,
            'trend_on_percentage': (days_with_trend_on / total_days * 100) if total_days > 0 else 0,
            'trend_confirmed_percentage': (days_with_trend_confirmed / total_days * 100) if total_days > 0 else 0,
            'multiplier_stats': self.multiplier_stats.copy()
        }
        
        if len(trades_df) > 0:
            wins = trades_df[trades_df['pnl_eur'] > 0]
            losses = trades_df[trades_df['pnl_eur'] < 0]
            
            results['total_trades'] = len(trades_df)
            results['wins'] = len(wins)
            results['losses'] = len(losses)
            results['win_rate'] = (len(wins) / len(trades_df)) * 100
            results['avg_win'] = wins['pnl_eur'].mean() if len(wins) > 0 else 0
            results['avg_loss'] = losses['pnl_eur'].mean() if len(losses) > 0 else 0
            
            avg_initial_margin = trades_df['initial_margin'].mean()
            results['avg_initial_margin'] = avg_initial_margin
            
            if avg_initial_margin > 0:
                results['return_on_margin_pct'] = (results['return_pct'] * self.starting_balance) / avg_initial_margin
            else:
                results['return_on_margin_pct'] = 0
            
            trading_days = (self.end_date - self.start_date).days
            years = trading_days / 365.25
            
            if years > 0 and results['final_balance'] > 0 and self.starting_balance > 0:
                results['cagr'] = (((results['final_balance'] / self.starting_balance) ** (1 / years)) - 1) * 100
            else:
                results['cagr'] = 0
            
            results['trading_days_total'] = trading_days
            results['trading_years'] = years
            
            results['avg_pct_balance_used'] = trades_df['pct_balance_used'].mean()
            results['max_pct_balance_used'] = trades_df['pct_balance_used'].max()
            results['min_pct_balance_used'] = trades_df['pct_balance_used'].min()
            
            if len(losses) > 0 and losses['pnl_eur'].sum() != 0:
                results['profit_factor'] = abs(wins['pnl_eur'].sum() / losses['pnl_eur'].sum())
            else:
                results['profit_factor'] = float('inf')
            
            # Weighted confluence specific analytics
            if 'multiplier' in trades_df.columns:
                # Performance by multiplier level
                for mult in [0.5, 1.0, 1.5, 2.0]:
                    mult_trades = trades_df[trades_df['multiplier'] == mult]
                    if len(mult_trades) > 0:
                        mult_wins = mult_trades[mult_trades['pnl_eur'] > 0]
                        results[f'trades_{mult}x'] = len(mult_trades)
                        results[f'win_rate_{mult}x'] = (len(mult_wins) / len(mult_trades)) * 100
                        results[f'pnl_{mult}x'] = mult_trades['pnl_eur'].sum()
                        results[f'avg_pnl_{mult}x'] = mult_trades['pnl_eur'].mean()
        
        return results
    
    def print_results(self, results):
        print("\n" + "="*80)
        print(f"WEIGHTED CONFLUENCE RESULTS - {self.instrument_name}")
        print("="*80)
        
        print(f"\n💰 Account Performance:")
        print(f"  Starting Balance: €{results['starting_balance']:,.0f}")
        print(f"  Final Balance: €{results['final_balance']:,.0f}")
        print(f"  Total Return: {results['return_pct']:+.2f}%")
        print(f"  Max Drawdown: €{results['max_drawdown_eur']:,.0f} ({results['max_drawdown_pct']:.2f}%)")
        
        if 'cagr' in results and 'return_on_margin_pct' in results:
            print(f"\n📈 Time-Adjusted & Leverage Metrics:")
            print(f"  CAGR: {results['cagr']:.2f}% (annualized return over {results['trading_years']:.2f} years)")
            print(f"  Return on Margin: {results['return_on_margin_pct']:.2f}% (return on capital at risk)")
        
        print(f"\n🎯 Weighted Confluence Distribution:")
        total_mult_trades = sum(results['multiplier_stats'].values())
        if total_mult_trades > 0:
            for key in sorted(results['multiplier_stats'].keys(), key=lambda x: float(x.replace('x', ''))):
                count = results['multiplier_stats'][key]
                pct = (count / total_mult_trades) * 100
                print(f"  {key} trades: {count} ({pct:.1f}%)")
        
        if self.enable_multi_timeframe and 'trades_0.5x' in results:
            print(f"\n📊 Performance by Confluence Level:")
            for mult in [0.5, 1.0, 1.5, 2.0]:
                if f'trades_{mult}x' in results:
                    print(f"  {mult}x confluence:")
                    print(f"    Trades: {results[f'trades_{mult}x']}")
                    print(f"    Win Rate: {results[f'win_rate_{mult}x']:.1f}%")
                    print(f"    Total P&L: €{results[f'pnl_{mult}x']:+,.0f}")
                    print(f"    Avg P&L: €{results[f'avg_pnl_{mult}x']:+,.0f}")
        
        print(f"\n📊 Day Analysis:")
        print(f"  Total trading days: {results['total_trading_days']}")
        print(f"  Days with trend ON: {results['days_with_trend_on']} ({results['trend_on_percentage']:.1f}%)")
        print(f"    └─ Confirmed: {results['days_with_trend_confirmed']} ({results['trend_confirmed_percentage']:.1f}%)")
        print(f"  Days traded: {results['days_traded']}")
        
        if 'total_trades' in results:
            print(f"\n📈 Trade Statistics:")
            print(f"  Total Trades: {results['total_trades']}")
            print(f"  Wins: {results['wins']} ({results['win_rate']:.1f}%)")
            print(f"  Losses: {results['losses']}")
            print(f"  Avg Win: €{results['avg_win']:,.0f}")
            print(f"  Avg Loss: €{results['avg_loss']:,.0f}")
            if results['profit_factor'] != float('inf'):
                print(f"  Profit Factor: {results['profit_factor']:.2f}")


    def create_balance_chart(self):
        """Create a matplotlib chart of balance over time"""
        balance_df = pd.DataFrame(self.balance_history)
        
        if len(balance_df) == 0:
            print("⚠ No balance history to plot")
            return None
        
        plt.figure(figsize=(12, 6))
        
        plt.plot(balance_df['timestamp'], balance_df['balance'], 
                color='green', linewidth=2, label='Account Balance')
        
        plt.axhline(y=self.starting_balance, color='gray', 
                   linestyle='--', linewidth=1, alpha=0.7, 
                   label=f'Starting Balance: €{self.starting_balance:,.0f}')
        
        total_return = ((self.current_balance - self.starting_balance) / self.starting_balance) * 100
        
        mtf_suffix = "MTF" if self.enable_multi_timeframe else "STF"
        plt.title(f'{self.instrument_name} ({self.main_timeframe} {mtf_suffix}) - Account Balance Growth\n'
                 f'Return: {total_return:+.2f}% | Final Balance: €{self.current_balance:,.0f}',
                 fontsize=12, fontweight='bold', pad=15)
        
        plt.xlabel('Date', fontsize=10)
        plt.ylabel('Balance (€)', fontsize=10)
        
        plt.gca().yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'€{x:,.0f}'))
        
        plt.grid(True, alpha=0.3, linestyle='--', linewidth=0.5)
        
        plt.xticks(rotation=45, ha='right')
        
        plt.legend(loc='best', framealpha=0.9)
        
        plt.tight_layout()
        
        filename = f'balance_chart_{self.instrument_name}_{self.main_timeframe}_{mtf_suffix}.png'
        plt.savefig(filename, dpi=150, bbox_inches='tight')
        print(f"✓ Balance chart saved: {filename}")
        
        plt.show()
        
        return filename
    
    def create_tradingview_chart(self, show_all_or=True, initial_visible_bars=200):
        """Create interactive TradingView-style chart"""
        
        bar_data = self.resampled_data.copy()
        
        bar_numbers = list(range(len(bar_data)))
        timestamp_to_bar = {ts: i for i, ts in enumerate(bar_data.index)}
        
        fig = go.Figure()
        
        candle_hover_texts = []
        for ts, row in bar_data.iterrows():
            candle_range = row['high'] - row['low']
            body = abs(row['close'] - row['open'])
            direction = "Bullish" if row['close'] >= row['open'] else "Bearish"
            
            hover_text = (
                f"<b>{ts.strftime('%Y-%m-%d %H:%M')}</b><br>"
                f"{ts.strftime('%A')}<br>"
                f"<br>"
                f"<b>OHLC:</b><br>"
                f"  O: {row['open']:.1f}<br>"
                f"  H: {row['high']:.1f}<br>"
                f"  L: {row['low']:.1f}<br>"
                f"  C: {row['close']:.1f}<br>"
                f"<br>"
                f"  Range: {candle_range:.1f}<br>"
                f"  Body: {body:.1f}<br>"
                f"  Type: {direction}"
            )
            candle_hover_texts.append(hover_text)
        
        fig.add_trace(go.Candlestick(
            x=bar_numbers,
            open=bar_data['open'],
            high=bar_data['high'],
            low=bar_data['low'],
            close=bar_data['close'],
            name=self.instrument_name,
            increasing_line_color='#089981',
            decreasing_line_color='#F23645',
            increasing_fillcolor='#089981',
            decreasing_fillcolor='#F23645',
            line=dict(width=1),
            whiskerwidth=0,
            hovertext=candle_hover_texts,
            hoverinfo='text'
        ))
        
        if show_all_or and self.or_levels:
            for or_level in self.or_levels:
                start_idx = None
                end_idx = None
                
                for ts, bar_num in timestamp_to_bar.items():
                    if ts >= or_level['timestamp_start'] and start_idx is None:
                        start_idx = bar_num
                    if ts >= or_level['timestamp_end']:
                        end_idx = bar_num
                        break
                
                if start_idx is not None and end_idx is not None:
                    or_range = or_level['or_high'] - or_level['or_low']
                    
                    or_high_hover = (
                        f"<b>OPENING RANGE HIGH</b><br>"
                        f"<br>"
                        f"  • Date: {or_level['date']}<br>"
                        f"  • Level: {or_level['or_high']:.2f}<br>"
                        f"  • OR Range: {or_range:.1f} points<br>"
                        f"  • SQ60 at {self.trend_check_hour:02d}:{self.trend_check_minute:02d}: {or_level['trend']}<br>"
                        f"<br>"
                        f"  • LONG entry if breaks above<br>"
                        f"<extra></extra>"
                    )
                    
                    fig.add_trace(go.Scatter(
                        x=[start_idx, end_idx],
                        y=[or_level['or_high'], or_level['or_high']],
                        mode='lines',
                        line=dict(color='rgba(100,150,255,0.4)', width=1, dash='dash'),
                        showlegend=False,
                        hovertemplate=or_high_hover
                    ))
                    
                    or_low_hover = (
                        f"<b>OPENING RANGE LOW</b><br>"
                        f"<br>"
                        f"  • Date: {or_level['date']}<br>"
                        f"  • Level: {or_level['or_low']:.2f}<br>"
                        f"  • OR Range: {or_range:.1f} points<br>"
                        f"  • SQ60 at {self.trend_check_hour:02d}:{self.trend_check_minute:02d}: {or_level['trend']}<br>"
                        f"<br>"
                        f"  • SHORT entry if breaks below<br>"
                        f"<extra></extra>"
                    )
                    
                    fig.add_trace(go.Scatter(
                        x=[start_idx, end_idx],
                        y=[or_level['or_low'], or_level['or_low']],
                        mode='lines',
                        line=dict(color='rgba(100,150,255,0.4)', width=1, dash='dash'),
                        showlegend=False,
                        hovertemplate=or_low_hover
                    ))
        
        total_bars = len(bar_numbers)
        if total_bars > initial_visible_bars:
            x_start = total_bars - initial_visible_bars
            x_end = total_bars - 1
        else:
            x_start = 0
            x_end = total_bars - 1
        
        visible_data = bar_data.iloc[x_start:x_end+1]
        y_min = visible_data['low'].min()
        y_max = visible_data['high'].max()
        y_range = y_max - y_min
        y_padding = y_range * 0.1
        
        num_ticks = 20
        tick_interval = max(1, total_bars // num_ticks)
        tick_positions = list(range(0, total_bars, tick_interval))
        tick_labels = [bar_data.index[i].strftime('%Y-%m-%d') for i in tick_positions]
        
        mtf_label = "Multi-TF" if self.enable_multi_timeframe else "Single-TF"
        
        fig.update_layout(
            title={
                'text': f"<b>{mtf_label} SQ60 + ORB - {self.instrument_name} ({self.main_timeframe})</b> ({self.start_date.strftime('%b %Y')} - {self.end_date.strftime('%b %Y')})",
                'x': 0.5,
                'xanchor': 'center',
                'font': {'size': 20, 'color': '#D1D4DC', 'family': 'Trebuchet MS'}
            },
            xaxis_title='',
            yaxis_title='',
            height=900,
            width=None,
            hovermode='closest',
            dragmode='pan',
            
            plot_bgcolor='#131722',
            paper_bgcolor='#0E1117',
            font=dict(color='#787B86'),
            
            xaxis=dict(
                gridcolor='#1E222D',
                showgrid=True,
                zeroline=False,
                showline=True,
                linewidth=1,
                linecolor='#2A2E39',
                tickfont=dict(size=10, color='#787B86'),
                range=[x_start, x_end],
                tickmode='array',
                tickvals=tick_positions,
                ticktext=tick_labels,
                tickangle=-45,
                rangeslider=dict(
                    visible=True,
                    bgcolor='#1E222D',
                    bordercolor='#2A2E39',
                    borderwidth=1,
                    thickness=0.05
                ),
                fixedrange=False
            ),
            yaxis=dict(
                gridcolor='#1E222D',
                showgrid=True,
                zeroline=False,
                showline=True,
                linewidth=1,
                linecolor='#2A2E39',
                tickfont=dict(size=11, color='#787B86'),
                range=[y_min - y_padding, y_max + y_padding],
                fixedrange=False,
                side='right'
            ),
            margin=dict(l=20, r=80, t=80, b=80),
            showlegend=False
        )
        
        config = {
            'displaylogo': False,
            'toImageButtonOptions': {
                'format': 'png',
                'filename': f'multi_tf_sq60_orb_{self.instrument_name}_{self.main_timeframe}',
                'height': 1080,
                'width': 1920,
                'scale': 2
            }
        }
        
        return fig, config


if __name__ == "__main__":
    print("✓ ProperSQ60ORBWithPlots module loaded successfully")
    print("  🎯 NEW: Weighted confluence system (15min + 1H + 4H)")
    print("  🎯 NEW: Dynamic position sizing (0.5× → 1.0× → 1.5× → 2.0×)")
    print("  🎯 NEW: Intelligent conflict handling")
    print("  ✅ C = (1×15min) + (2×1H) + (3×4H)")
    print("  ✅ Risk scales with confluence score")
    print("  Use interactive_backtest.py or run_backtest.py to run backtests")