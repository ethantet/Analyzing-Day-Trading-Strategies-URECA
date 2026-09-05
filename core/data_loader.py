import pandas as pd
import numpy as np

def create_resampled_bars(minute_data, timeframe='1H'):
    """
    Create resampled bars from minute data - TradingView Compatible
    Works with any timeframe: 15min, 1H, 4H, etc.
    
    Args:
        minute_data: DataFrame with minute OHLC data (index = timestamp)
        timeframe: Pandas resample string ('15T', '1H', '4H', etc.)
                   '15T' or '15min' = 15-minute bars
                   '1H' or '60T' = 1-hour bars
                   '4H' = 4-hour bars
    
    Bar Timing (TradingView Compatible):
        2:00 bar = 2:00 to 2:59 data (bar starts at 2:00, closes at 2:59)
        3:00 bar = 3:00 to 3:59 data (bar starts at 3:00, closes at 3:59)
    
    Returns:
        DataFrame with resampled OHLCV bars
    """
    print(f"Creating {timeframe} bars from minute data...")
    
    # Use pandas resample - handles all timeframes consistently
    resampled = minute_data.resample(timeframe).agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum'
    }).dropna()
    
    print(f"  Created {len(resampled):,} {timeframe} bars")
    print(f"  Date range: {resampled.index.min()} to {resampled.index.max()}")
    
    return resampled


def verify_resampled_bars(minute_data, resampled_data, timeframe='1H', num_tests=5):
    """
    Verify resampled bars contain the right minute data
    Returns True if timing is correct
    
    Args:
        minute_data: Original minute bars
        resampled_data: Resampled bars to verify
        timeframe: Timeframe used for resampling
        num_tests: Number of bars to verify
    """
    print(f"\nVERIFYING {timeframe} BAR TIMING (TradingView Style)")
    print("-" * 50)
    
    # Get first day with data
    first_date = resampled_data.index[0].date()
    day_minute = minute_data[minute_data.index.date == first_date]
    day_resampled = resampled_data[resampled_data.index.date == first_date]
    
    if len(day_resampled) == 0:
        print("No resampled bars to verify")
        return False
    
    correct_count = 0
    
    # Parse timeframe to get duration
    if timeframe.endswith('T') or timeframe.endswith('min'):
        # Extract minutes (e.g., '15T' or '15min')
        minutes = int(timeframe.replace('T', '').replace('min', ''))
        duration = pd.Timedelta(minutes=minutes)
    elif timeframe.endswith('H'):
        # Extract hours (e.g., '1H' or '4H')
        hours = int(timeframe.replace('H', ''))
        duration = pd.Timedelta(hours=hours)
    else:
        print(f"⚠️  Unknown timeframe format: {timeframe}")
        return False
    
    for i in range(min(num_tests, len(day_resampled))):
        bar_time = day_resampled.index[i]
        bar_data = day_resampled.iloc[i]
        
        # Calculate expected minute data range (TradingView style)
        start_time = bar_time
        end_time = bar_time + duration - pd.Timedelta(seconds=1)
        
        # Get actual minute data in this range
        minute_subset = day_minute[
            (day_minute.index >= start_time) & 
            (day_minute.index <= end_time)
        ]
        
        if len(minute_subset) > 0:
            # Calculate expected values
            expected_open = minute_subset['open'].iloc[0]
            expected_high = minute_subset['high'].max()
            expected_low = minute_subset['low'].min()
            expected_close = minute_subset['close'].iloc[-1]
            
            # Check matches
            open_match = abs(expected_open - bar_data['open']) < 0.01
            high_match = abs(expected_high - bar_data['high']) < 0.01
            low_match = abs(expected_low - bar_data['low']) < 0.01
            close_match = abs(expected_close - bar_data['close']) < 0.01
            
            all_match = open_match and high_match and low_match and close_match
            
            if all_match:
                correct_count += 1
                print(f"{bar_time.strftime('%Y-%m-%d %H:%M')}: ✓ (includes {start_time.strftime('%H:%M')}-{end_time.strftime('%H:%M')})")
            else:
                print(f"{bar_time.strftime('%Y-%m-%d %H:%M')}: ✗")
                print(f"  Expected: O={expected_open:.1f} H={expected_high:.1f} L={expected_low:.1f} C={expected_close:.1f}")
                print(f"  Actual:   O={bar_data['open']:.1f} H={bar_data['high']:.1f} L={bar_data['low']:.1f} C={bar_data['close']:.1f}")
    
    success_rate = (correct_count / min(num_tests, len(day_resampled))) * 100
    print(f"\nResult: {correct_count}/{min(num_tests, len(day_resampled))} bars correct ({success_rate:.0f}%)")
    
    return success_rate == 100


def load_and_create_resampled(data_file, timeframe='1H'):
    """
    Load minute data and create resampled bars.
    Auto-detects date format and handles various CSV structures:
    1. YYYY-MM-DD HH:MM:SS,open,high,low,close,volume (6 cols - standard futures)
    2. YYYYMMDD,HH:MM:SS,open,high,low,close,volume (7 cols - FX separated)
    
    Args:
        data_file: Path to CSV file with minute data
        timeframe: Pandas resample string ('15T', '1H', '4H', etc.)
    
    Returns:
        tuple: (minute_data DataFrame, resampled_data DataFrame)
    """
    print(f"Loading data from {data_file}...")
    
    # First, peek at the file to understand its structure
    try:
        # Read first line to detect structure
        with open(data_file, 'r') as f:
            first_line = f.readline().strip()
            num_columns = len(first_line.split(','))
        
        print(f"  Detected {num_columns} columns in CSV")
        
        if num_columns == 6:
            # Standard futures format: datetime, open, high, low, close, volume
            df = pd.read_csv(
                data_file,
                header=None,
                names=['datetime', 'open', 'high', 'low', 'close', 'volume']
            )
            
            print(f"✓ Detected YYYY-MM-DD HH:MM:SS format (6 cols - standard futures)")
            
            # Parse the datetime column (pandas will auto-detect YYYY-MM-DD HH:MM:SS)
            df['date_time'] = pd.to_datetime(df['datetime'].astype(str).str.strip())
            
            # Drop original datetime column
            df = df.drop(['datetime'], axis=1)
            
        elif num_columns == 7:
            # FX separated format: date, time, open, high, low, close, volume
            df = pd.read_csv(
                data_file,
                header=None,
                names=['date', 'time', 'open', 'high', 'low', 'close', 'volume']
            )
            
            # FX format is always YYYYMMDD (no dashes)
            date_format = '%Y%m%d %H:%M:%S'
            print(f"✓ Detected YYYYMMDD HH:MM:SS format (7 cols - FX)")
            
            # Parse datetime with FX format
            df['date_time'] = pd.to_datetime(
                df['date'].astype(str).str.strip() + ' ' + df['time'].astype(str).str.strip(),
                format=date_format
            )
            
            # Drop original date and time columns
            df = df.drop(['date', 'time'], axis=1)
            
        else:
            raise ValueError(
                f"Unexpected number of columns ({num_columns}). "
                f"Expected 6 or 7 columns.\n"
                f"First line: {first_line}"
            )
        
    except Exception as e:
        print(f"❌ Error reading file structure: {str(e)}")
        print(f"\nPlease check your data file format. Expected formats:")
        print(f"  Format 1 (6 cols): YYYY-MM-DD HH:MM:SS,open,high,low,close,volume (standard futures)")
        print(f"  Format 2 (7 cols): YYYYMMDD,HH:MM:SS,open,high,low,close,volume (FX)")
        raise
    
    # Set datetime as index
    df = df.set_index('date_time')
    df.index.name = 'timestamp'
    
    # Sort by timestamp
    df = df.sort_index()
    
    print(f"  Date range: {df.index.min()} to {df.index.max()}")
    print(f"  Total minute bars: {len(df):,}")
    
    # Create resampled bars
    resampled = create_resampled_bars(df, timeframe)
    
    return df, resampled


# Simple test
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python bar_resampler.py <data_file> [timeframe]")
        print("Example: python bar_resampler.py data.csv 15T")
        print("Example: python bar_resampler.py data.csv 1H")
        print("Example: python bar_resampler.py data.csv 4H")
        sys.exit(1)
    
    data_file = sys.argv[1]
    timeframe = sys.argv[2] if len(sys.argv) > 2 else '1H'
    
    minute_data, resampled_data = load_and_create_resampled(data_file, timeframe)
    verify_resampled_bars(minute_data, resampled_data, timeframe, num_tests=5)