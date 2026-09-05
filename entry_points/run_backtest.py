"""
Interactive Weighted Confluence Backtest Runner
================================================

Run this file in Python - it will ask you which contract to backtest.
NO command line needed!

✅ NEW: Weighted confluence system (15min + 1H + 4H)
✅ NEW: Dynamic position sizing (0.5× → 2.0×)
✅ Supports 15min, 1H, 4H timeframes
✅ Includes pip_size parameter for forex support
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))

from contracts import get_contract_spec, list_contracts, CONTRACT_SPECS
from backtester import ProperSQ60ORBWithPlots


def run_interactive_backtest():
    """Interactive backtest - asks user for input"""
    
    print("\n" + "="*80)
    print("INTERACTIVE MULTI-TIMEFRAME BACKTESTER")
    print("="*80)
    
    # Show available contracts
    print("\nAvailable Contracts:")
    print("-" * 80)
    for symbol, spec in sorted(CONTRACT_SPECS.items()):
        print(f"  {symbol:6s} - {spec['name']:35s} ({spec['exchange']})")
    print("-" * 80)
    
    # Get contract choice
    while True:
        contract = input("\nEnter contract symbol (e.g., GC, ES, CL, EURUSD, USDJPY): ").strip().upper()
        try:
            spec = get_contract_spec(contract)
            break
        except ValueError:
            print(f"❌ Invalid contract: {contract}. Please try again.")
    
    print(f"\n✓ Selected: {spec['name']} ({contract})")
    
    # Show pip_size info for forex
    if spec['pip_size'] != 1.0:
        print(f"   📊 Forex instrument: 1 pip = {spec['pip_size']} price units")
        print(f"   💰 Point value: {spec['currency']}{spec['point_value']} per pip")
    
    # Get main timeframe choice
    print("\n⏱️  Timeframe Selection:")
    print("   15T  - 15-minute bars")
    print("   1H   - 1-hour bars (default)")
    print("   4H   - 4-hour bars")
    
    timeframe_input = input("\n  Select timeframe (15T/1H/4H, default 1H): ").strip().upper()
    if timeframe_input == "":
        main_timeframe = "1H"
    elif timeframe_input in ["15T", "15MIN", "15"]:
        main_timeframe = "15T"
    elif timeframe_input in ["1H", "1HR", "1", "60T"]:
        main_timeframe = "1H"
    elif timeframe_input in ["4H", "4HR", "4"]:
        main_timeframe = "4H"
    else:
        print(f"⚠️  Invalid timeframe '{timeframe_input}', defaulting to 1H")
        main_timeframe = "1H"
    
    print(f"   ✓ Selected: {main_timeframe}")
    
    # Multi-timeframe alignment
    mtf_input = input("\n🎯 Enable multi-timeframe alignment? (y/n, default y): ").strip().lower()
    enable_multi_timeframe = mtf_input != 'n'
    
    # Get date range
    print("\n📅 Date Range (press Enter to use all data):")
    start_date = input("  Start date (YYYY-MM-DD) or press Enter: ").strip()
    if start_date == "":
        start_date = None
    
    end_date = input("  End date (YYYY-MM-DD) or press Enter: ").strip()
    if end_date == "":
        end_date = None
    
    # Get balance
    print("\n💰 Account Settings:")
    balance_input = input(f"  Starting balance (default 100000): ").strip()
    starting_balance = float(balance_input) if balance_input else 100000
    
    risk_input = input(f"  Risk per trade % (default 2.0): ").strip()
    risk_pct = float(risk_input) if risk_input else 2.0
    
    # Verbose?
    verbose_input = input("\n📝 Show detailed trade logs? (y/n, default y): ").strip().lower()
    verbose = verbose_input != 'n'
    
    # Confirm settings
    print("\n" + "="*80)
    print("BACKTEST CONFIGURATION")
    print("="*80)
    print(f"Contract: {spec['name']} ({contract})")
    print(f"Data File: {spec['data_file']}")
    print(f"Timeframe: {main_timeframe}")
    print(f"Multi-Timeframe: {'ENABLED' if enable_multi_timeframe else 'DISABLED'}")
    print(f"Date Range: {start_date or 'ALL'} to {end_date or 'ALL'}")
    print(f"Starting Balance: ${starting_balance:,.0f}")
    print(f"Risk per Trade: {risk_pct}%")
    print(f"Point Value: {spec['currency']}{spec['point_value']}")
    print(f"Pip Size: {spec['pip_size']} {'(forex)' if spec['pip_size'] != 1.0 else '(futures)'}")
    print(f"Detailed Logs: {'Yes' if verbose else 'No'}")
    
    if spec.get('trading_end_next_day', False):
        print(f"\n⚠️  OVERNIGHT SESSION: Trading crosses midnight!")
    
    print("="*80)
    
    confirm = input("\nProceed with backtest? (y/n): ").strip().lower()
    if confirm != 'y':
        print("\n❌ Backtest cancelled.")
        return
    
    # Run backtest
    print("\n🚀 Running backtest...\n")
    
    try:
        backtester = ProperSQ60ORBWithPlots(
            data_file=spec['data_file'],
            starting_balance=starting_balance,
            risk_per_trade_pct=risk_pct,
            profit_target_multiplier=spec['profit_target_multiplier'],
            min_or_range=spec['min_or_range'],
            start_date=start_date,
            end_date=end_date,
            verbose=verbose,
            initial_margin_pct=spec['initial_margin_pct'],
            maintenance_margin_pct=spec['maintenance_margin_pct'],
            point_value=spec['point_value'],
            pip_size=spec['pip_size'],
            instrument_name=contract,
            instrument_type=spec.get('instrument_type', 'FUTURE'),
            contract_size=spec.get('contract_size', 1),
            or_start_hour=spec['or_start_hour'],
            or_start_minute=spec.get('or_start_minute', 0),
            or_end_hour=spec['or_end_hour'],
            or_end_minute=spec.get('or_end_minute', 0),
            trend_check_hour=spec['trend_check_hour'],
            trend_check_minute=spec.get('trend_check_minute', 0),
            trading_start_hour=spec['trading_start_hour'],
            trading_start_minute=spec.get('trading_start_minute', 0),
            trading_end_hour=spec['trading_end_hour'],
            trading_end_minute=spec['trading_end_minute'],
            trading_end_next_day=spec.get('trading_end_next_day', False),
            main_timeframe=main_timeframe,
            enable_multi_timeframe=enable_multi_timeframe
        )
        
        results = backtester.run()
        backtester.print_results(results)
        
        # Generate charts
        print("\n" + "="*80)
        print("GENERATING CHARTS")
        print("="*80)
        
        print("\n1️⃣  Creating balance chart...")
        backtester.create_balance_chart()
        
        print("\n2️⃣  Creating interactive trading chart...")
        trade_fig, trade_config = backtester.create_tradingview_chart(
            show_all_or=True,
            initial_visible_bars=300
        )
        
        mtf_suffix = "MTF" if enable_multi_timeframe else "STF"
        date_str = f"{start_date or 'full'}_{end_date or 'full'}"
        html_filename = f'sq60_orb_{mtf_suffix}_{contract}_{main_timeframe}_{date_str}.html'
        trade_fig.write_html(html_filename, config=trade_config)
        print(f"    ✓ Saved: {html_filename}")
        
        if len(results['trades']) > 0:
            print("\n3️⃣  Exporting trade data...")
            csv_filename = f'sq60_orb_{mtf_suffix}_trades_{contract}_{main_timeframe}_{date_str}.csv'
            results['trades'].to_csv(csv_filename, index=False)
            print(f"    ✓ Saved: {csv_filename}")
        
        print("\n" + "="*80)
        print(f"✅ BACKTEST COMPLETE FOR {contract} ({main_timeframe})")
        print("="*80)
        
        # Quick summary
        if len(results['trades']) > 0:
            print(f"\n📊 Quick Summary:")
            print(f"   Total Return: {results['return_pct']:+.2f}%")
            print(f"   Win Rate: {results['win_rate']:.1f}%")
            print(f"   Total Trades: {results['total_trades']}")
            if 'cagr' in results:
                print(f"   CAGR: {results['cagr']:.2f}%")
            if enable_multi_timeframe and 'alignment_stats' in results:
                total = sum(results['alignment_stats'].values())
                if total > 0:
                    print(f"\n   Alignment: ", end="")
                    print(", ".join([f"{k}:{v}" for k, v in results['alignment_stats'].items()]))
        
        print("\n💡 Tip: Open the HTML file in your browser for interactive charts!")
        print("="*80 + "\n")
        
        return results, backtester
        
    except FileNotFoundError as e:
        print(f"\n❌ Error: {e}")
        print("Please make sure the data file exists in the current directory.")
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()


def run_quick_backtest(contract='GC', main_timeframe='1H', start_date=None, end_date=None, 
                       balance=100000, risk_pct=2.0, verbose=True, enable_multi_timeframe=True):
    """
    Quick backtest function - call directly from Python console or notebook.
    
    Examples:
        # Run GC with multi-timeframe (default)
        run_quick_backtest('GC')
        
        # Run ES on 15-minute main timeframe with MTF
        run_quick_backtest('ES', main_timeframe='15T')
        
        # Run FDAX on 4-hour main timeframe with MTF
        run_quick_backtest('FDAX', main_timeframe='4H')
        
        # Disable multi-timeframe (single timeframe only)
        run_quick_backtest('ES', enable_multi_timeframe=False)
        
        # Run with custom dates
        run_quick_backtest('ES', main_timeframe='1H', start_date='2024-01-01', end_date='2024-12-31')
        
        # Run CL with custom balance and higher risk
        run_quick_backtest('CL', balance=50000, risk_pct=3.0)
        
        # Run forex with multi-timeframe
        run_quick_backtest('EURUSD', main_timeframe='1H')
        run_quick_backtest('USDJPY', main_timeframe='4H')
        
        # Quiet mode
        run_quick_backtest('NQ', verbose=False)
    """
    
    spec = get_contract_spec(contract)
    
    mtf_status = "MTF ENABLED" if enable_multi_timeframe else "STF ONLY"
    print("\n" + "="*80)
    print(f"RUNNING: {spec['name']} ({contract}) | {main_timeframe} | {mtf_status}")
    print("="*80 + "\n")
    
    backtester = ProperSQ60ORBWithPlots(
        data_file=spec['data_file'],
        starting_balance=balance,
        risk_per_trade_pct=risk_pct,
        profit_target_multiplier=spec['profit_target_multiplier'],
        min_or_range=spec['min_or_range'],
        start_date=start_date,
        end_date=end_date,
        verbose=verbose,
        initial_margin_pct=spec['initial_margin_pct'],
        maintenance_margin_pct=spec['maintenance_margin_pct'],
        point_value=spec['point_value'],
        pip_size=spec['pip_size'],
        instrument_name=contract,
        instrument_type=spec.get('instrument_type', 'FUTURE'),
        contract_size=spec.get('contract_size', 1),
        or_start_hour=spec['or_start_hour'],
        or_start_minute=spec.get('or_start_minute', 0),
        or_end_hour=spec['or_end_hour'],
        or_end_minute=spec.get('or_end_minute', 0),
        trend_check_hour=spec['trend_check_hour'],
        trend_check_minute=spec.get('trend_check_minute', 0),
        trading_start_hour=spec['trading_start_hour'],
        trading_start_minute=spec.get('trading_start_minute', 0),
        trading_end_hour=spec['trading_end_hour'],
        trading_end_minute=spec['trading_end_minute'],
        trading_end_next_day=spec.get('trading_end_next_day', False),
        main_timeframe=main_timeframe,
        enable_multi_timeframe=enable_multi_timeframe
    )
    
    results = backtester.run()
    backtester.print_results(results)
    
    # Generate charts
    print("\n📊 Generating charts...")
    backtester.create_balance_chart()
    
    trade_fig, trade_config = backtester.create_tradingview_chart()
    
    mtf_suffix = "MTF" if enable_multi_timeframe else "STF"
    date_str = f"{start_date or 'full'}_{end_date or 'full'}"
    html_filename = f'sq60_orb_{mtf_suffix}_{contract}_{main_timeframe}_{date_str}.html'
    trade_fig.write_html(html_filename, config=trade_config)
    print(f"✓ Chart saved: {html_filename}")
    
    if len(results['trades']) > 0:
        csv_filename = f'sq60_orb_{mtf_suffix}_trades_{contract}_{main_timeframe}_{date_str}.csv'
        results['trades'].to_csv(csv_filename, index=False)
        print(f"✓ Trades saved: {csv_filename}")
    
    print(f"\n✅ Backtest complete!\n")
    
    return results, backtester


def compare_single_vs_multi_timeframe(contract='GC', main_timeframe='1H', 
                                      start_date=None, end_date=None,
                                      balance=100000, risk_pct=2.0, verbose=False):
    """
    Run both single-timeframe and multi-timeframe backtests for comparison
    
    Example:
        compare_single_vs_multi_timeframe('ES', main_timeframe='1H')
        compare_single_vs_multi_timeframe('FDAX', main_timeframe='4H', start_date='2024-01-01')
    """
    
    print("\n" + "="*80)
    print(f"COMPARISON: Single vs Multi-Timeframe - {contract} ({main_timeframe})")
    print("="*80 + "\n")
    
    # Run single-timeframe
    print("🔹 Running SINGLE-TIMEFRAME backtest...")
    print("-" * 80)
    results_stf, bt_stf = run_quick_backtest(
        contract=contract,
        main_timeframe=main_timeframe,
        start_date=start_date,
        end_date=end_date,
        balance=balance,
        risk_pct=risk_pct,
        verbose=verbose,
        enable_multi_timeframe=False
    )
    
    print("\n\n")
    
    # Run multi-timeframe
    print("🔸 Running MULTI-TIMEFRAME backtest...")
    print("-" * 80)
    results_mtf, bt_mtf = run_quick_backtest(
        contract=contract,
        main_timeframe=main_timeframe,
        start_date=start_date,
        end_date=end_date,
        balance=balance,
        risk_pct=risk_pct,
        verbose=verbose,
        enable_multi_timeframe=True
    )
    
    # Compare results
    print("\n" + "="*80)
    print("📊 COMPARISON RESULTS")
    print("="*80)
    
    print(f"\n{'Metric':<30} {'Single-TF':>15} {'Multi-TF':>15} {'Difference':>15}")
    print("-" * 80)
    
    metrics = [
        ('Total Return %', 'return_pct'),
        ('Final Balance', 'final_balance'),
        ('CAGR %', 'cagr'),
        ('Max Drawdown %', 'max_drawdown_pct'),
        ('Total Trades', 'total_trades'),
        ('Win Rate %', 'win_rate'),
        ('Profit Factor', 'profit_factor'),
    ]
    
    for label, key in metrics:
        if key in results_stf and key in results_mtf:
            val_stf = results_stf[key]
            val_mtf = results_mtf[key]
            
            if key == 'final_balance':
                diff = val_mtf - val_stf
                print(f"{label:<30} ${val_stf:>14,.0f} ${val_mtf:>14,.0f} ${diff:>+14,.0f}")
            elif key == 'total_trades':
                diff = val_mtf - val_stf
                print(f"{label:<30} {val_stf:>15.0f} {val_mtf:>15.0f} {diff:>+15.0f}")
            elif key == 'profit_factor':
                if val_stf == float('inf'):
                    val_stf = 999
                if val_mtf == float('inf'):
                    val_mtf = 999
                diff = val_mtf - val_stf
                print(f"{label:<30} {val_stf:>15.2f} {val_mtf:>15.2f} {diff:>+15.2f}")
            else:
                diff = val_mtf - val_stf
                print(f"{label:<30} {val_stf:>15.2f} {val_mtf:>15.2f} {diff:>+15.2f}")
    
    if 'alignment_stats' in results_mtf:
        print("\n🎯 Multi-Timeframe Alignment Distribution:")
        total = sum(results_mtf['alignment_stats'].values())
        for key, count in results_mtf['alignment_stats'].items():
            pct = (count / total * 100) if total > 0 else 0
            print(f"   {key}: {count} trades ({pct:.1f}%)")
    
    print("\n" + "="*80 + "\n")
    
    return {
        'single_tf': results_stf,
        'multi_tf': results_mtf,
        'backtester_stf': bt_stf,
        'backtester_mtf': bt_mtf
    }


if __name__ == "__main__":
    # When you run this file, it starts interactive mode
    run_interactive_backtest()
    
    # Or uncomment below to run quick backtests directly:
    
    # Standard multi-timeframe backtest
    # run_quick_backtest('GC', main_timeframe='1H')
    
    # Different timeframes
    # run_quick_backtest('ES', main_timeframe='15T')  # 15-minute
    # run_quick_backtest('FDAX', main_timeframe='4H')  # 4-hour
    
    # Single-timeframe only (disable MTF)
    # run_quick_backtest('ES', main_timeframe='1H', enable_multi_timeframe=False)
    
    # Comparison test
    # compare_single_vs_multi_timeframe('ES', main_timeframe='1H', start_date='2024-01-01')
    
    # Forex with multi-timeframe
    # run_quick_backtest('EURUSD', main_timeframe='1H')
    # run_quick_backtest('USDJPY', main_timeframe='4H')