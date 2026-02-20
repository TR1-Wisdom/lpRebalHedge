"""
main.py
จุดเริ่มรันโปรเจกต์ Inventory LP Backtester

ทำหน้าที่ร้อยเรียงทุกโมดูลเข้าด้วยกัน และแสดงผลลัพธ์ ROI สุทธิ
อัปเดต: เพิ่มการโชว์ Effective APR, กราฟ 2 แกน, แก้ไขข้อความวันให้ไดนามิก
"""

from datetime import datetime
import matplotlib.pyplot as plt
from src.oracle.oracle import OracleModule, OracleConfig
from src.lp.lp import LPModule, LPConfig
from src.perp.perp import PerpModule, PerpConfig
from src.strategy.strategy import StrategyModule, StrategyConfig
from src.portfolio.portfolio import PortfolioModule
from src.engine.backtest_engine import BacktestEngine

def run_sample_simulation():
    print("--- Starting Backtest Simulation v1.0.4 ---")
    
    # 1. Setup Configs
    capital = 10000.0
    
    # รัน 120 วัน ความผันผวน 50%
    oracle_cfg = OracleConfig(start_price=2000.0, days=120, annual_volatility=0.50, seed=123)
    
    lp_cfg = LPConfig(
        initial_capital=capital, 
        range_width=0.20, 
        rebalance_threshold=0.25,
        fee_mode='base_apr',
        base_apr=0.05 
    )
    
    perp_cfg = PerpConfig(leverage=1.0)
    
    # [PM FIXED]: ใช้โหมด 'always' อย่างเป็นทางการ บอทจะไม่สน EMA อีกต่อไป
    strat_cfg = StrategyConfig(
        hedge_mode='always',
        safety_net_pct=0.03,
        hedge_threshold=0.10 
    )
    
    # 2. Initialize Modules
    oracle = OracleModule()
    lp = LPModule(lp_cfg, oracle_cfg.start_price)
    perp = PerpModule(perp_cfg)
    strategy = StrategyModule(lp, perp)
    portfolio = PortfolioModule(capital)
    
    portfolio.allocate_to_lp(capital)
    
    # 3. Setup Engine & Run
    engine = BacktestEngine(oracle, lp, perp, strategy, portfolio)
    data = oracle.generate_data(oracle_cfg)
    print(f"Generated {len(data)} hours of price data.")
    
    results = engine.run(data, strat_cfg)
    results['price'] = data['close'].values
    
    # 4. Summary Result
    initial_equity = results['net_equity'].iloc[0]
    final_equity = results['net_equity'].iloc[-1]
    total_roi = ((final_equity - initial_equity) / initial_equity) * 100
    effective_apr = lp_cfg.base_apr * lp.multiplier * 100
    
    print("\n" + "="*40)
    print(f"SIMULATION COMPLETE ({oracle_cfg.days} Days)")
    print("="*40)
    print(f"LP Setting      : Base APR {lp_cfg.base_apr*100}% | Range ±{lp_cfg.range_width*100}%")
    print(f"LP Multiplier   : {lp.multiplier:.2f}x")
    print(f"Effective APR   : {effective_apr:.2f}% (Max Theoretical)")
    print("-" * 40)
    print(f"Initial Capital : ${initial_equity:,.2f}")
    print(f"Final Net Equity: ${final_equity:,.2f}")
    print(f"Total ROI       : {total_roi:.2f}%")
    print(f"Total Fees Earn : ${results['total_fees_collected'].iloc[-1]:,.2f}")
    print(f"Total Costs     : ${results['total_costs'].iloc[-1]:,.2f}")
    print("="*40)

    # 5. Plot Results (Dual Axis)
    fig, ax1 = plt.subplots(figsize=(12, 6))

    color1 = 'tab:blue'
    ax1.set_xlabel('Time (Hours)')
    ax1.set_ylabel('Net Equity ($)', color=color1, fontweight='bold')
    ax1.plot(results.index, results['net_equity'], color=color1, label='Net Equity (Left)', linewidth=2)
    ax1.tick_params(axis='y', labelcolor=color1)
    ax1.grid(True, linestyle='--', alpha=0.6)

    ax2 = ax1.twinx()  
    color2 = 'tab:orange'
    ax2.set_ylabel('ETH Price ($)', color=color2, fontweight='bold')  
    ax2.plot(results.index, results['price'], color=color2, label='ETH Price (Right)', linewidth=1.5, alpha=0.7, linestyle='-.')
    ax2.tick_params(axis='y', labelcolor=color2)

    plt.title(f"Always Hedge LP Performance ({oracle_cfg.days} Days)\nROI: {total_roi:.2f}% | Eff. APR: {effective_apr:.2f}%", fontweight='bold')
    fig.tight_layout()
    plt.show()

if __name__ == "__main__":
    run_sample_simulation()