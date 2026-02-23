"""
optimizer.py
โมดูลสำหรับค้นหาพารามิเตอร์ที่เหมาะสมที่สุด (Grid Search Optimization & Robustness)

อัปเดต: ย้ายการจัดเก็บผลลัพธ์ CSV ไปไว้ที่โฟลเดอร์ /results
"""

import os
import yaml
import itertools
import numpy as np
import pandas as pd
from datetime import datetime
from tqdm import tqdm

from src.oracle.oracle import OracleModule, OracleConfig
from src.lp.lp import LPModule, LPConfig
from src.perp.perp import PerpModule, PerpConfig
from src.strategy.strategy import StrategyModule, StrategyConfig
from src.portfolio.portfolio import PortfolioModule
from src.engine.backtest_engine import BacktestEngine

def load_config(file_path: str = 'config.yaml') -> dict:
    with open(file_path, 'r', encoding='utf-8') as file:
        return yaml.safe_load(file)

def parse_opt_range(value_list):
    if isinstance(value_list, list) and len(value_list) == 4:
        is_on, start, stop, step = value_list
        if is_on:
            gen = np.arange(start, stop + (step / 2), step)
            return [round(float(x), 4) for x in gen]
    return value_list

def run_optimizer():
    cfg = load_config()
    opt_space = cfg.get('optimization_space', {})
    
    if not opt_space:
        print("⚠️ ไม่พบ 'optimization_space' ใน config.yaml")
        return

    print("="*65)
    print("🔬 QUANT LAB: Optimizer & Robustness Tester [On/Off Mode]")
    print("="*65)

    base_vals = {
        'range_width': float(cfg['lp']['range_width']),
        'rebalance_threshold': float(cfg['lp']['rebalance_threshold']),
        'hedge_threshold': float(cfg['strategy']['hedge_threshold']),
        'safety_net_pct': float(cfg['strategy']['safety_net_pct']),
        'annual_volatility': float(cfg['market']['annual_volatility']),
        'seed': int(cfg['market']['seed']) if cfg['market']['seed'] not in ['null', None, 'None', ''] else 42
    }

    keys = list(opt_space.keys())
    values_to_combine = []
    
    for k in keys:
        v_list = opt_space[k]
        if isinstance(v_list, list) and len(v_list) == 4:
            is_on, start, stop, step = v_list
            if is_on:
                gen = np.arange(start, stop + (step / 2), step)
                if k == 'seed':
                    values_to_combine.append([int(x) for x in gen])
                else:
                    values_to_combine.append([round(float(x), 4) for x in gen])
            else:
                values_to_combine.append([base_vals.get(k, 0)]) 
        else:
            values_to_combine.append([base_vals.get(k, 0)]) 

    combinations = list(itertools.product(*values_to_combine))
    print(f"[*] Total Combinations to test: {len(combinations)} variants")
    print("-" * 65)

    days_to_run = int(cfg['market']['days_to_run'])
    start_price = float(cfg['market']['start_price'])
    lp_capital = float(cfg['capital']['lp_capital'])
    perp_capital = float(cfg['capital']['perp_capital'])
    total_capital = lp_capital + perp_capital
    leverage = float(cfg['capital']['leverage'])
    funding_rate_8h = float(cfg['costs']['funding_rate_8h'])
    harvest_cfg = cfg.get('harvesting', {})

    market_data_cache = {} 
    results_list = []

    for combo in tqdm(combinations, desc="Optimizing"):
        params = dict(zip(keys, combo))
        
        c_range = params.get('range_width', base_vals['range_width'])
        c_rebal = params.get('rebalance_threshold', base_vals['rebalance_threshold'])
        c_hedge = params.get('hedge_threshold', base_vals['hedge_threshold'])
        c_safety = params.get('safety_net_pct', base_vals['safety_net_pct'])
        c_vol = params.get('annual_volatility', base_vals['annual_volatility'])
        c_seed = params.get('seed', base_vals['seed'])

        market_key = (days_to_run, c_vol, c_seed)
        if market_key not in market_data_cache:
            o_cfg = OracleConfig(start_price=start_price, days=days_to_run, annual_volatility=c_vol, seed=c_seed)
            market_data_cache[market_key] = OracleModule().generate_data(o_cfg)
        
        data = market_data_cache[market_key]

        lp_cfg = LPConfig(
            initial_capital=lp_capital, 
            range_width=c_range, 
            rebalance_threshold=c_rebal,
            fee_mode='base_apr',
            base_apr=float(cfg['lp']['base_apr']),
            gas_fee=float(cfg['costs']['gas_fee_usd']),
            slippage=float(cfg['costs']['slippage'])
        )
        
        strat_cfg = StrategyConfig(
            hedge_mode=cfg['strategy']['hedge_mode'],
            use_safety_net=bool(cfg['strategy']['use_safety_net']),
            safety_net_pct=c_safety,
            hedge_threshold=c_hedge,
            ema_period=int(cfg['strategy']['ema_period'])
        )
        
        perp_cfg = PerpConfig(leverage=leverage, taker_fee=float(cfg['costs']['perp_taker_fee']))

        lp = LPModule(lp_cfg, start_price)
        perp = PerpModule(perp_cfg)
        strategy = StrategyModule(lp, perp)
        portfolio = PortfolioModule(total_capital)
        portfolio.allocate_to_lp(lp_capital) 
        
        engine = BacktestEngine(oracle=OracleModule(), lp=lp, perp=perp, strategy=strategy, portfolio=portfolio)
        res_df = engine.run(data, strat_cfg, funding_rate=funding_rate_8h, harvest_config=harvest_cfg)
        
        final_equity = res_df['net_equity'].iloc[-1]
        total_withdrawn = res_df['total_withdrawn'].iloc[-1]
        total_wealth = final_equity + total_withdrawn
        
        net_profit = total_wealth - total_capital
        cagr = (pow(1 + (net_profit / total_capital), 365 / days_to_run) - 1) * 100
        
        wealth_series = res_df['net_equity'] + res_df['total_withdrawn']
        roll_max = wealth_series.cummax()
        max_drawdown = ((wealth_series - roll_max) / roll_max).min() * 100
        
        hourly_returns = wealth_series.pct_change().dropna()
        std_dev = hourly_returns.std()
        sharpe = (hourly_returns.mean() / std_dev) * np.sqrt(365 * 24) if std_dev > 0 else 0

        margin_call_count = len(engine.margin_call_events)
        min_cex_margin = res_df['cex_available_margin'].min()

        results_list.append({
            'Volatility': c_vol,
            'Seed': c_seed,
            'Range_Width': c_range,
            'Rebal_Thresh': c_rebal,
            'Hedge_Thresh': c_hedge,
            'Safety_Pct': c_safety,
            'CAGR_%': round(cagr, 2),
            'Max_DD_%': round(max_drawdown, 2),
            'Sharpe': round(sharpe, 2),
            'Margin_Calls': margin_call_count,
            'Min_CEX_Margin': round(min_cex_margin, 2),
            'Hedge_Trades': engine.hedge_count,
            'Rebalances': lp.rebalance_count
        })

    df_results = pd.DataFrame(results_list)
    df_safe = df_results[df_results['Margin_Calls'] == 0].copy()
    
    print("\n" + "="*65)
    print("🏆 TOP 5 SAFEST & MOST PROFITABLE CONFIGURATIONS")
    print("="*65)
    
    if df_safe.empty:
        print("🚨 ไม่มี Config ไหนเลยที่รอดจาก Margin Call! (ความผันผวนอาจจะสูงเกินไป หรือทุน CEX น้อยไป)")
    else:
        df_safe = df_safe.sort_values(by='CAGR_%', ascending=False)
        print(df_safe.head(5).to_string(index=False))

    # [FIX] สร้างโฟลเดอร์ results ถ้ายั้งไม่มี และเซฟลงไปในนั้น
    os.makedirs('results', exist_ok=True)
    output_file = os.path.join('results', f"optimization_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
    
    df_results.sort_values(by=['Margin_Calls', 'CAGR_%'], ascending=[True, False]).to_csv(output_file, index=False)
    
    print("-" * 65)
    print(f"💾 บันทึกผลลัพธ์ฉบับเต็มลงใน: {output_file}")

if __name__ == "__main__":
    run_optimizer()