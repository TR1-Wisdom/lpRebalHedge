"""
src/ui/dashboard.py
โมดูล Interactive Web Dashboard (Full Version)

อัปเดต: v2.3.0 (Path & Attribute Sync)
- [FIX] แก้ไข ModuleNotFoundError โดยการจัดลำดับ sys.path ใหม่
- [SYNC] บังคับใช้ชื่อคอลัมน์ lp_value และ perp_pnl ให้ตรงกับ BacktestEngine v1.6.2
- [SYNC] ปรับปรุงสถิติให้ดึงจาก cross_rebalance_count แทน Margin Call
"""

__version__ = "2.3.0"
__author__ = "LP-Rebal-Coding (Senior Quant Developer)"

import os
import sys

# =========================================================
# [CRITICAL] FIX: ModuleNotFoundError 'src'
# =========================================================
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, "../../"))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from typing import Dict, Any, Tuple

# Import Project Modules
try:
    from src.oracle.oracle import OracleModule, OracleConfig
    from src.lp.lp import LPModule, LPConfig
    from src.perp.perp import PerpModule, PerpConfig
    from src.strategy.strategy import StrategyModule, StrategyConfig
    from src.portfolio.portfolio import PortfolioModule
    from src.engine.backtest_engine import BacktestEngine
except ImportError as e:
    st.error(f"❌ ไม่สามารถโหลดโมดูลระบบได้: {e}")
    st.info(f"Path ปัจจุบัน: {sys.path[0]}")
    st.stop()

def run_simulation(params: Dict[str, Any]) -> Tuple[pd.DataFrame, BacktestEngine]:
    """ฟังก์ชันหลักในการเชื่อมต่อทุกโมดูลเพื่อรัน Simulation"""
    
    # 1. Oracle: สร้างข้อมูลราคาจำลอง
    oracle_cfg = OracleConfig(
        start_price=params['start_price'], 
        days=params['days'], 
        annual_volatility=params['volatility'], 
        seed=42
    )
    oracle = OracleModule()
    data: pd.DataFrame = oracle.generate_data(oracle_cfg)
    
    # 2. Portfolio: บัญชีแยกประเภท (Sync กับ config.yaml)
    lp_cap = params['lp_capital']
    perp_cap = params['perp_capital']
    initial_total = lp_cap + perp_cap
    
    portfolio = PortfolioModule(initial_total)
    # แบ่งเงินตามโครงสร้างจริง: CEX Wallet เก็บไว้เทรด, ส่วน LP แยกออกไปค้ำ LP
    portfolio.cex_wallet_balance = perp_cap
    if hasattr(portfolio, 'lp_allocated_cash'):
        portfolio.lp_allocated_cash = lp_cap
    
    # 3. Multi-Tier LP Setup
    t1_share = params['tier1_share'] / 100.0
    t2_share = 1.0 - t1_share
    
    lp_configs = []
    # Tier 1
    lp1_cfg = LPConfig(
        range_width=params['tier1_range'], 
        base_apr=0.10, 
        fee_mode='base_apr',
        initial_capital=lp_cap * t1_share
    )
    lp1 = LPModule(lp1_cfg, params['start_price'])
    lp_configs.append(lp1)
    
    # Tier 2 (ถ้ามี)
    if t2_share > 0:
        lp2_cfg = LPConfig(
            range_width=params['tier2_range'], 
            base_apr=0.10, 
            fee_mode='base_apr',
            initial_capital=lp_cap * t2_share
        )
        lp2 = LPModule(lp2_cfg, params['start_price'])
        lp_configs.append(lp2)
    
    # 4. Perp & Strategy
    perp_cfg = PerpConfig(leverage=params['leverage'])
    perp = PerpModule(perp_cfg)
    
    strat_cfg = StrategyConfig(
        hedge_mode=params['hedge_mode'], 
        hedge_threshold=params['hedge_threshold'],
        hysteresis_band_pct=params['hysteresis_band'],
        safety_net_pct=params['safety_net_pct']
    )
    strategy = StrategyModule(lp_configs, perp)
    
    # 5. Engine: ตัวขับเคลื่อน Simulation
    engine = BacktestEngine(oracle, lp_configs, perp, strategy, portfolio)
    
    results: pd.DataFrame = engine.run(
        data_feed=data, 
        strategy_config=strat_cfg,
        withdraw_passive_income=params['withdraw_income'],
        auto_transfer_interval_days=params['auto_transfer_days']
    )
    
    return results, engine

def main() -> None:
    st.set_page_config(page_title="Quant Lab: LP-Rebal", layout="wide")
    
    st.title("🧪 Multi-Tier LP & Delta Hedge Simulator")
    st.markdown("ระบบจำลองกลยุทธ์ Rebalance ระหว่าง LP DeFi และ CEX Perp (v2.3.0 Standardized)")
    
    # --- Sidebar ---
    with st.sidebar:
        st.header("⚙️ Config Parameters")
        
        with st.expander("Market Settings", expanded=True):
            start_price = st.number_input("Start Price", value=2000.0)
            days = st.slider("Simulation Days", 30, 365, 360)
            volatility = st.slider("Annual Volatility", 0.1, 1.5, 0.7)
            
        with st.expander("Capital Allocation", expanded=True):
            lp_cap = st.number_input("LP Capital (DeFi)", value=10000.0, step=1000.0)
            perp_cap = st.number_input("Perp Capital (CEX)", value=5000.0, step=1000.0)
            leverage = st.slider("Perp Leverage", 1.0, 10.0, 5.0)
            
        with st.expander("LP Tier Settings", expanded=True):
            t1_share = st.slider("Tier 1 Share (%)", 0, 100, 60)
            t1_range = st.number_input("Tier 1 Range (±%)", value=0.05, format="%.2f")
            t2_range = st.number_input("Tier 2 Range (±%)", value=0.10, format="%.2f")
            
        with st.expander("Strategy (Hedge)", expanded=False):
            hedge_mode = st.selectbox("Hedge Mode", ["always", "smart"], index=0)
            hedge_threshold = st.slider("Hedge Threshold (Drift)", 0.0, 0.5, 0.1)
            hysteresis_band = st.slider("Anti-Whipsaw (%)", 0.0, 0.02, 0.005, format="%.3f")
            safety_net_pct = st.slider("Safety Net (%)", 0.0, 0.2, 0.1)

        with st.expander("Automation", expanded=False):
            withdraw_income = st.checkbox("Passive Income Withdrawal", value=False)
            auto_transfer_days = st.number_input("Auto Rebalance Capital Days", value=30)
            
        run_btn = st.button("🚀 Run Backtest", use_container_width=True, type="primary")

    # --- Execution ---
    if run_btn:
        params = {
            'start_price': start_price, 'days': days, 'volatility': volatility,
            'lp_capital': lp_cap, 'perp_capital': perp_cap, 'leverage': leverage,
            'tier1_share': t1_share, 'tier1_range': t1_range, 'tier2_range': t2_range,
            'hedge_mode': hedge_mode, 'hedge_threshold': hedge_threshold,
            'hysteresis_band': hysteresis_band, 'safety_net_pct': safety_net_pct,
            'withdraw_income': withdraw_income, 'auto_transfer_days': auto_transfer_days
        }
        
        try:
            results, engine = run_simulation(params)
            
            # --- Metrics ---
            initial_cap = lp_cap + perp_cap
            final_equity = results['net_equity'].iloc[-1]
            total_roi = ((final_equity - initial_cap) / initial_cap) * 100
            
            st.markdown("### 📊 Performance Metrics")
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Final Net Equity", f"${final_equity:,.2f}", f"{total_roi:.2f}%")
            m2.metric("Hedge Adjustments", f"{engine.hedge_count} Times")
            m3.metric("Auto Sweep/Rebal", f"{engine.cross_rebalance_count} Events")
            m4.metric("Withdrawals", f"{engine.withdrawal_count} Times")
            
            # --- Charts ---
            st.markdown("---")
            st.subheader("📈 Net Equity & Portfolio Components")
            
            fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10), sharex=True)
            
            # Plot 1: Net Equity
            ax1.plot(results['timestamp'], results['net_equity'], color='#2ecc71', label='Total Net Equity', linewidth=2)
            ax1.axhline(y=initial_cap, color='red', linestyle='--', alpha=0.5, label='Initial Capital')
            ax1.set_ylabel("Portfolio Value (USD)")
            ax1.legend()
            ax1.grid(alpha=0.2)
            
            # Plot 2: Components (Sync: lp_value, perp_pnl)
            ax2.fill_between(results['timestamp'], results['lp_value'], color='#3498db', alpha=0.3, label='LP Value (DeFi)')
            ax2.plot(results['timestamp'], results['perp_pnl'], color='#e74c3c', label='Perp Unrealized PnL (CEX)')
            ax2.axhline(y=0, color='black', alpha=0.3)
            ax2.set_ylabel("USD Value / PnL")
            ax2.legend()
            ax2.grid(alpha=0.2)
            
            plt.tight_layout()
            st.pyplot(fig)
            
            # --- Multiplier Comparison ---
            st.markdown("---")
            st.subheader("⚖️ Tier Efficiency (Multiplier)")
            col_a, col_b = st.columns([2, 1])
            
            with col_a:
                tier_names = [f"Tier 1 (±{t1_range*100:.1f}%)", f"Tier 2 (±{t2_range*100:.1f}%)"]
                mults = [lp.multiplier for lp in engine.lp_list]
                
                fig_bar, ax_bar = plt.subplots(figsize=(8, 4))
                sns.barplot(x=tier_names, y=mults, ax=ax_bar, palette="Blues_d")
                ax_bar.set_ylabel("Capital Efficiency Multiplier (x)")
                st.pyplot(fig_bar)
                
            with col_b:
                st.info("💡 **Multiplier** แสดงถึงประสิทธิภาพการใช้ทุน ยิ่งช่วง Range แคบ Multiplier จะยิ่งสูง แต่ความเสี่ยงในการหลุด Range ก็จะสูงขึ้นตาม")

            # --- Data ---
            with st.expander("🔍 View Raw Logs"):
                st.dataframe(results.tail(100), use_container_width=True)
                
        except Exception as e:
            st.error(f"🚨 Simulation Error: {e}")
            st.exception(e)
            
    else:
        st.info("กรุณาปรับพารามิเตอร์ด้านซ้ายมือแล้วกด 'Run Backtest' เพื่อเริ่มการวิเคราะห์")

if __name__ == "__main__":
    main()