"""
optimizer.py (v1.8.0 Parallel Edition)
อัปเกรด: ใช้ ProcessPoolExecutor เพื่อรัน Monte Carlo 501 รอบพร้อมกัน
ช่วยเพิ่มความเร็วสูงสุดตามจำนวน CPU Core ที่เครื่องมี
"""

import os
import yaml
import itertools
import numpy as np
import pandas as pd
from datetime import datetime
from tqdm import tqdm
from concurrent.futures import ProcessPoolExecutor # [NEW] สำหรับรันขนาน

from src.oracle.oracle import OracleModule, OracleConfig
from src.lp.lp import LPModule, LPConfig
from src.perp.perp import PerpModule, PerpConfig
from src.strategy.strategy import StrategyModule, StrategyConfig
from src.portfolio.portfolio import PortfolioModule
from src.engine.backtest_engine import BacktestEngine

def load_config():
    with open('config.yaml', 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)

def run_single_backtest(params):
    """ฟังก์ชันย่อยสำหรับรัน 1 รอบ (เพื่อให้ส่งเข้า ProcessPool ได้)"""
    # 1. Setup Data
    o_cfg = OracleConfig(start_price=params['start_price'], days=params['days'], annual_volatility=params['vol'], seed=params['seed'])
    oracle = OracleModule()
    data = oracle.generate_data(o_cfg)

    # 2. Setup Modules
    lp_cfg = LPConfig(initial_capital=params['lp_cap'], range_width=params['range'], rebalance_threshold=params['rebal'],
                      fee_mode='base_apr', base_apr=params['base_apr'], gas_fee=2.0, slippage=0.001)
    strat_cfg = StrategyConfig(hedge_mode=params['h_mode'], use_safety_net=True, safety_net_pct=params['safe_pct'], 
                               hedge_threshold=params['h_thresh'], ema_period=200)
    perp_cfg = PerpConfig(leverage=params['lev'], taker_fee=0.0005)

    lp = LPModule(lp_cfg, params['start_price'])
    perp = PerpModule(perp_cfg)
    strategy = StrategyModule(lp, perp)
    portfolio = PortfolioModule(params['lp_cap'] + params['perp_cap'])
    portfolio.allocate_to_lp(params['lp_cap'])
    
    # 3. Run Engine
    engine = BacktestEngine(oracle, lp, perp, strategy, portfolio)
    
    # [FIX] ใส่ record_all_ticks=False เพื่อให้ Optimizer กิน RAM น้อยลงและวิ่งเร็วที่สุด
    res_df = engine.run(data, strat_cfg, funding_rate=params['fund'], cross_rebalance_config={'enabled': True, 'freq_days': 15}, record_all_ticks=False)
    
    # 4. Extract Metrics
    final_equity = res_df['net_equity'].iloc[-1]
    total_wealth = final_equity + res_df['total_withdrawn'].iloc[-1]
    net_profit = total_wealth - (params['lp_cap'] + params['perp_cap'])
    cagr = (pow(1 + (net_profit / (params['lp_cap'] + params['perp_cap'])), 365 / params['days']) - 1) * 100
    
    return {
        'Seed': params['seed'],
        'CAGR_%': round(cagr, 2),
        'Margin_Calls': len(engine.margin_call_events),
        'Min_CEX_Margin': res_df['cex_available_margin'].min() if 'cex_available_margin' in res_df.columns else 0
    }

def main():
    cfg = load_config()
    seeds = np.arange(42, 542 + 1, 1) # 501 Seeds
    
    print(f"🚀 เริ่มการทดสอบ Monte Carlo {len(seeds)} รอบแบบขนาน...")
    
    # เตรียม Parameter พื้นฐาน
    base_params = {
        'start_price': float(cfg['market']['start_price']),
        'days': int(cfg['market']['days_to_run']),
        'vol': float(cfg['market']['annual_volatility']),
        'lp_cap': float(cfg['capital']['lp_capital']),
        'perp_cap': float(cfg['capital']['perp_capital']),
        'lev': float(cfg['capital']['leverage']),
        'base_apr': float(cfg['lp']['base_apr']),
        'range': float(cfg['lp']['range_width']),
        'rebal': float(cfg['lp']['rebalance_threshold']),
        'h_mode': cfg['strategy']['hedge_mode'],
        'h_thresh': float(cfg['strategy']['hedge_threshold']),
        'safe_pct': float(cfg['strategy']['safety_net_pct']),
        'fund': float(cfg['costs']['funding_rate_8h'])
    }
    
    # สร้างรายการงาน (Task List)
    tasks = []
    for s in seeds:
        p = base_params.copy()
        p['seed'] = int(s)
        tasks.append(p)

    # รันแบบขนาน (Parallel)
    results = []
    with ProcessPoolExecutor() as executor:
        # ใช้ tqdm ครอบเพื่อให้เห็นความคืบหน้า
        results = list(tqdm(executor.map(run_single_backtest, tasks), total=len(tasks), desc="Simulating"))

    # สรุปผล
    df = pd.DataFrame(results)
    print("\n--- 📊 Monte Carlo Summary ---")
    print(f"เฉลี่ย CAGR: {df['CAGR_%'].mean():.2f}%")
    print(f"ความเสี่ยงพอร์ตแตก (Margin Calls > 0): {len(df[df['Margin_Calls'] > 0])} รอบ")
    
    os.makedirs('results', exist_ok=True)
    df.to_csv(f"results/monte_carlo_{datetime.now().strftime('%Y%m%d_%H%M')}.csv", index=False)

if __name__ == "__main__":
    main()