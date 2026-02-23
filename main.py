"""
main.py
จุดเริ่มรันโปรเจกต์ Inventory LP Backtester

อัปเดตล่าสุด: v1.1.5 (Add Min Margin Tracker)
- ผสานระบบถอนเงิน (Passive Income Harvesting) เข้ากับ CSV Export แบบเต็มรูปแบบ
- เพิ่มระบบติดตาม Min CEX Margin (Lowest) เพื่อประเมินความเสี่ยงเงินค้ำประกัน
"""

import os
import yaml
import numpy as np
import pandas as pd
from datetime import datetime
import matplotlib.pyplot as plt

from src.oracle.oracle import OracleModule, OracleConfig
from src.lp.lp import LPModule, LPConfig
from src.perp.perp import PerpModule, PerpConfig
from src.strategy.strategy import StrategyModule, StrategyConfig
from src.portfolio.portfolio import PortfolioModule, TransactionType
from src.engine.backtest_engine import BacktestEngine

def load_config(file_path: str = 'config.yaml') -> dict:
    """อ่านไฟล์ YAML และคืนค่าเป็น Dictionary"""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"⚠️ ไม่พบไฟล์ตั้งค่า: {file_path}")
    with open(file_path, 'r', encoding='utf-8') as file:
        return yaml.safe_load(file)

def run_simulation_from_config():
    # 1. โหลดไฟล์ Config
    try:
        cfg = load_config('config.yaml')
    except Exception as e:
        print(e)
        return

    print("="*65)
    print("🚀 QUANT LAB: Delta Hedge Backtest Engine v1.1.5")
    print("="*65)
    
    # 2. Extract Configs to Dataclasses
    # --- Capital ---
    lp_capital = float(cfg['capital']['lp_capital'])
    perp_capital = float(cfg['capital']['perp_capital'])
    total_capital = lp_capital + perp_capital
    leverage = float(cfg['capital']['leverage'])
    
    # --- Market ---
    days_to_run = int(cfg['market']['days_to_run'])
    seed_val = cfg['market']['seed']
    if seed_val == 'null' or seed_val is None:
        seed_val = None
    else:
        seed_val = int(seed_val)

    oracle_cfg = OracleConfig(
        start_price=float(cfg['market']['start_price']), 
        days=days_to_run, 
        annual_volatility=float(cfg['market']['annual_volatility']), 
        seed=seed_val
    )
    
    # --- LP ---
    base_apr_val = float(cfg['lp']['base_apr'])
    range_width_val = float(cfg['lp']['range_width'])
    rebalance_threshold_val = float(cfg['lp']['rebalance_threshold'])
    
    lp_cfg = LPConfig(
        initial_capital=lp_capital, 
        range_width=range_width_val, 
        rebalance_threshold=rebalance_threshold_val,
        fee_mode='base_apr',
        base_apr=base_apr_val,
        gas_fee=float(cfg['costs']['gas_fee_usd']),
        slippage=float(cfg['costs']['slippage'])
    )
    
    # --- Perp ---
    perp_taker_fee_val = float(cfg['costs']['perp_taker_fee'])
    perp_cfg = PerpConfig(
        leverage=leverage,
        taker_fee=perp_taker_fee_val
    )
    
    # --- Strategy ---
    hedge_mode_val = cfg['strategy']['hedge_mode']
    use_safety_net_val = bool(cfg['strategy']['use_safety_net'])
    safety_net_pct_val = float(cfg['strategy']['safety_net_pct'])
    hedge_threshold_val = float(cfg['strategy']['hedge_threshold'])
    ema_period_val = int(cfg['strategy']['ema_period'])
    
    strat_cfg = StrategyConfig(
        hedge_mode=hedge_mode_val,
        use_safety_net=use_safety_net_val,
        safety_net_pct=safety_net_pct_val,
        hedge_threshold=hedge_threshold_val,
        ema_period=ema_period_val
    )
    
    funding_rate_8h = float(cfg['costs']['funding_rate_8h'])

    # 3. Initialize Modules
    oracle = OracleModule()
    lp = LPModule(lp_cfg, oracle_cfg.start_price)
    perp = PerpModule(perp_cfg)
    strategy = StrategyModule(lp, perp)
    
    portfolio = PortfolioModule(total_capital)
    portfolio.allocate_to_lp(lp_capital) 
    
    # 4. Setup Engine & Run
    engine = BacktestEngine(oracle, lp, perp, strategy, portfolio)
    data = oracle.generate_data(oracle_cfg)
    print(f"[*] Generated {len(data)} hours of price data. Starting Simulation...")
    
    # ส่ง harvest_config จาก yaml ไปให้ Engine จัดการถอนเงิน
    harvest_cfg = cfg.get('harvesting', {})
    results = engine.run(data, strat_cfg, funding_rate=funding_rate_8h, harvest_config=harvest_cfg)
    results['price'] = data['close'].values
    
    # 5. Calculate Advanced Metrics (Wealth Tracking)
    initial_equity = total_capital 
    final_equity = results['net_equity'].iloc[-1]
    total_withdrawn = results['total_withdrawn'].iloc[-1]
    
    # คำนวณหา CEX Margin ต่ำสุดที่เคยไปถึง
    cex_equity_series = results['idle_cash'] + results['perp_pnl']
    min_cex_margin = cex_equity_series.min()
    min_cex_margin_pct = (min_cex_margin / perp_capital) * 100

    # ความมั่งคั่งรวม = เงินที่เหลืออยู่ในระบบ + เงินที่ถอนออกไปใช้แล้ว
    total_wealth = final_equity + total_withdrawn 
    
    net_profit = total_wealth - initial_equity
    total_roi = net_profit / initial_equity
    cagr = (pow(1 + total_roi, 365 / days_to_run) - 1) * 100
    
    roll_max = results['net_equity'].cummax()
    drawdown = (results['net_equity'] - roll_max) / roll_max
    max_drawdown = drawdown.min() * 100
    
    hourly_returns = results['net_equity'].pct_change().dropna()
    std_dev = hourly_returns.std()
    sharpe = (hourly_returns.mean() / std_dev) * np.sqrt(365 * 24) if std_dev > 0 else 0
    
    effective_apr = lp_cfg.base_apr * lp.multiplier * 100

    # 6. Extract Ledger (PnL Statement)
    gross_fees = portfolio.ledgers[TransactionType.REVENUE_LP_FEE]
    funding_received = portfolio.ledgers[TransactionType.REVENUE_FUNDING]
    funding_paid = portfolio.ledgers[TransactionType.EXPENSE_FUNDING]
    net_funding = funding_received + funding_paid 
    
    perp_costs = abs(portfolio.ledgers[TransactionType.EXPENSE_PERP_FEE])
    rebal_gas = abs(portfolio.ledgers[TransactionType.EXPENSE_GAS])
    rebal_slip = abs(portfolio.ledgers[TransactionType.EXPENSE_SLIPPAGE])
    rebal_total_costs = rebal_gas + rebal_slip
    
    total_costs_all = perp_costs + rebal_total_costs
    expected_profit_cashflow = gross_fees + net_funding - total_costs_all
    
    # หักลบยอดถอนออกจากสมการเพื่อหา IL สุทธิอย่างแม่นยำ
    delta_pnl = net_profit - expected_profit_cashflow 
    
    # 7. Print Standard Report
    print("\n" + "="*65)
    print(f"💰 STRATEGIC WEALTH REPORT ({days_to_run} Days)")
    print("="*65)
    print(f"Total Initial Capital : ${initial_equity:,.2f} (LP: ${lp_capital:,.0f} | CEX: ${perp_capital:,.0f})")
    print(f"Final Net Equity      : ${final_equity:,.2f}")
    print(f"Total Withdrawn (Cash): ${total_withdrawn:,.2f}  <-- Passive Income 💵")
    print(f"TOTAL WEALTH CREATED  : ${total_wealth:,.2f}")
    print("-" * 65)
    print(f"Net Profit            : ${net_profit:+,.2f}")
    print(f"Raw ROI               : {total_roi*100:+.2f}%")
    print(f"Annualized CAGR       : {cagr:+.2f}%")
    print(f"Max Drawdown (Live)   : {max_drawdown:.2f}%")
    print(f"Sharpe Ratio          : {sharpe:.2f}")
    
    print("\n" + "-"*65)
    print(f"⚙️  STRATEGY & ACTIVITY")
    print("-" * 65)
    print(f"Hedge Mode            : {strat_cfg.hedge_mode.upper()} (Threshold: {strat_cfg.hedge_threshold*100}%)")
    print(f"Safety Net            : {'ON' if strat_cfg.use_safety_net else 'OFF'} (Trigger: {strat_cfg.safety_net_pct*100}%)")
    print(f"LP Multiplier         : {lp.multiplier:.2f}x (Base APR: {lp_cfg.base_apr*100}%)")
    print(f"Effective APR         : {effective_apr:.2f}%")
    print(f"LP Rebalances         : {lp.rebalance_count} Times")
    print(f"Hedge Trades          : {engine.hedge_count} Times")
    print(f"Successful Withdrawals: {engine.withdrawal_count} Times")
    print(f"Margin Call Rejects   : {len(engine.margin_call_events)} Times 🚨")
    print(f"Min CEX Margin (Low)  : ${min_cex_margin:,.2f} ({min_cex_margin_pct:.2f}%)") # แสดงยอด Margin ต่ำสุด

    # --- Print Margin Call Details (Top 3 Worst Cases) ---
    if engine.margin_call_events:
        print("\n" + "🔴 MARGIN CALL ANALYSIS (Worst Cases):")
        sorted_events = sorted(engine.margin_call_events, key=lambda x: x['margin_needed'] - x['available_margin'], reverse=True)
        for i, ev in enumerate(sorted_events[:3]):
            deficit = ev['margin_needed'] - ev['available_margin']
            print(f" {i+1}. [{ev['timestamp']}] ETH: ${ev['price']:,.0f} | Required: ${ev['margin_needed']:,.0f} | Deficit: -${deficit:,.2f}")

    print("\n" + "-"*65)
    print(f"🧮 PNL STATEMENT BREAKDOWN")
    print("-" * 65)
    print(f"Gross LP Yield (Fees) : +${gross_fees:,.2f}")
    print(f"Net Funding Rate      : {('+' if net_funding >= 0 else '')}${net_funding:,.2f}")
    print(f"Trading Costs (Perp)  : -${perp_costs:,.2f}")
    print(f"Rebalance (Gas+Slip)  : -${rebal_total_costs:,.2f}  (Gas: ${rebal_gas:.0f}, Slip: ${rebal_slip:.0f})")
    print(f"IL & Residual Risk    : {('+' if delta_pnl >= 0 else '')}${delta_pnl:,.2f}")
    print("="*65)

    # 8. CSV Friendly Output (Full Version)
    csv_output = f"""
\n⬇️ --- COPY BELOW THIS LINE TO EXCEL (CSV Format) --- ⬇️
Category,Metric,Value
[1. CONFIG],LP Capital,{lp_capital}
[1. CONFIG],Perp Capital,{perp_capital}
[1. CONFIG],Leverage,{leverage}
[1. CONFIG],Start Price,{oracle_cfg.start_price}
[1. CONFIG],Days to Run,{days_to_run}
[1. CONFIG],Annual Volatility,{oracle_cfg.annual_volatility}
[1. CONFIG],Seed,{seed_val}
[1. CONFIG],Base APR,{base_apr_val}
[1. CONFIG],Range Width,{range_width_val}
[1. CONFIG],Rebalance Threshold,{rebalance_threshold_val}
[1. CONFIG],Hedge Mode,{hedge_mode_val}
[1. CONFIG],Use Safety Net,{use_safety_net_val}
[1. CONFIG],Safety Net Pct,{safety_net_pct_val}
[1. CONFIG],Hedge Threshold,{hedge_threshold_val}
[1. CONFIG],EMA Period,{ema_period_val}
[1. CONFIG],Gas Fee USD,{lp_cfg.gas_fee}
[1. CONFIG],Slippage,{lp_cfg.slippage}
[1. CONFIG],Perp Taker Fee,{perp_taker_fee_val}
[1. CONFIG],Funding Rate 8H,{funding_rate_8h}
[1. CONFIG],Harvesting Enabled,{harvest_cfg.get('enabled', False)}
[1. CONFIG],Harvesting Freq Days,{harvest_cfg.get('withdrawal_freq_days', 0)}
[1. CONFIG],Harvesting Target $,{harvest_cfg.get('target_amount', 0)}
[2. WEALTH],Total Initial Capital,{initial_equity:.2f}
[2. WEALTH],Final Net Equity (Live),{final_equity:.2f}
[2. WEALTH],Total Withdrawn (Cash),{total_withdrawn:.2f}
[2. WEALTH],Total Wealth Created,{total_wealth:.2f}
[2. WEALTH],Net Profit,{net_profit:.2f}
[2. WEALTH],Annualized CAGR (%),{cagr:.2f}
[2. WEALTH],Max Drawdown (%),{max_drawdown:.2f}
[2. WEALTH],Sharpe Ratio,{sharpe:.2f}
[3. STATS],LP Multiplier,{lp.multiplier:.2f}
[3. STATS],Effective APR (%),{effective_apr:.2f}
[3. STATS],LP Rebalances,{lp.rebalance_count}
[3. STATS],Hedge Trades,{engine.hedge_count}
[3. STATS],Withdrawal Count,{engine.withdrawal_count}
[3. STATS],Margin Call Count,{len(engine.margin_call_events)}
[3. STATS],Min CEX Margin $,{min_cex_margin:.2f}
[3. STATS],Min CEX Margin %,{min_cex_margin_pct:.2f}
[4. PNL],Gross LP Yield,{gross_fees:.2f}
[4. PNL],Net Funding Rate,{net_funding:.2f}
[4. PNL],Trading Costs (Perp),{-perp_costs:.2f}
[4. PNL],Rebalance Costs (Gas+Slip),{-rebal_total_costs:.2f}
[4. PNL],IL & Residual Risk Impact,{delta_pnl:.2f}
⬆️ --- COPY ABOVE THIS LINE --- ⬆️
"""
    print(csv_output)

    # 9. Plot Results (Dual Lines for Wealth Tracking)
    fig, ax1 = plt.subplots(figsize=(12, 6))

    color1 = 'tab:purple'
    color_wealth = 'tab:green'
    ax1.set_xlabel('Time (Hours)')
    ax1.set_ylabel('Equity ($)', color=color1, fontweight='bold')
    
    # เส้นพอร์ตปัจจุบัน (โดนหักเงินถอนออกไปแล้ว)
    ax1.plot(results.index, results['net_equity'], color=color1, label='Live Equity', linewidth=2)
    # เส้นความมั่งคั่งรวม (ถ้าไม่ได้ถอนออกมาเลยจะอยู่ตรงนี้)
    ax1.plot(results.index, results['net_equity'] + results['total_withdrawn'], color=color_wealth, label='Total Wealth (Inc. Withdrawals)', linestyle='--', alpha=0.8, linewidth=2)
    
    ax1.tick_params(axis='y', labelcolor=color1)
    ax1.legend(loc='upper left')
    ax1.grid(True, linestyle='--', alpha=0.6)

    ax2 = ax1.twinx()  
    color2 = 'tab:gray'
    ax2.set_ylabel('ETH Price ($)', color=color2, fontweight='bold')  
    ax2.plot(results.index, results['price'], color=color2, label='ETH Price', linewidth=1, alpha=0.4, linestyle='-.')
    ax2.tick_params(axis='y', labelcolor=color2)
    ax2.legend(loc='lower left')

    plt.title(f"Quant Lab v1.1.5: Wealth Harvesting & Margin Tracking\nCAGR: {cagr:.2f}% | Passive Income: ${total_withdrawn:,.2f} | Margin Calls: {len(engine.margin_call_events)}", fontweight='bold')
    fig.tight_layout()
    plt.show()

if __name__ == "__main__":
    run_simulation_from_config()