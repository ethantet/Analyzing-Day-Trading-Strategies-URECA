import pandas as pd
from typing import List, Tuple, Optional
from datetime import date

class SQ60Detector:
    """
    SQ60 Framework - TIMEFRAME AGNOSTIC - OPTIMIZED VERSION
    
    Works on ANY timeframe: 15min, 1H, 4H, daily, etc.
    All logic based on "bars" not "hours"
    
    PERFORMANCE OPTIMIZATIONS:
    1. Incremental swing detection (only check new bars)
    2. Cached bar duration calculation
    3. Optimized consecutive filtering
    
    SWING DETECTION (STANDARD METHOD):
    - SigHigh: Bar's HIGH >= next 7 bars' HIGHs (high-to-high comparison only)
    - SigLow: Bar's LOW <= next 7 bars' LOWs (low-to-low comparison only)
    
    FILTERING:
    1. Remove consecutive bars of same type (keep first)
    2. Enforce alternating H-L-H-L pattern (if two HIGHs in a row, keep higher one)
    
    ENTRY PATTERNS (UNCHANGED):
    - UPTREND: L1→H1→L2→H2 where L2>L1 AND H2>H1 (strict: both required)
    - DOWNTREND: H1→L1→H2→L2 where H2<H1 AND L2<L1 (strict: both required)
    - Must be most recent 4 swings after all filtering
    
    ACTIVATION:
    - SQLong: Uptrend pattern + price breaks above H2 (2nd high)
    - SQShort: Downtrend pattern + price breaks below L2 (2nd low)
    
    INVALIDATION (checked every bar):
    - SQLong: Price crosses below L1 (1st low) - ANCHORED, never changes
    - SQShort: Price crosses above H1 (1st high) - ANCHORED, never changes
    
    CONTINUATION TRACKING (SIMPLIFIED):
    - SQ_LONG: Only track swing HIGHS that are higher than previous high
      * H3 > H2 → valid continuation, timer resets
      * Correction = bars from H2 until price breaks above H2 again (to reach H3)
      * Ignore all swing lows - don't care if they're higher or lower
    
    - SQ_SHORT: Only track swing LOWS that are lower than previous low
      * L3 < L2 → valid continuation, timer resets
      * Correction = bars from L2 until price breaks below L2 again (to reach L3)
      * Ignore all swing highs - don't care if they're higher or lower
    
    CORRECTION EXIT:
    - If bars since last progression > threshold, exit trend
    - Threshold = average of last N corrections × 3 (where N = 1, 2, or 3+)
    """
    
    def __init__(self, 
                 timeframe: str = '1H',
                 confirmation_bars: int = 7,
                 lookback_bars: int = 720):
        """
        Initialize SQ60 Detector
        
        Args:
            timeframe: Bar period for reference ('15T'=15min, '1H'=1hour, '4H'=4hour)
                      Only used for logging/display purposes
            confirmation_bars: Bars needed to confirm swing (default: 7, don't change)
            lookback_bars: Bars to keep in memory (default: 720)
                          15min: 720 bars = 7.5 days
                          1H: 720 bars = 30 days
                          4H: 720 bars = 120 days
        """
        self.timeframe = timeframe
        self.bars = []  # Renamed from hourly_bars
        self.raw_swings = []  # All detected swings before filtering
        self.swing_points = []  # Filtered swings (after removing consecutive)
        self.current_date = None
        self.trend = "OFF"
        self.lookback_bars = lookback_bars
        self.last_checked_index = -1
        
        # OPTIMIZATION: Cache bar duration calculation (parse once, not every call)
        self._bar_duration_hours = self._parse_timeframe()
        
        self.trend_invalidation_level = None
        self.trend_start_timestamp = None
        self.confirmation_bars = confirmation_bars
        
        self.last_used_pattern_indices = None
        
        # Simplified correction tracking
        self.last_significant_level_time = None  # When did we last see a higher high / lower low?
        self.last_significant_level_price = None  # What was that price level?
        self.bars_since_last_progress = 0.0  # Bars since last significant level
        self.correction_durations = []  # Historical correction durations (in bars)
        self.last_processed_swing_index = -1  # Track which swing we last looked at
        
        # Trend duration tracking
        self.trend_durations = []  # List of (trend_type, duration_bars, exit_reason)
        
        # Track all swings that were part of trends for visualization
        self.trend_swing_sequences = []  # List of {'entry_time', 'exit_time', 'trend_type', 'swings': [(ts, price, type), ...]}
    
    def _parse_timeframe(self) -> float:
        """
        Parse timeframe once and cache it (called once in __init__)
        OPTIMIZATION: Don't parse the string on every bar duration calculation
        
        Returns:
            Bar duration in hours (e.g., 0.25 for 15min, 1.0 for 1H, 4.0 for 4H)
        """
        if self.timeframe.endswith('T') or self.timeframe.endswith('min'):
            minutes = int(self.timeframe.replace('T', '').replace('min', ''))
            return minutes / 60.0
        elif self.timeframe.endswith('H'):
            return int(self.timeframe.replace('H', ''))
        else:
            return 1.0
        
    def update(self, timestamp: pd.Timestamp, high: float, low: float, close: float, open_price: float = None) -> str:
        """Process new bar and update swing detection"""
        if open_price is None:
            open_price = close
            
        bar_date = timestamp.date()
        if self.current_date != bar_date:
            self.current_date = bar_date
        
        self.bars.append((timestamp, open_price, high, low, close))
        self._clean_old_data(timestamp)
        
        # Detect all swings, filter consecutive, then enforce alternating pattern
        self._identify_all_swings()
        self._filter_consecutive_swings()
        self._enforce_alternating_pattern()
        
        return self.trend
    
    def _reset_trend(self):
        """Reset all trend-related variables"""
        self.trend = "OFF"
        self.trend_invalidation_level = None
        self.trend_start_timestamp = None
        self.last_significant_level_time = None
        self.last_significant_level_price = None
        self.bars_since_last_progress = 0.0
        self.correction_durations = []
        self.last_processed_swing_index = -1
        # Note: we keep trend_durations for analysis
    
    def check_minute_trigger(self, timestamp: pd.Timestamp, price: float) -> str:
        """
        Check for triggers AND invalidation on every minute bar
        Also tracks corrections for exit logic
        
        CRITICAL FIX: Only check for new patterns when trend is OFF
        This prevents confusing re-triggering during active trends
        
        CRITICAL FIX 2: Extremely defensive state management
        Ensures only ONE trend can be active at a time
        """
        if len(self.swing_points) < 4:
            return self.trend
        
        # DEFENSIVE CHECK: Should never happen, but verify state consistency
        if self.trend != "OFF":
            # Verify all trend variables are set
            if self.trend_invalidation_level is None or self.trend_start_timestamp is None:
                print(f"⚠️  CRITICAL: Trend {self.trend} active but variables not set! Force resetting.")
                self._reset_trend()
                return self.trend
        else:
            # If OFF, all trend variables should be None
            if self.trend_invalidation_level is not None or self.trend_start_timestamp is not None:
                print(f"⚠️  CRITICAL: Trend OFF but variables still set! Force resetting.")
                self._reset_trend()
        
        # FIRST: Check invalidation and corrections if trend is active
        if self.trend != "OFF":
            # Check invalidation first (this can set trend to OFF)
            self._check_minute_invalidation(timestamp, price)
            
            # If invalidated, don't check corrections - trend is already OFF
            if self.trend == "OFF":
                return self.trend
            
            # Track corrections and check correction-time exit (this can also set trend to OFF)
            self._track_corrections(timestamp, price)
            
            # If exited, return immediately
            if self.trend == "OFF":
                return self.trend
            
            # Still in trend after all checks - don't look for new patterns
            return self.trend
        
        # SECOND: Check for new triggers ONLY when trend is OFF
        # At this point, trend MUST be OFF or we would have returned above
        if self.trend != "OFF":
            print(f"⚠️  CRITICAL: Reached pattern detection with trend={self.trend}! This should never happen!")
            return self.trend
        
        pattern_info = self._get_pattern_info()
        
        # Check for uptrend trigger
        if pattern_info['has_uptrend'] and self.last_used_pattern_indices != pattern_info['pattern_indices']:
            trigger_level = pattern_info['h2']  # 2nd high
            
            if price > trigger_level:
                # TRIPLE CHECK: Trend must be OFF before entering new trend
                if self.trend != "OFF":
                    print(f"⚠️  CRITICAL: Attempting SQ_LONG entry but trend={self.trend}! Force exiting first.")
                    duration = self._calculate_bar_duration(self.trend_start_timestamp, timestamp)
                    self.trend_durations.append((self.trend, duration, 'FORCED_EXIT_BEFORE_NEW_TREND'))
                    self._reset_trend()
                
                self.trend = "SQ_LONG"
                self.last_used_pattern_indices = pattern_info['pattern_indices']
                self.trend_invalidation_level = pattern_info['l1']  # 1st low - NEVER changes
                self.trend_start_timestamp = timestamp
                
                # Calculate initial corrections from the pattern (for threshold history)
                pattern_start_idx = pattern_info['pattern_indices'][0]
                l1_ts, l1_price, _ = self.swing_points[pattern_start_idx]
                h1_ts, h1_price, _ = self.swing_points[pattern_start_idx + 1]
                l2_ts, l2_price, _ = self.swing_points[pattern_start_idx + 2]
                h2_ts, h2_price, _ = self.swing_points[pattern_start_idx + 3]
                
                # Correction 1: From H1 until price went back above H1 (to reach H2)
                correction_1 = self._find_break_time(h1_ts, h1_price, h2_ts, 'above')
                
                # Correction 2: From H2 until trigger (now)
                correction_2 = self._calculate_bar_duration(h2_ts, timestamp)
                
                # Store historical corrections for threshold calculation
                self.correction_durations = [correction_1, correction_2]
                
                # Start tracking from H2 (last high of pattern)
                # Timer measures how long since H2 formed
                self.last_significant_level_time = h2_ts  # When H2 formed
                self.last_significant_level_price = h2_price  # H2 as baseline
                self.bars_since_last_progress = self._calculate_bar_duration(h2_ts, timestamp)
                
                # Mark where we are in the swing list
                self.last_processed_swing_index = pattern_start_idx + 3
                
                # Start tracking swings for this trend
                self.current_trend_swings = [
                    (l1_ts, l1_price, 'LOW'),
                    (h1_ts, h1_price, 'HIGH'),
                    (l2_ts, l2_price, 'LOW'),
                    (h2_ts, h2_price, 'HIGH')
                ]
                
                print(f"✓ NEW: OFF → SQ_LONG at {timestamp.strftime('%Y-%m-%d %H:%M')}")
                print(f"   Price {price:.1f} > H2 trigger {trigger_level:.1f}")
                print(f"   Pattern: {pattern_info['pattern_str']}")
                print(f"   Invalidation: L1 = {self.trend_invalidation_level:.1f} (ANCHORED)")
                print(f"   Initial corrections: H1→H2={correction_1:.1f} bars, H2→trigger={correction_2:.1f} bars")
                print(f"   Continuation rule: Track only HIGHER HIGHS (ignore lows)")
                print(f"   ⏱️  Timer at {self.bars_since_last_progress:.1f} bars since H2 formed")
                return self.trend
        
        # Check for downtrend trigger
        if pattern_info['has_downtrend'] and self.last_used_pattern_indices != pattern_info['pattern_indices']:
            trigger_level = pattern_info['l2']  # 2nd low
            
            if price < trigger_level:
                # TRIPLE CHECK: Trend must be OFF before entering new trend
                if self.trend != "OFF":
                    print(f"⚠️  CRITICAL: Attempting SQ_SHORT entry but trend={self.trend}! Force exiting first.")
                    duration = self._calculate_bar_duration(self.trend_start_timestamp, timestamp)
                    self.trend_durations.append((self.trend, duration, 'FORCED_EXIT_BEFORE_NEW_TREND'))
                    self._reset_trend()
                
                self.trend = "SQ_SHORT"
                self.last_used_pattern_indices = pattern_info['pattern_indices']
                self.trend_invalidation_level = pattern_info['h1']  # 1st high - NEVER changes
                self.trend_start_timestamp = timestamp
                
                # Calculate initial corrections from the pattern
                pattern_start_idx = pattern_info['pattern_indices'][0]
                h1_ts, h1_price, _ = self.swing_points[pattern_start_idx]
                l1_ts, l1_price, _ = self.swing_points[pattern_start_idx + 1]
                h2_ts, h2_price, _ = self.swing_points[pattern_start_idx + 2]
                l2_ts, l2_price, _ = self.swing_points[pattern_start_idx + 3]
                
                # Correction 1: From L1 until price went back below L1 (to reach L2)
                correction_1 = self._find_break_time(l1_ts, l1_price, l2_ts, 'below')
                
                # Correction 2: From L2 until trigger (now)
                correction_2 = self._calculate_bar_duration(l2_ts, timestamp)
                
                # Store historical corrections for threshold calculation
                self.correction_durations = [correction_1, correction_2]
                
                # Start tracking from L2 (last low of pattern)
                # Timer measures how long since L2 formed
                self.last_significant_level_time = l2_ts  # When L2 formed
                self.last_significant_level_price = l2_price  # L2 as baseline
                self.bars_since_last_progress = self._calculate_bar_duration(l2_ts, timestamp)
                
                # Mark where we are in the swing list
                self.last_processed_swing_index = pattern_start_idx + 3
                
                # Start tracking swings for this trend
                self.current_trend_swings = [
                    (h1_ts, h1_price, 'HIGH'),
                    (l1_ts, l1_price, 'LOW'),
                    (h2_ts, h2_price, 'HIGH'),
                    (l2_ts, l2_price, 'LOW')
                ]
                
                print(f"✓ NEW: OFF → SQ_SHORT at {timestamp.strftime('%Y-%m-%d %H:%M')}")
                print(f"   Price {price:.1f} < L2 trigger {trigger_level:.1f}")
                print(f"   Pattern: {pattern_info['pattern_str']}")
                print(f"   Invalidation: H1 = {self.trend_invalidation_level:.1f} (ANCHORED)")
                print(f"   Initial corrections: L1→L2={correction_1:.1f} bars, L2→trigger={correction_2:.1f} bars")
                print(f"   Continuation rule: Track only LOWER LOWS (ignore highs)")
                print(f"   ⏱️  Timer at {self.bars_since_last_progress:.1f} bars since L2 formed")
                return self.trend
        
        return self.trend
    
    def _calculate_bar_duration(self, start_ts: pd.Timestamp, end_ts: pd.Timestamp) -> float:
        """
        Calculate duration in bars between two timestamps
        OPTIMIZED: Uses cached bar duration from __init__
        
        Args:
            start_ts: Start timestamp
            end_ts: End timestamp
            
        Returns:
            Number of bars between timestamps (fractional)
        """
        duration_hours = (end_ts - start_ts).total_seconds() / 3600
        return duration_hours / self._bar_duration_hours
    
    def _identify_all_swings(self):
        """
        Detect ALL SigHighs and SigLows (before filtering)
        OPTIMIZED: Only check NEW bars we haven't checked yet (incremental detection)
        
        HUGE PERFORMANCE GAIN: Instead of checking all 10,000 bars every update,
        only check the 1 new bar. That's 10,000x less work!
        """
        if len(self.bars) < self.confirmation_bars + 1:
            return
        
        latest_checkable = len(self.bars) - self.confirmation_bars - 1
        
        # OPTIMIZATION: Only check bars we haven't checked yet
        start_index = max(0, self.last_checked_index + 1)
        
        for i in range(start_index, latest_checkable + 1):
            timestamp, bar_open, bar_high, bar_low, bar_close = self.bars[i]
            
            # OPTIMIZATION: Use slice instead of loop (much faster)
            next_bars = self.bars[i + 1:i + 1 + self.confirmation_bars]
            
            if len(next_bars) < self.confirmation_bars:
                continue
            
            # OPTIMIZATION: Use all() instead of manual loop
            # Check SigHigh - SIMPLIFIED: Only compare high to high
            is_sig_high = all(bar_high >= next_bar[2] for next_bar in next_bars)
            
            # OPTIMIZATION: Use all() instead of manual loop
            # Check SigLow - SIMPLIFIED: Only compare low to low
            is_sig_low = all(bar_low <= next_bar[3] for next_bar in next_bars)
            
            # CRITICAL FIX: A bar cannot be both HIGH and LOW
            # If both conditions are true, pick the more dominant one based on bar structure
            if is_sig_high and is_sig_low:
                # Calculate which is more extreme
                bar_range = bar_high - bar_low
                upper_wick = bar_high - max(bar_open, bar_close)
                lower_wick = min(bar_open, bar_close) - bar_low
                
                # If upper wick is significantly larger, it's a HIGH
                # If lower wick is significantly larger, it's a LOW
                # Otherwise, skip this bar as it's ambiguous
                if upper_wick > lower_wick * 1.5:
                    is_sig_low = False  # Keep only HIGH
                elif lower_wick > upper_wick * 1.5:
                    is_sig_high = False  # Keep only LOW
                else:
                    # Ambiguous - skip this bar entirely
                    continue
            
            # Add to raw_swings (now guaranteed to be only one type per bar)
            if is_sig_high:
                self.raw_swings.append((timestamp, bar_high, 'HIGH'))
            elif is_sig_low:
                self.raw_swings.append((timestamp, bar_low, 'LOW'))
        
        # OPTIMIZATION: Update last checked index so we don't re-check these bars
        self.last_checked_index = latest_checkable
    
    def _filter_consecutive_swings(self):
        """
        Remove consecutive same-type bars, keep only FIRST
        OPTIMIZED: Calculate threshold once, not in loop
        """
        if len(self.raw_swings) == 0:
            return
        
        # Sort by timestamp
        sorted_swings = sorted(self.raw_swings, key=lambda x: x[0])
        
        # OPTIMIZATION: Calculate threshold ONCE (not in the loop)
        consecutive_threshold = self._bar_duration_hours * 1.5
        
        filtered = []
        i = 0
        removed_count = 0
        
        while i < len(sorted_swings):
            current_ts, current_price, current_type = sorted_swings[i]
            
            # Find consecutive same-type swings
            j = i + 1
            while j < len(sorted_swings):
                next_ts, next_price, next_type = sorted_swings[j]
                
                # Check if consecutive (within threshold) and same type
                time_diff = (next_ts - current_ts).total_seconds() / 3600
                if time_diff <= consecutive_threshold and next_type == current_type:
                    j += 1
                else:
                    break
            
            # Keep FIRST in run
            filtered.append(sorted_swings[i])
            
            # Log if we removed consecutive swings
            run_length = j - i
            if run_length > 1:
                removed_count += run_length - 1
            
            i = j
        
        # Update swing_points
        self.swing_points = filtered
    
    def _enforce_alternating_pattern(self):
        """
        Enforce strict alternating HIGH-LOW-HIGH-LOW pattern
        When two same-type swings appear in a row, keep the more extreme one
        """
        if len(self.swing_points) < 2:
            return
        
        filtered = [self.swing_points[0]]  # Always keep first swing
        
        for i in range(1, len(self.swing_points)):
            current_ts, current_price, current_type = self.swing_points[i]
            prev_ts, prev_price, prev_type = filtered[-1]
            
            if current_type == prev_type:
                # Same type as previous - keep the more extreme one
                if current_type == 'HIGH':
                    # Keep the higher high
                    if current_price > prev_price:
                        filtered[-1] = self.swing_points[i]  # Replace with current (higher)
                    # else: keep previous (already higher)
                elif current_type == 'LOW':
                    # Keep the lower low
                    if current_price < prev_price:
                        filtered[-1] = self.swing_points[i]  # Replace with current (lower)
                    # else: keep previous (already lower)
            else:
                # Different type - add to list (alternating pattern maintained)
                filtered.append(self.swing_points[i])
        
        self.swing_points = filtered
    
    def _find_break_time(self, start_ts: pd.Timestamp, level: float, 
                        end_ts: pd.Timestamp, direction: str) -> float:
        """
        Find when price broke through a level after a swing formed
        
        Example for uptrend:
        - H2 forms at bar 10, price = 100
        - Price stays below 100 for bars 11-16 (correction)
        - Price breaks above 100 at bar 17 (H3 detected at bar 17)
        - Correction duration = 17 - 10 = 7 bars
        
        Returns:
            Duration in bars from start_ts until price broke the level
        """
        for bar_ts, bar_open, bar_high, bar_low, bar_close in self.bars:
            if bar_ts <= start_ts:
                continue
            if bar_ts > end_ts:
                break
            
            # Check if price broke the level in this bar
            if direction == 'above' and bar_high > level:
                # Price broke above the level
                return self._calculate_bar_duration(start_ts, bar_ts)
            elif direction == 'below' and bar_low < level:
                # Price broke below the level
                return self._calculate_bar_duration(start_ts, bar_ts)
        
        # If not found, correction lasted until the new swing was detected
        return self._calculate_bar_duration(start_ts, end_ts)
    
    def _track_corrections(self, timestamp: pd.Timestamp, price: float):
        """
        SIMPLIFIED: Track bars since last progression using a CONTINUOUS TIMER
        
        For SQ_LONG:
        - Track swing HIGHS for progression (higher highs reset timer)
        - Check for opposite DOWNTREND pattern (4-swing H-L-H-L structure) to exit
        
        For SQ_SHORT:
        - Track swing LOWS for progression (lower lows reset timer)
        - Check for opposite UPTREND pattern (4-swing L-H-L-H structure) to exit
        """
        if self.last_significant_level_time is None:
            return
        
        # Update the continuous timer
        self.bars_since_last_progress = self._calculate_bar_duration(self.last_significant_level_time, timestamp)
        
        # CRITICAL: Check for opposite pattern formation FIRST
        # This allows early exit when trend clearly reverses
        if len(self.swing_points) >= 4:
            opposite_pattern = self._check_opposite_pattern()
            
            if opposite_pattern['detected']:
                trend_type = self.trend
                duration_bars = self._calculate_bar_duration(self.trend_start_timestamp, timestamp)
                
                if hasattr(self, 'current_trend_swings') and self.current_trend_swings:
                    self.trend_swing_sequences.append({
                        'entry_time': self.trend_start_timestamp,
                        'exit_time': timestamp,
                        'trend_type': trend_type,
                        'swings': self.current_trend_swings.copy()
                    })
                
                self.trend_durations.append((trend_type, duration_bars, 'OPPOSITE_PATTERN_DETECTED'))
                
                print(f"❌ OPPOSITE PATTERN EXIT: {trend_type} → OFF at {timestamp.strftime('%Y-%m-%d %H:%M')}")
                print(f"   Detected opposite pattern: {opposite_pattern['pattern_str']}")
                print(f"   Last 4 swings: {opposite_pattern['last_4_swings']}")
                print(f"   Trend is reversing - full opposite structure formed")
                print(f"   Total trend duration: {duration_bars:.1f} bars")
                if self.correction_durations:
                    print(f"   Completed corrections: {[f'{d:.1f}' for d in self.correction_durations]} bars")
                
                self._reset_trend()
                return  # Exit immediately, don't check other conditions
        
        if self.trend == "SQ_LONG":
            # Track swing HIGHS for progression
            for i in range(self.last_processed_swing_index + 1, len(self.swing_points)):
                swing_ts, swing_price, swing_type = self.swing_points[i]
                
                # Only process swings after trend started
                if swing_ts <= self.trend_start_timestamp:
                    continue
                
                # Only care about HIGHs for progression
                if swing_type == 'HIGH':
                    # Check if this is a HIGHER high
                    if swing_price > self.last_significant_level_price:
                        # Correction = bars from last high until THIS high formed
                        correction_duration = self._calculate_bar_duration(self.last_significant_level_time, swing_ts)
                        
                        self.correction_durations.append(correction_duration)
                        self.current_trend_swings.append((swing_ts, swing_price, 'HIGH'))
                        
                        print(f"   📈 Higher high: {swing_price:.1f} at {swing_ts.strftime('%m-%d %H:%M')}")
                        print(f"      Previous high: {self.last_significant_level_price:.1f}")
                        print(f"      Correction duration: {correction_duration:.1f} bars")
                        print(f"      Correction history: {[f'{d:.1f}' for d in self.correction_durations]} bars")
                        print(f"      Threshold: {self._get_correction_threshold():.1f} bars")
                        
                        # RESET THE TIMER - track from when THIS high formed
                        self.last_significant_level_time = swing_ts  # When H3 formed
                        self.last_significant_level_price = swing_price  # New baseline
                        self.bars_since_last_progress = self._calculate_bar_duration(swing_ts, timestamp)
                        print(f"      ⏱️  TIMER RESET! Currently at {self.bars_since_last_progress:.1f} bars since this high")
                    else:
                        print(f"   ⚠️  Swing high {swing_price:.1f} at {swing_ts.strftime('%m-%d %H:%M')} not higher than {self.last_significant_level_price:.1f} (ignored)")
                
                # Mark this swing as processed
                self.last_processed_swing_index = i
            
            # Check if timer exceeded threshold (normal correction timeout)
            if self._is_correction_too_long():
                duration_bars = self._calculate_bar_duration(self.trend_start_timestamp, timestamp)
                self.trend_durations.append(('SQ_LONG', duration_bars, 'CORRECTION_TIMEOUT'))
                
                # Save the swing sequence for this trend
                if hasattr(self, 'current_trend_swings') and self.current_trend_swings:
                    self.trend_swing_sequences.append({
                        'entry_time': self.trend_start_timestamp,
                        'exit_time': timestamp,
                        'trend_type': 'SQ_LONG',
                        'swings': self.current_trend_swings.copy()
                    })
                
                print(f"❌ CORRECTION EXIT: SQ_LONG → OFF at {timestamp.strftime('%Y-%m-%d %H:%M')}")
                print(f"   ⏱️  Bars since last higher high: {self.bars_since_last_progress:.1f}")
                print(f"   Threshold: {self._get_correction_threshold():.1f} bars")
                print(f"   Correction history: {[f'{d:.1f}' for d in self.correction_durations]} bars")
                print(f"   Total trend duration: {duration_bars:.1f} bars")
                self._reset_trend()
        
        elif self.trend == "SQ_SHORT":
            # Track swing LOWS for progression
            for i in range(self.last_processed_swing_index + 1, len(self.swing_points)):
                swing_ts, swing_price, swing_type = self.swing_points[i]
                
                # Only process swings after trend started
                if swing_ts <= self.trend_start_timestamp:
                    continue
                
                # Only care about LOWs for progression
                if swing_type == 'LOW':
                    # Check if this is a LOWER low
                    if swing_price < self.last_significant_level_price:
                        # Correction = bars from last low until THIS low formed
                        correction_duration = self._calculate_bar_duration(self.last_significant_level_time, swing_ts)
                        
                        self.correction_durations.append(correction_duration)
                        self.current_trend_swings.append((swing_ts, swing_price, 'LOW'))
                        
                        print(f"   📉 Lower low: {swing_price:.1f} at {swing_ts.strftime('%m-%d %H:%M')}")
                        print(f"      Previous low: {self.last_significant_level_price:.1f}")
                        print(f"      Correction duration: {correction_duration:.1f} bars")
                        print(f"      Correction history: {[f'{d:.1f}' for d in self.correction_durations]} bars")
                        print(f"      Threshold: {self._get_correction_threshold():.1f} bars")
                        
                        # RESET THE TIMER - track from when THIS low formed
                        self.last_significant_level_time = swing_ts  # When L3 formed
                        self.last_significant_level_price = swing_price  # New baseline
                        self.bars_since_last_progress = self._calculate_bar_duration(swing_ts, timestamp)
                        print(f"      ⏱️  TIMER RESET! Currently at {self.bars_since_last_progress:.1f} bars since this low")
                    else:
                        print(f"   ⚠️  Swing low {swing_price:.1f} at {swing_ts.strftime('%m-%d %H:%M')} not lower than {self.last_significant_level_price:.1f} (ignored)")
                
                # Mark this swing as processed
                self.last_processed_swing_index = i
            
            # Check if timer exceeded threshold (normal correction timeout)
            if self._is_correction_too_long():
                duration_bars = self._calculate_bar_duration(self.trend_start_timestamp, timestamp)
                self.trend_durations.append(('SQ_SHORT', duration_bars, 'CORRECTION_TIMEOUT'))
                
                # Save the swing sequence for this trend
                if hasattr(self, 'current_trend_swings') and self.current_trend_swings:
                    self.trend_swing_sequences.append({
                        'entry_time': self.trend_start_timestamp,
                        'exit_time': timestamp,
                        'trend_type': 'SQ_SHORT',
                        'swings': self.current_trend_swings.copy()
                    })
                
                print(f"❌ CORRECTION EXIT: SQ_SHORT → OFF at {timestamp.strftime('%Y-%m-%d %H:%M')}")
                print(f"   ⏱️  Bars since last lower low: {self.bars_since_last_progress:.1f}")
                print(f"   Threshold: {self._get_correction_threshold():.1f} bars")
                print(f"   Correction history: {[f'{d:.1f}' for d in self.correction_durations]} bars")
                print(f"   Total trend duration: {duration_bars:.1f} bars")
                self._reset_trend()
    
    def _check_opposite_pattern(self) -> dict:
        """
        Check if last 4 swings form a valid OPPOSITE pattern
        
        If in SQ_LONG: Check for DOWNTREND pattern (H→L→H→L with H2<H1 AND L2<L1)
        If in SQ_SHORT: Check for UPTREND pattern (L→H→L→H with L2>L1 AND H2>H1)
        
        Returns dict with 'detected' flag and pattern details
        """
        if len(self.swing_points) < 4:
            return {'detected': False}
        
        last_4 = self.swing_points[-4:]
        types = [s[2] for s in last_4]
        prices = [s[1] for s in last_4]
        timestamps = [s[0] for s in last_4]
        
        pattern_str = "→".join([f"{s[2][0]}:{s[1]:.1f}" for s in last_4])
        
        # If in LONG, check for opposite DOWNTREND pattern
        if self.trend == "SQ_LONG":
            if types == ['HIGH', 'LOW', 'HIGH', 'LOW']:
                h1, l1, h2, l2 = prices
                # Valid downtrend: H2 < H1 AND L2 < L1 (strict)
                if h2 < h1 and l2 < l1:
                    return {
                        'detected': True,
                        'pattern_type': 'DOWNTREND',
                        'pattern_str': pattern_str,
                        'last_4_swings': [f"{t[2]}:{t[1]:.1f}@{t[0].strftime('%m-%d %H:%M')}" for t in last_4]
                    }
        
        # If in SHORT, check for opposite UPTREND pattern
        elif self.trend == "SQ_SHORT":
            if types == ['LOW', 'HIGH', 'LOW', 'HIGH']:
                l1, h1, l2, h2 = prices
                # Valid uptrend: L2 > L1 AND H2 > H1 (strict)
                if l2 > l1 and h2 > h1:
                    return {
                        'detected': True,
                        'pattern_type': 'UPTREND',
                        'pattern_str': pattern_str,
                        'last_4_swings': [f"{t[2]}:{t[1]:.1f}@{t[0].strftime('%m-%d %H:%M')}" for t in last_4]
                    }
        
        return {'detected': False}
    
    def _get_correction_threshold(self) -> float:
        """
        Calculate the correction time threshold (in bars)
        
        Rules (exit if current correction > threshold):
        - 3+ corrections: average of last 3 × 3
        - 2 corrections: average of last 2 × 3
        - 1 correction: that correction × 3
        """
        num_corrections = len(self.correction_durations)
        
        if num_corrections >= 3:
            avg_of_last_3 = sum(self.correction_durations[-3:]) / 3
            return avg_of_last_3 * 3
        elif num_corrections == 2:
            avg_of_2 = sum(self.correction_durations) / 2
            return avg_of_2 * 3
        elif num_corrections == 1:
            return self.correction_durations[0] * 3
        else:
            # No previous corrections - never exit on first correction
            return float('inf')
    
    def _is_correction_too_long(self) -> bool:
        """Check if continuous timer exceeds threshold"""
        threshold = self._get_correction_threshold()
        return self.bars_since_last_progress > threshold
    
    def _get_pattern_info(self) -> dict:
        """
        Analyze last 4 swings for valid entry patterns
        Returns all pattern information needed for triggering
        """
        if len(self.swing_points) < 4:
            return {
                'has_uptrend': False,
                'has_downtrend': False,
                'pattern_indices': None,
                'pattern_str': 'Incomplete'
            }
        
        last_4 = self.swing_points[-4:]
        types = [s[2] for s in last_4]
        prices = [s[1] for s in last_4]
        indices = tuple(range(len(self.swing_points) - 4, len(self.swing_points)))
        
        pattern_str = "→".join([f"{s[2][0]}:{s[1]:.1f}" for s in last_4])
        
        result = {
            'has_uptrend': False,
            'has_downtrend': False,
            'pattern_indices': indices,
            'pattern_str': pattern_str
        }
        
        # Check UPTREND: L1→H1→L2→H2 with L2>L1 AND H2>H1
        if types == ['LOW', 'HIGH', 'LOW', 'HIGH']:
            l1, h1, l2, h2 = prices
            if l2 > l1 and h2 > h1:  # Strict: BOTH conditions required
                result['has_uptrend'] = True
                result['l1'] = l1  # Invalidation level
                result['h1'] = h1
                result['l2'] = l2
                result['h2'] = h2  # Trigger level
        
        # Check DOWNTREND: H1→L1→H2→L2 with H2<H1 AND L2<L1
        elif types == ['HIGH', 'LOW', 'HIGH', 'LOW']:
            h1, l1, h2, l2 = prices
            if h2 < h1 and l2 < l1:  # Strict: BOTH conditions required
                result['has_downtrend'] = True
                result['h1'] = h1  # Invalidation level
                result['l1'] = l1
                result['h2'] = h2
                result['l2'] = l2  # Trigger level
        
        return result
    
    def _check_minute_invalidation(self, timestamp: pd.Timestamp, price: float):
        """Check invalidation on every minute bar"""
        if self.trend_invalidation_level is None:
            return
        
        invalidated = False
        
        if self.trend == "SQ_LONG" and price < self.trend_invalidation_level:
            invalidated = True
            duration_bars = self._calculate_bar_duration(self.trend_start_timestamp, timestamp)
            self.trend_durations.append(('SQ_LONG', duration_bars, 'L1_INVALIDATION'))
            
            # Save the swing sequence for this trend
            if hasattr(self, 'current_trend_swings') and self.current_trend_swings:
                self.trend_swing_sequences.append({
                    'entry_time': self.trend_start_timestamp,
                    'exit_time': timestamp,
                    'trend_type': 'SQ_LONG',
                    'swings': self.current_trend_swings.copy()
                })
            
            print(f"❌ L1 INVALIDATION: SQ_LONG → OFF at {timestamp.strftime('%Y-%m-%d %H:%M')}")
            print(f"   Price {price:.1f} < L1 invalidation {self.trend_invalidation_level:.1f}")
            print(f"   Trend duration: {duration_bars:.1f} bars")
            if self.correction_durations:
                print(f"   Completed corrections: {[f'{d:.1f}' for d in self.correction_durations]} bars")
            
        elif self.trend == "SQ_SHORT" and price > self.trend_invalidation_level:
            invalidated = True
            duration_bars = self._calculate_bar_duration(self.trend_start_timestamp, timestamp)
            self.trend_durations.append(('SQ_SHORT', duration_bars, 'H1_INVALIDATION'))
            
            # Save the swing sequence for this trend
            if hasattr(self, 'current_trend_swings') and self.current_trend_swings:
                self.trend_swing_sequences.append({
                    'entry_time': self.trend_start_timestamp,
                    'exit_time': timestamp,
                    'trend_type': 'SQ_SHORT',
                    'swings': self.current_trend_swings.copy()
                })
            
            print(f"❌ H1 INVALIDATION: SQ_SHORT → OFF at {timestamp.strftime('%Y-%m-%d %H:%M')}")
            print(f"   Price {price:.1f} > H1 invalidation {self.trend_invalidation_level:.1f}")
            print(f"   Trend duration: {duration_bars:.1f} bars")
            if self.correction_durations:
                print(f"   Completed corrections: {[f'{d:.1f}' for d in self.correction_durations]} bars")
        
        if invalidated:
            self._reset_trend()
    
    def _clean_old_data(self, current_timestamp: pd.Timestamp):
        """Don't clean data - keep all bars for full analysis"""
        # DISABLED: We want to keep all data for the full period
        # cutoff_time = current_timestamp - pd.Timedelta(hours=self.lookback_hours)
        # self.bars = [bar for bar in self.bars if bar[0] >= cutoff_time]
        pass
    
    def get_trend_state(self) -> str:
        return self.trend
    
    def get_swing_summary(self) -> dict:
        """Get swing analysis"""
        # Only detect patterns when trend is OFF (avoid confusion during active trends)
        if self.trend == "OFF":
            pattern_info = self._get_pattern_info()
        else:
            # Don't show pattern info during active trend - it's meaningless
            pattern_info = {
                'pattern_str': 'N/A (trend active)',
                'has_uptrend': False,
                'has_downtrend': False
            }
        
        return {
            "date": self.current_date,
            "trend": self.trend,
            "bars_since_last_progress": self.bars_since_last_progress,
            "correction_threshold": self._get_correction_threshold() if self.trend != "OFF" else 0,
            "completed_corrections": len(self.correction_durations),
            "correction_durations": self.correction_durations.copy() if self.correction_durations else [],
            "total_raw_swings": len(self.raw_swings),
            "total_filtered_swings": len(self.swing_points),
            "pattern": pattern_info['pattern_str'],
            "has_valid_uptrend": pattern_info['has_uptrend'],
            "has_valid_downtrend": pattern_info['has_downtrend'],
            "trigger_high": pattern_info.get('h2'),
            "trigger_low": pattern_info.get('l2'),
            "invalidation": self.trend_invalidation_level
        }
    
    def get_lag_bars(self) -> int:
        """Return the lag in bars (confirmation bars needed)"""
        return self.confirmation_bars