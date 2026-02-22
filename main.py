"""
main.py
จุดเริ่มรันโปรเจกต์ Inventory LP Backtester

อัปเดตล่าสุด: v1.0.5 (Ultimate PnL Edition)
- ระบบ Split Capital (LP + CEX Margin)
- คำนวณ CAGR, Max Drawdown, Sharpe Ratio
- พ่นรีพอร์ต PnL Statement ตรงกับ React v1.8
"""

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

def run_sample_simulation():
    print("="*60)
    print("🚀 QUANT LAB: Delta Hedge Backtest Engine v1.0.5")
    print("="*60)
    
    # 1. Setup Configs (Split Capital System)
    lp_capital = 10000.0
    perp_capital = 3000.0  # เงินค้ำประกันบน Binance (เผื่อรัน Leverage 5x)
    total_capital = lp_capital + perp_capital
    
    days_to_run = 360
    
    oracle_cfg = OracleConfig(start_price=2000.0, days=days_to_run, annual_volatility=1.2, seed=123)
    
    lp_cfg = LPConfig(
        initial_capital=lp_capital, 
        range_width=0.10, 
        rebalance_threshold=0.25,
        fee_mode='base_apr',
        base_apr=0.04 
    )
    
    perp_cfg = PerpConfig(leverage=5.0)
    
    strat_cfg = StrategyConfig(
        hedge_mode='always',
        use_safety_net=True,     # [PM ADDED] สวิตช์หลักสำหรับเปิดใช้งาน Safety Net
        safety_net_pct=0.05,     # ถ้าราคาเหวี่ยงเกิน 3% ในแท่งเดียว บอทจะรีบ Hedge ปิดรอยรั่วทันที
        hedge_threshold=0.25     # ปล่อยหลวมๆ 25% ในเวลาปกติเพื่อประหยัดค่า Fee
    )
    
    # 2. Initialize Modules
    oracle = OracleModule()
    lp = LPModule(lp_cfg, oracle_cfg.start_price)
    perp = PerpModule(perp_cfg)
    strategy = StrategyModule(lp, perp)
    
    # พอร์ตโฟลิโอเริ่มต้นด้วยเงินก้อนรวม
    portfolio = PortfolioModule(total_capital)
    portfolio.allocate_to_lp(lp_capital) # ตัดเงินไปลง On-chain ที่เหลือจะกลายเป็น Idle Cash สำหรับ CEX Margin
    
    # 3. Setup Engine & Run
    engine = BacktestEngine(oracle, lp, perp, strategy, portfolio)
    data = oracle.generate_data(oracle_cfg)
    print(f"[*] Generated {len(data)} hours of price data.")
    
    # funding_rate 0.0001 = 0.01% ต่อ 8 ชั่วโมง
    results = engine.run(data, strat_cfg, funding_rate=0.0001)
    results['price'] = data['close'].values
    
    # 4. Calculate Advanced Metrics (Pandas Vectorization)
    initial_equity = total_capital  # [PM FIXED] ใช้ทุนจริงที่ตั้งไว้ตอนแรก
    final_equity = results['net_equity'].iloc[-1]
    net_profit = final_equity - initial_equity
    total_roi = net_profit / initial_equity
    
    # CAGR
    cagr = (pow(1 + total_roi, 365 / days_to_run) - 1) * 100
    
    # Max Drawdown
    roll_max = results['net_equity'].cummax()
    drawdown = (results['net_equity'] - roll_max) / roll_max
    max_drawdown = drawdown.min() * 100
    
    # Sharpe Ratio
    hourly_returns = results['net_equity'].pct_change().dropna()
    std_dev = hourly_returns.std()
    sharpe = (hourly_returns.mean() / std_dev) * np.sqrt(365 * 24) if std_dev > 0 else 0
    
    # Effective APR
    effective_apr = lp_cfg.base_apr * lp.multiplier * 100

    # 5. Extract Ledger (PnL Statement)
    gross_fees = portfolio.ledgers[TransactionType.REVENUE_LP_FEE]
    funding_received = portfolio.ledgers[TransactionType.REVENUE_FUNDING]
    funding_paid = portfolio.ledgers[TransactionType.EXPENSE_FUNDING]
    net_funding = funding_received + funding_paid # paid เป็นลบอยู่แล้ว
    
    perp_costs = abs(portfolio.ledgers[TransactionType.EXPENSE_PERP_FEE])
    rebal_gas = abs(portfolio.ledgers[TransactionType.EXPENSE_GAS])
    rebal_slip = abs(portfolio.ledgers[TransactionType.EXPENSE_SLIPPAGE])
    rebal_total_costs = rebal_gas + rebal_slip
    
    # การคำนวณ Residual Risk / IL Impact
    total_costs_all = perp_costs + rebal_total_costs
    expected_profit_cashflow = gross_fees + net_funding - total_costs_all
    delta_pnl = net_profit - expected_profit_cashflow # ส่วนต่างคือกำไร/ขาดทุนจากราคาเพียวๆ
    
    # 6. Print Report (Matching React UI)
    print("\n" + "="*60)
    print(f"📊 SUMMARY METRICS ({days_to_run} Days)")
    print("="*60)
    print(f"Total Initial Capital : ${initial_equity:,.2f} (LP: ${lp_capital:,.0f} | CEX: ${perp_capital:,.0f})")
    print(f"Final Net Equity      : ${final_equity:,.2f}")
    print(f"Net Profit            : ${net_profit:+,.2f}")
    print(f"Raw ROI               : {total_roi*100:+.2f}%")
    print(f"Annualized CAGR       : {cagr:+.2f}%")
    print(f"Max Drawdown          : {max_drawdown:.2f}%")
    print(f"Sharpe Ratio          : {sharpe:.2f}")
    
    print("\n" + "-"*60)
    print(f"⚙️  STRATEGY & ACTIVITY")
    print("-" * 60)
    print(f"LP Multiplier         : {lp.multiplier:.2f}x (Base APR: {lp_cfg.base_apr*100}%)")
    print(f"Effective APR         : {effective_apr:.2f}%")
    print(f"LP Rebalances         : {lp.rebalance_count} Times ({(lp.rebalance_count/days_to_run):.2f} / Day)")
    print(f"Hedge Trades          : {engine.hedge_count} Times ({(engine.hedge_count/days_to_run):.2f} / Day)")

    print("\n" + "-"*60)
    print(f"🧮 PNL STATEMENT BREAKDOWN")
    print("-" * 60)
    print(f"Gross LP Yield (Fees) : +${gross_fees:,.2f}")
    print(f"Net Funding Rate      : {('+' if net_funding >= 0 else '')}${net_funding:,.2f}")
    print(f"Trading Costs (Perp)  : -${perp_costs:,.2f}")
    print(f"Rebalance (Gas+Slip)  : -${rebal_total_costs:,.2f}  (Gas: ${rebal_gas:.0f}, Slip: ${rebal_slip:.0f})")
    print(f"IL & Residual Risk    : {('+' if delta_pnl >= 0 else '')}${delta_pnl:,.2f}  <-- ตัวแปรสำคัญ")
    print("-" * 60)
    print(f"NET PROFIT            : {('+' if net_profit >= 0 else '')}${net_profit:,.2f}")
    print("="*60)

    # 7. Plot Results
    fig, ax1 = plt.subplots(figsize=(12, 6))

    color1 = 'tab:purple'
    ax1.set_xlabel('Time (Hours)')
    ax1.set_ylabel('Total Net Equity ($)', color=color1, fontweight='bold')
    ax1.plot(results.index, results['net_equity'], color=color1, label='Total Net Equity', linewidth=2)
    ax1.tick_params(axis='y', labelcolor=color1)
    ax1.grid(True, linestyle='--', alpha=0.6)

    ax2 = ax1.twinx()  
    color2 = 'tab:gray'
    ax2.set_ylabel('ETH Price ($)', color=color2, fontweight='bold')  
    ax2.plot(results.index, results['price'], color=color2, label='ETH Price', linewidth=1, alpha=0.5, linestyle='-.')
    ax2.tick_params(axis='y', labelcolor=color2)

    plt.title(f"Quant Lab: Delta Hedge v1.0.5\nCAGR: {cagr:.2f}% | MDD: {max_drawdown:.2f}% | Sharpe: {sharpe:.2f}", fontweight='bold')
    fig.tight_layout()
    plt.show()

if __name__ == "__main__":
    run_sample_simulation()