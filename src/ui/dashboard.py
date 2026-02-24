"""
src/ui/dashboard.py
โมดูล Interactive Web Dashboard สำหรับจำลองระบบด้วย Streamlit

ทำหน้าที่เป็น Frontend UI เพื่อให้ PD สามารถปรับพารามิเตอร์แบบ Real-time
โดยยังคงเรียกใช้ Core Engine ที่เป็น Python/Pandas อยู่เบื้องหลัง
"""

__version__ = "1.0.1"
__author__ = "LP-Rebal-Coding (Senior Quant Developer)"

import sys
import os

# [CRITICAL FIX] บังคับให้ Python รู้จัก Project Root Directory เพื่อแก้ปัญหา ModuleNotFoundError
# ดึง Path ปัจจุบัน แล้วถอยกลับไป 2 ระดับ (จาก src/ui/ ถอยไปที่ Root)
project_root: str = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
from typing import Dict, Any

# ตอนนี้สามารถ Import 'src' ได้อย่างปลอดภัยแล้ว
from src.oracle.oracle import OracleModule, OracleConfig
from src.lp.lp import LPModule, LPConfig
from src.perp.perp import PerpModule, PerpConfig
from src.strategy.strategy import StrategyModule, StrategyConfig
from src.portfolio.portfolio import PortfolioModule
from src.engine.backtest_engine import BacktestEngine


def run_simulation(params: Dict[str, Any]) -> pd.DataFrame:
    """
    ฟังก์ชันสำหรับรัน Backtest Engine ตามพารามิเตอร์ที่รับมาจาก UI

    Args:
        params (Dict[str, Any]): Dictionary บรรจุค่าพารามิเตอร์จาก Streamlit UI

    Returns:
        pd.DataFrame: ข้อมูลประวัติการเทรด (Portfolio State History)
    """
    # 1. Oracle Setup
    oracle_cfg = OracleConfig(
        start_price=2500.0, 
        days=params['days'], 
        annual_volatility=params['volatility'], 
        seed=42
    )
    oracle = OracleModule()
    data: pd.DataFrame = oracle.generate_data(oracle_cfg)
    
    # 2. Portfolio Setup
    initial_capital: float = 10000.0
    portfolio = PortfolioModule(initial_capital)
    
    # 3. LP Tiers Setup
    t1_cap: float = initial_capital * (params['tier1_alloc'] / 100.0)
    t2_cap: float = initial_capital * (params['tier2_alloc'] / 100.0)
    
    lp1_cfg = LPConfig(range_width=0.05, base_apr=0.10, fee_mode='base_apr')
    lp1 = LPModule(lp1_cfg, oracle_cfg.start_price)
    portfolio.allocate_to_lp(t1_cap)
    
    lp2_cfg = LPConfig(range_width=0.10, base_apr=0.10, fee_mode='base_apr')
    lp2 = LPModule(lp2_cfg, oracle_cfg.start_price)
    portfolio.allocate_to_lp(t2_cap)
    
    # 4. Perp & Strategy Setup
    perp_cfg = PerpConfig(leverage=1.0)
    perp = PerpModule(perp_cfg)
    
    strat_cfg = StrategyConfig(
        hedge_mode=params['hedge_mode'], 
        hedge_threshold=params['hedge_threshold']
    )
    strategy = StrategyModule([lp1, lp2], perp)
    
    # 5. Engine Execution
    engine = BacktestEngine(oracle, [lp1, lp2], perp, strategy, portfolio)
    results: pd.DataFrame = engine.run(data, strat_cfg)
    
    return results


def main() -> None:
    """ฟังก์ชันหลักสำหรับวาดหน้าจอ Streamlit UI"""
    st.set_page_config(page_title="LP-Rebal Strategy Simulator", layout="wide")
    
    st.title("🧪 Inventory-based LP & Smart Hedge Simulator")
    st.markdown("Interactive Dashboard สำหรับจำลอง Multi-Tiered LP และ Perp Hedging")
    
    # Sidebar สำหรับปรับพารามิเตอร์
    with st.sidebar:
        st.header("⚙️ Simulation Parameters")
        
        st.subheader("Market Conditions (Oracle)")
        days: int = st.slider("Simulation Days", min_value=30, max_value=365, value=90, step=30)
        volatility: float = st.slider("Annual Volatility", min_value=0.1, max_value=1.5, value=0.7, step=0.1)
        
        st.subheader("Portfolio Allocation")
        tier1_alloc: int = st.slider("Tier 1 (±5%) Allocation %", min_value=0, max_value=100, value=60, step=10)
        tier2_alloc: int = 100 - tier1_alloc
        st.write(f"Tier 2 (±10%) Allocation %: **{tier2_alloc}%**")
        
        st.subheader("Strategy Config")
        hedge_mode: str = st.radio("Hedge Mode", options=['always', 'smart'], index=0)
        hedge_threshold: float = st.slider("Hedge Threshold (Drift)", min_value=0.01, max_value=0.10, value=0.05, step=0.01)
        
        run_btn: bool = st.button("🚀 Run Simulation", use_container_width=True)

    # Main Panel
    if run_btn:
        with st.spinner('Running quantitative simulation...'):
            params: Dict[str, Any] = {
                'days': days,
                'volatility': volatility,
                'tier1_alloc': tier1_alloc,
                'tier2_alloc': tier2_alloc,
                'hedge_mode': hedge_mode,
                'hedge_threshold': hedge_threshold
            }
            
            results = run_simulation(params)
            
            # คำนวณ Metrics
            initial_cap: float = 10000.0
            final_equity: float = results['net_equity'].iloc[-1]
            roi: float = ((final_equity - initial_cap) / initial_cap) * 100
            
            # แสดง Metrics แบบ 3 คอลัมน์
            col1, col2, col3 = st.columns(3)
            col1.metric("Initial Capital", f"${initial_cap:,.2f}")
            col2.metric("Final Net Equity", f"${final_equity:,.2f}", f"{roi:.2f}%")
            col3.metric("Simulation Days", f"{days} Days")
            
            # วาดกราฟ Net Equity
            st.subheader("📈 Net Equity Curve")
            fig, ax = plt.subplots(figsize=(10, 4))
            ax.plot(results.index, results['net_equity'], label='Net Equity (USD)', color='blue')
            ax.set_title("Portfolio Growth Over Time")
            ax.set_xlabel("Time (Ticks)")
            ax.set_ylabel("USD")
            ax.grid(True, alpha=0.3)
            ax.legend()
            st.pyplot(fig)
            
            # แสดง Raw Data บางส่วน
            st.subheader("📊 Raw Portfolio Data")
            st.dataframe(results.tail(10))
    else:
        st.info("👈 ปรับพารามิเตอร์ด้านซ้าย และกด 'Run Simulation' เพื่อเริ่มต้น")

if __name__ == "__main__":
    main()