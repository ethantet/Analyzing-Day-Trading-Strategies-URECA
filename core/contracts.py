"""
Contract Specifications for Futures Backtesting - UPDATED FOR FX SUPPORT
=========================================================================

✅ ALL FIXES + FX DYNAMIC PIP VALUE SUPPORT:
1. NKD trading_start_hour: 18 → 19
2. BTC trading_start_hour: 18 → 19
3. pip_size added for universal price-to-pip conversion
4. instrument_type added ('FUTURE' or 'FX')
5. contract_size added for FX pairs (100,000 = 1 standard lot)

For FUTURES:
- instrument_type = 'FUTURE'
- point_value = FIXED (never changes)
- pip_size = 1.0 (1 point = 1 price unit, no conversion)

For FX:
- instrument_type = 'FX'
- contract_size = 100,000 (1 standard lot)
- point_value = IGNORED (calculated dynamically based on price)
- pip_size = 0.0001 (EUR/USD) or 0.01 (USD/JPY)

All times are in exchange local time (24-hour format).
"""

CONTRACT_SPECS = {
    'ES': {
        'name': 'E-mini S&P 500',
        'data_file': 'ES_SP500_Mini_Futures_1min.txt',
        'instrument_type': 'FUTURE',
        'point_value': 50,              
        'pip_size': 1.0,               
        'tick_size': 0.25,             
        'currency': 'USD',
        'initial_margin_pct': 10.0,
        'maintenance_margin_pct': 7.5,
        'exchange': 'CME',
        'timezone': 'America/Chicago',
        
        # Trading Hours (Chicago time)
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
        
        'min_or_range': 8,
        'profit_target_multiplier': 1.0,
    },
    
    'GC': {
        'name': 'Gold Futures (Full Contract)',
        'data_file': 'GC_1min.txt',
        'instrument_type': 'FUTURE',
        'point_value': 100,            
        'pip_size': 1.0,                
        'tick_size': 0.10,
        'currency': 'USD',
        'initial_margin_pct': 8.0,
        'maintenance_margin_pct': 6.0,
        'exchange': 'COMEX',
        'timezone': 'America/New_York',
        
        # Trading Hours (New York time)
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
        'profit_target_multiplier': 1.0,
    },
    
    'FDAX': {
        'name': 'DAX Futures',
        'data_file': 'FDAX_1min.txt',
        'instrument_type': 'FUTURE',
        'point_value': 25,             
        'pip_size': 1.0,              
        'tick_size': 0.50,
        'currency': 'EUR',
        'initial_margin_pct': 10.0,
        'maintenance_margin_pct': 7.5,
        'exchange': 'EUREX',
        'timezone': 'Europe/Berlin',
        
        # Trading Hours (Berlin/CET time)
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
        
        'min_or_range': 10,
        'profit_target_multiplier': 2.0,
    },
    
    'NKD': {
        'name': 'Nikkei 225 Mini Futures (CME)',
        'data_file': 'NKD_1min.txt',
        'instrument_type': 'FUTURE',
        'point_value': 5,              
        'pip_size': 1.0,                
        'tick_size': 5,                
        'currency': 'USD',
        'initial_margin_pct': 10.0,
        'maintenance_margin_pct': 7.5,
        'exchange': 'CME',
        'timezone': 'America/Chicago',
        
        # Trading Hours (Chicago time) - Evening session
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
        
        'min_or_range': 50,
        'profit_target_multiplier': 1.0,
    },
    
    'BTC': {
        'name': 'Bitcoin Futures (CME)',
        'data_file': 'BTC_1min.txt',
        'instrument_type': 'FUTURE',
        'point_value': 5,            
        'pip_size': 1.0,               
        'tick_size': 5,              
        'currency': 'USD',
        'initial_margin_pct': 10.0,
        'maintenance_margin_pct': 7.5,
        'exchange': 'CME',
        'timezone': 'America/Chicago',
        
        # Trading Hours (Chicago time) - Evening session
        'or_start_hour': 9,
        'or_start_minute': 30,
        'or_end_hour': 10,
        'or_end_minute': 15,
        'trend_check_hour': 10,
        'trend_check_minute': 15,
        'trading_start_hour': 10,
        'trading_start_minute': 15,
        'trading_end_hour': 13,
        'trading_end_minute': 0,
        'trading_end_next_day': False,
        
        'min_or_range': 100,          
        'profit_target_multiplier': 2.0,
    },
    
    'NG': {
        'name': 'Natural Gas Futures (NYMEX)',
        'data_file': 'NG_1min.txt',
        'instrument_type': 'FUTURE',
        'point_value': 10000,           
        'pip_size': 1.0,                 
        'tick_size': 0.001,              
        'currency': 'USD',
        'initial_margin_pct': 10.0,
        'maintenance_margin_pct': 7.5,
        'exchange': 'NYMEX',
        'timezone': 'America/New_York',
        
        # Trading Hours (NY time) - Evening session
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
        'profit_target_multiplier': 2.0,
    },
    
    'CL': {
        'name': 'Crude Oil Futures (NYMEX)',
        'data_file': 'CL_1min.txt',
        'instrument_type': 'FUTURE',
        'point_value': 1000,            
        'pip_size': 1.0,                 
        'tick_size': 0.01,               
        'currency': 'USD',
        'initial_margin_pct': 10.0,
        'maintenance_margin_pct': 7.5,
        'exchange': 'NYMEX',
        'timezone': 'America/New_York',
        
        # Trading Hours (NY time) - Evening session
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
        'profit_target_multiplier': 2.0,
    },
    
    'VX': {
        'name': 'VIX Futures (CFE)',
        'data_file': 'VX_1min.txt',
        'instrument_type': 'FUTURE',
        'point_value': 1000,            
        'pip_size': 1.0,                 
        'tick_size': 0.05,               
        'currency': 'USD',
        'initial_margin_pct': 15.0,      
        'maintenance_margin_pct': 11.25,
        'exchange': 'CFE',
        'timezone': 'America/Chicago',
        
        # Trading Hours (Chicago time) - Regular session
        'or_start_hour': 9,
        'or_start_minute': 30,
        'or_end_hour': 10,
        'or_end_minute': 15,
        'trend_check_hour': 10,
        'trend_check_minute': 15,
        'trading_start_hour': 10,
        'trading_start_minute': 15,
        'trading_end_hour': 13,
        'trading_end_minute': 0,
        'trading_end_next_day': False,
        
        'min_or_range': 0.3,             
        'profit_target_multiplier': 1.0,
    },
        
    'ZN': {
        'name': '10-Year T-Note Futures (CBOT)',
        'data_file': 'ZN_1min.txt',
        'instrument_type': 'FUTURE',
        'point_value': 1000,             
        'pip_size': 1.0,                 
        'tick_size': 0.015625,           
        'currency': 'USD',
        'initial_margin_pct': 5.0,       
        'maintenance_margin_pct': 3.75,
        'exchange': 'CBOT',
        'timezone': 'America/Chicago',
        
        # Trading Hours (Chicago time) - Evening session
        'or_start_hour': 9,
        'or_start_minute': 30,
        'or_end_hour': 10,
        'or_end_minute': 15,
        'trend_check_hour': 10,
        'trend_check_minute': 15,
        'trading_start_hour': 10,
        'trading_start_minute': 15,
        'trading_end_hour': 13,
        'trading_end_minute': 0,
        'trading_end_next_day': False,
        
        'min_or_range': 0.125,           
        'profit_target_multiplier': 2.0,
    },
    
    'YM': {
        'name': 'E-mini Dow ($5) Futures (CBOT)',
        'data_file': 'YM_1min.txt',
        'instrument_type': 'FUTURE',
        'point_value': 5,               
        'pip_size': 1.0,                
        'tick_size': 1,                  
        'currency': 'USD',
        'initial_margin_pct': 10.0,
        'maintenance_margin_pct': 7.5,
        'exchange': 'CBOT',
        'timezone': 'America/Chicago',
        
        # Trading Hours (Chicago time) - Evening session
        'or_start_hour': 9,
        'or_start_minute': 30,
        'or_end_hour': 10,
        'or_end_minute': 15,
        'trend_check_hour': 10,
        'trend_check_minute': 15,
        'trading_start_hour': 10,
        'trading_start_minute': 15,
        'trading_end_hour': 13,
        'trading_end_minute': 0,
        'trading_end_next_day': False,
        
        'min_or_range': 50,             
        'profit_target_multiplier': 1.0,
    },
    
    'NQ': {
        'name': 'E-mini Nasdaq 100 Futures (CME)',
        'data_file': 'NQ_1min.txt',
        'instrument_type': 'FUTURE',
        'point_value': 20,              
        'pip_size': 1.0,                
        'tick_size': 0.25,              
        'currency': 'USD',
        'initial_margin_pct': 10.0,
        'maintenance_margin_pct': 7.5,
        'exchange': 'CME',
        'timezone': 'America/Chicago',
        
        # Trading Hours (Chicago time) - Evening session
        'or_start_hour': 9,
        'or_start_minute': 30,
        'or_end_hour': 10,
        'or_end_minute': 15,
        'trend_check_hour': 10,
        'trend_check_minute': 15,
        'trading_start_hour': 10,
        'trading_start_minute': 15,
        'trading_end_hour': 13,
        'trading_end_minute': 0,
        'trading_end_next_day': False,
        
        'min_or_range': 15,              
        'profit_target_multiplier': 1.0,
    },
    
    'FGBL': {
        'name': 'Euro-Bund Futures (EUREX)',
        'data_file': 'FGBL_1min.txt',
        'instrument_type': 'FUTURE',
        'point_value': 1000,             
        'pip_size': 1.0,                 
        'tick_size': 0.01,               
        'currency': 'EUR',
        'initial_margin_pct': 5.0,       
        'maintenance_margin_pct': 3.75,
        'exchange': 'EUREX',
        'timezone': 'Europe/Berlin',
        
        # Trading Hours (Berlin/CET time)
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
        
        'min_or_range': 0.20,            
        'profit_target_multiplier': 2.0,
    },
    
    'EURUSD': {
        'name': 'EUR/USD Spot FX',
        'data_file': 'EURUSD_1min.txt',
        'instrument_type': 'FX',         # ⭐ FX PAIR
        'contract_size': 100000,          # ⭐ 1 standard lot = 100,000 units
        'point_value': 10,                # Ignored for FX (calculated dynamically)
        'pip_size': 0.0001,              # 1 pip = 0.0001 (4th decimal)
        'tick_size': 0.00001,            # 1 pipette
        'currency': 'USD',
        'initial_margin_pct': 2.0,       # Lower margin for FX
        'maintenance_margin_pct': 1.5,
        'exchange': 'FX',
        'timezone': 'America/New_York',
        
        # Trading Hours (NY time) - 24hr FX market
        'or_start_hour': 18,             # 6 PM (NY evening)
        'or_start_minute': 0,
        'or_end_hour': 19,
        'or_end_minute': 0,
        'trend_check_hour': 19,
        'trend_check_minute': 0,
        'trading_start_hour': 19,
        'trading_start_minute': 0,
        'trading_end_hour': 23,          # 11 PM
        'trading_end_minute': 0,
        'trading_end_next_day': False,   
        
        'min_or_range': 0.0020,          # 20 pips
        'profit_target_multiplier': 1.5,
    },
    
    'USDJPY': {
        'name': 'USD/JPY Spot FX',
        'data_file': 'USDJPY_1min.txt',
        'instrument_type': 'FX',         # ⭐ FX PAIR
        'contract_size': 100000,          # ⭐ 1 standard lot = 100,000 units
        'point_value': 8,                 # Ignored for FX (calculated dynamically)
        'pip_size': 0.01,                 # 1 pip = 0.01 (2nd decimal for JPY pairs)
        'tick_size': 0.001,               # 0.1 pip
        'currency': 'USD',
        'initial_margin_pct': 2.0,        # Lower margin for FX
        'maintenance_margin_pct': 1.5,
        'exchange': 'FX',
        'timezone': 'America/New_York',
        
        # Trading Hours (NY time) - 24hr FX market
        'or_start_hour': 18,              # 6 PM (NY evening)
        'or_start_minute': 0,
        'or_end_hour': 19,
        'or_end_minute': 0,
        'trend_check_hour': 19,
        'trend_check_minute': 0,
        'trading_start_hour': 19,
        'trading_start_minute': 0,
        'trading_end_hour': 23,           # 11 PM
        'trading_end_minute': 0,
        'trading_end_next_day': False,    
        
        'min_or_range': 0.20,             # 20 pips (0.20 for JPY = 20 pips)
        'profit_target_multiplier': 2.0,
    },
    'RTY': {
        'name': 'E-mini Russell 2000 Futures (CME)',
        'data_file': 'RTY_1min.txt',
        'instrument_type': 'FUTURE',
        'point_value': 50.0,
        'pip_size': 0.10,
        'tick_size': 0.10,                
        'currency': 'USD',
        'initial_margin_pct': 10.0,
        'maintenance_margin_pct': 7.5,
        'exchange': 'CME',
        'timezone': 'America/Chicago',
        
        # Trading Hours (Chicago/CST time)
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
        'profit_target_multiplier': 1.2,
    },
}


def get_contract_spec(symbol):
    """Get contract specifications for a given symbol."""
    symbol = symbol.upper()
    
    if symbol not in CONTRACT_SPECS:
        available = ', '.join(CONTRACT_SPECS.keys())
        raise ValueError(
            f"Unknown contract symbol: '{symbol}'\n"
            f"Available contracts: {available}\n"
            f"Use list_contracts() to see details."
        )
    
    return CONTRACT_SPECS[symbol].copy()


def list_contracts():
    """Display all available contracts with their specifications."""
    print("\n" + "=" * 100)
    print("AVAILABLE FUTURES & FX CONTRACTS")
    print("=" * 100)
    print(f"\n{'Symbol':<8} {'Name':<35} {'Type':<8} {'Exchange':<10} {'Point Value':<15} {'Pip Size':<12}")
    print("-" * 100)
    
    for symbol, spec in sorted(CONTRACT_SPECS.items()):
        inst_type = spec.get('instrument_type', 'FUTURE')
        
        if inst_type == 'FX':
            point_val = f"DYNAMIC"
        else:
            point_val = f"{spec['currency']}{spec['point_value']:,.0f}"
        
        pip_str = f"{spec['pip_size']:.5f}".rstrip('0').rstrip('.')
        print(f"{symbol:<8} {spec['name']:<35} {inst_type:<8} {spec['exchange']:<10} {point_val:<15} {pip_str:<12}")
    
    print("-" * 100)
    print(f"Total: {len(CONTRACT_SPECS)} contracts available")
    print("\n⭐ FUTURES (instrument_type='FUTURE'):")
    print("   • pip_size = 1.0 (1 point = 1 price unit)")
    print("   • point_value = FIXED (never changes)")
    print("\n⭐ FX (instrument_type='FX'):")
    print("   • contract_size = 100,000 (1 standard lot)")
    print("   • pip_size = 0.0001 for EUR/USD (4th decimal)")
    print("   • pip_size = 0.01 for USD/JPY (2nd decimal)")
    print("   • point_value = DYNAMIC (varies with exchange rate)")
    print("\n📊 FX Pip Value Examples:")
    print("   • EUR/USD at 1.0850: 1 pip = 0.0001 × 100,000 = $10.00")
    print("   • USD/JPY at 150.00: 1 pip = (0.01 × 100,000) / 150 = $6.67")
    print("   • USD/JPY at 140.00: 1 pip = (0.01 × 100,000) / 140 = $7.14\n")


def get_contract_info(symbol):
    """Print detailed information about a specific contract."""
    spec = get_contract_spec(symbol)
    inst_type = spec.get('instrument_type', 'FUTURE')
    
    print("\n" + "=" * 80)
    print(f"CONTRACT SPECIFICATION: {symbol}")
    print("=" * 80)
    
    print(f"\n📊 Basic Info:")
    print(f"   Name: {spec['name']}")
    print(f"   Type: {inst_type}")
    print(f"   Exchange: {spec['exchange']} ({spec['timezone']})")
    print(f"   Data File: {spec['data_file']}")
    
    print(f"\n💰 Contract Specifications:")
    
    if inst_type == 'FX':
        print(f"   Contract Size: {spec['contract_size']:,} units (1 standard lot)")
        pip_str = f"{spec['pip_size']:.5f}".rstrip('0').rstrip('.')
        print(f"   Pip Size: {pip_str}")
        print(f"   Tick Size: {spec['tick_size']}")
        print(f"   Currency: {spec['currency']}")
        print(f"\n   ⚠️ Point Value: DYNAMIC (calculated per trade)")
        
        if 'JPY' in symbol:
            print(f"\n   💱 USD/JPY Pip Value Calculation:")
            print(f"      Formula: (pip_size × contract_size) / current_price")
            print(f"      At 150.00: (0.01 × 100,000) / 150 = $6.67 per pip")
            print(f"      At 140.00: (0.01 × 100,000) / 140 = $7.14 per pip")
            print(f"      At 160.00: (0.01 × 100,000) / 160 = $6.25 per pip")
        else:
            print(f"\n   💱 EUR/USD Pip Value:")
            print(f"      Formula: pip_size × contract_size")
            print(f"      Always: 0.0001 × 100,000 = $10.00 per pip (constant)")
    else:
        print(f"   Point Value: {spec['currency']}{spec['point_value']:,.0f} (FIXED)")
        pip_str = f"{spec['pip_size']:.5f}".rstrip('0').rstrip('.')
        print(f"   Pip Size: {pip_str}")
        print(f"   Tick Size: {spec['tick_size']}")
        print(f"   Currency: {spec['currency']}")
        print(f"\n   ℹ️ Futures: 1 pip = 1 point = {spec['currency']}{spec['point_value']:,.0f}")
    
    print(f"\n💼 Margin Requirements:")
    print(f"   Initial Margin: {spec['initial_margin_pct']:.1f}%")
    print(f"   Maintenance Margin: {spec['maintenance_margin_pct']:.1f}%")
    
    if inst_type == 'FX':
        print(f"   Note: Lower margins for FX due to high liquidity")
    
    print(f"\n⏰ Trading Schedule ({spec['timezone']}):")
    print(f"   Opening Range: {spec['or_start_hour']:02d}:{spec.get('or_start_minute', 0):02d} - {spec['or_end_hour']:02d}:{spec.get('or_end_minute', 0):02d}")
    print(f"   Trend Check: {spec['trend_check_hour']:02d}:{spec.get('trend_check_minute', 0):02d}")
    
    if spec.get('trading_end_next_day', False):
        print(f"   Trading Window: {spec['trading_start_hour']:02d}:{spec.get('trading_start_minute', 0):02d} - {spec['trading_end_hour']:02d}:{spec['trading_end_minute']:02d} (⭐ NEXT DAY)")
        print(f"   Session Type: OVERNIGHT (crosses midnight)")
    else:
        print(f"   Trading Window: {spec['trading_start_hour']:02d}:{spec.get('trading_start_minute', 0):02d} - {spec['trading_end_hour']:02d}:{spec['trading_end_minute']:02d}")
        print(f"   Session Type: Same-day")
    
    print(f"\n🎯 Strategy Parameters:")
    print(f"   Min OR Range: {spec['min_or_range']}")
    print(f"   Profit Target Multiplier: {spec['profit_target_multiplier']}x")
    
    print("=" * 80 + "\n")


def validate_data_file(symbol):
    """Check if data file exists for a contract."""
    import os
    spec = get_contract_spec(symbol)
    return os.path.exists(spec['data_file'])


if __name__ == "__main__":
    print("Contract Specifications Module - FX DYNAMIC PIP VALUE SUPPORT")
    print("=" * 80)
    print("\n✅ UPDATES APPLIED:")
    print("   1. instrument_type added ('FUTURE' or 'FX')")
    print("   2. contract_size added for FX (100,000 = 1 standard lot)")
    print("   3. FX pairs use DYNAMIC pip value calculation")
    print("   4. All existing values preserved\n")
    
    list_contracts()
    
    print("\nDetailed Info for FX Contracts:")
    for symbol in ['EURUSD', 'USDJPY']:
        get_contract_info(symbol)
    
    print("\nQuick Comparison - Futures vs FX:")
    print("=" * 80)
    print("\nFDAX (Future):")
    get_contract_info('FDAX')
    print("\nEURUSD (FX):")
    get_contract_info('EURUSD')