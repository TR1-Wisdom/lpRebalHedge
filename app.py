"""
app.py (Streamlit UI for Quant Lab v1.5.2)
- อัปเกรด: Config Sync Edition (ดึงค่าตั้งต้นจาก config.yaml เสมอ)
- แก้ไข: นำค่า Hardcoded (Funding Rate, Safety Net) ออกมาให้ปรับผ่าน UI ได้
- รับประกันผลลัพธ์ตรงกับ Optimizer 100% เมื่อใช้พารามิเตอร์ชุดเดียวกัน
"""

import os
import yaml
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime

from src.oracle.oracle import OracleModule, OracleConfig
from src.lp.lp import LPModule, LPConfig
from src.perp.perp import PerpModule, PerpConfig
from src.strategy.strategy import StrategyModule, StrategyConfig
from src.portfolio.portfolio import PortfolioModule
from src.engine.backtest_engine import BacktestEngine

# --- Load Configuration ---
def load_default_config():
    try:
        with open('config.yaml', 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    except Exception:
        # Fallback หากไฟล์ config.yaml หายไป
        return {}

cfg = load_default_config()

def get_cfg(section, key, fallback):
    return cfg.get(section, {}).get(key, fallback)

# --- Page Configuration ---
st.set_page_config(page_title="Quant Lab v1.5.2", layout="wide", page_icon="🚀")

st.markdown("""
    <style>
    .main { background-color: #f8fafc; }
    .stMetric { background-color: white; padding: 15px; border-radius: 15px; border: 1px solid #e2e8f0; }
    [data-testid="stSidebar"] { background-color: #f1f5f9; }
    </style>
    """, unsafe_allow_html=True)

# --- Sidebar Control Panel ---
with st.sidebar:
    st.title("⚙️ Strategy Control")
    st.caption("v1.5.2 - Synced with config.yaml")
    
    with st.expander("📊 Market Simulator", expanded=True):
        start_price = st.number_input("Start Price ($)", value=float(get_cfg('market', 'start_price', 2000.0)))
        days = st.slider("Duration (Days)", 7, 360, int(get_cfg('market', 'days_to_run', 120)))
        vol_def = float(get_cfg('market', 'annual_volatility', 0.5)) * 100
        volatility = st.slider("Annual Volatility (%)", 10.0, 150.0, float(vol_def)) / 100
        seed_def = get_cfg('market', 'seed', 42)
        seed = st.number_input("Random Seed", value=int(seed_def) if seed_def else 42)

    with st.expander("🚜 LP Configuration", expanded=True):
        lp_cap = st.number_input("LP Capital ($)", value=float(get_cfg('capital', 'lp_capital', 10000.0)))
        base_apr_def = float(get_cfg('lp', 'base_apr', 0.05)) * 100
        base_apr = st.slider("Base APR (%)", 1.0, 20.0, float(base_apr_def)) / 100
        range_def = float(get_cfg('lp', 'range_width', 0.10)) * 100
        range_width = st.slider("Range Width (±%)", 1.0, 50.0, float(range_def)) / 100
        rebal_def = float(get_cfg('lp', 'rebalance_threshold', 0.20)) * 100
        rebal_thresh = st.slider("Rebalance Threshold (%)", 5.0, 80.0, float(rebal_def)) / 100

    with st.expander("🛡️ Hedge & Capital Management", expanded=True):
        perp_cap = st.number_input("CEX Capital ($)", value=float(get_cfg('capital', 'perp_capital', 3000.0)))
        leverage = st.number_input("Leverage (x)", value=float(get_cfg('capital', 'leverage', 5.0)))
        
        hedge_mode_def = get_cfg('strategy', 'hedge_mode', 'smart')
        h_index = 0 if hedge_mode_def == 'always' else 1
        hedge_mode = st.selectbox("Hedge Mode", options=['always', 'smart'], index=h_index)
        
        hedge_thresh_def = float(get_cfg('strategy', 'hedge_threshold', 0.10)) * 100
        hedge_thresh = st.slider("Hedge Threshold (%)", 1.0, 30.0, float(hedge_thresh_def)) / 100
        
        # [FIX] นำค่า Safety Net ออกมาให้ปรับได้
        safe_net_def = float(get_cfg('strategy', 'safety_net_pct', 0.02)) * 100
        safety_net_pct = st.slider("Safety Net Threshold (%)", 1.0, 15.0, float(safe_net_def)) / 100
        
        cross_rebal_freq = st.slider("Cross-Margin Freq (Days)", 1, 60, int(get_cfg('capital', 'rebalance_freq_days', 15)))

    with st.expander("💸 Costs & Fees (Advanced)", expanded=False):
        # [FIX] นำค่า Funding Rate ออกมาให้ปรับได้
        fund_def = float(get_cfg('costs', 'funding_rate_8h', 0.0001)) * 100
        funding_rate_8h = st.number_input("Funding Rate per 8H (%)", value=float(fund_def), format="%.4f") / 100
        interval = st.select_slider("Execution Lag (Min)", options=[1, 5, 15, 30, 60], value=int(get_cfg('execution', 'interval_minutes', 5)))

# --- Simulation Execution Logic ---
def run_quant_sim():
    oracle_cfg = OracleConfig(start_price=start_price, days=days, annual_volatility=volatility, seed=seed, timeframe='5m')
    
    lp_cfg = LPConfig(
        initial_capital=lp_cap, 
        range_width=range_width, 
        rebalance_threshold=rebal_thresh, 
        base_apr=base_apr, 
        fee_mode='base_apr', 
        gas_fee=float(get_cfg('costs', 'gas_fee_usd', 2.0)), 
        slippage=float(get_cfg('costs', 'slippage', 0.001))
    )
    
    strat_cfg = StrategyConfig(
        hedge_mode=hedge_mode, 
        use_safety_net=True, 
        safety_net_pct=safety_net_pct,  # [FIXED] ใช้ค่าจาก UI (ซึ่งดึงมาจาก YAML)
        hedge_threshold=hedge_thresh, 
        ema_period=int(get_cfg('strategy', 'ema_period', 200))
    )
    
    perp_cfg = PerpConfig(
        leverage=leverage, 
        taker_fee=float(get_cfg('costs', 'perp_taker_fee', 0.0005))
    )
    
    oracle = OracleModule()
    lp = LPModule(lp_cfg, start_price)
    perp = PerpModule(perp_cfg)
    strategy = StrategyModule(lp, perp)
    portfolio = PortfolioModule(lp_cap + perp_cap)
    portfolio.allocate_to_lp(lp_cap)
    
    engine = BacktestEngine(oracle, lp, perp, strategy, portfolio)
    data = oracle.generate_data(oracle_cfg)
    
    results = engine.run(
        data, strat_cfg, 
        funding_rate=funding_rate_8h, # [FIXED] ใช้ค่าจาก UI
        cross_rebalance_config={'enabled': True, 'freq_days': cross_rebal_freq}, 
        execution_interval_min=interval
    )
    results['price'] = data['close'].values
    
    if 'lp_eth' in results.columns and 'perp_size' in results.columns:
        results['residual_delta'] = results['lp_eth'] - results['perp_size']
    else:
        results['lp_eth'] = 0.0
        results['perp_size'] = 0.0
        results['residual_delta'] = 0.0
        
    if 'event' not in results.columns:
        results['event'] = ""
    
    return results, engine, lp

# --- Main UI ---
st.title("🚀 Quant Lab: Delta Hedge Dashboard")
st.caption(f"Status: Synchronized (v1.5.2) | Config Loaded | Analysis: {datetime.now().strftime('%H:%M:%S')}")

if st.button("▶️ RUN BACKTEST SIMULATION", use_container_width=True):
    with st.spinner("ประมวลผลกลยุทธ์และจำลองสถานการณ์ตลาด..."):
        results, engine, lp_obj = run_quant_sim()
        
        initial_cap = lp_cap + perp_cap
        final_net_equity = results['net_equity'].iloc[-1]
        total_withdrawn = results['total_withdrawn'].iloc[-1]
        total_wealth = final_net_equity + total_withdrawn
        
        roi_pct = ((total_wealth / initial_cap) - 1) * 100
        cagr = (pow(1 + (roi_pct/100), 365/days) - 1) * 100
        
        wealth_series = results['net_equity'] + results['total_withdrawn']
        roll_max = wealth_series.cummax()
        dd = (wealth_series - roll_max) / roll_max
        max_dd = dd.min() * 100

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total Wealth Created", f"${total_wealth:,.2f}", f"{roi_pct:+.2f}% ROI")
        m2.metric("Annualized CAGR", f"{cagr:.2f}%")
        m3.metric("Max Drawdown", f"{max_dd:.2f}%", delta_color="inverse")
        m4.metric("Effective APR", f"{lp_obj.config.base_apr * lp_obj.multiplier * 100:.1f}%")

        # --- Charts Section ---
        st.subheader("📈 Portfolio Analysis")
        
        fig1 = make_subplots(specs=[[{"secondary_y": True}]])
        fig1.add_trace(go.Scatter(x=results.index, y=results['net_equity'], name="Live Equity", line=dict(color='#6366f1', width=2)), secondary_y=False)
        fig1.add_trace(go.Scatter(x=results.index, y=wealth_series, name="Total Wealth", line=dict(color='#10b981', width=2, dash='dash')), secondary_y=False)
        fig1.add_trace(go.Scatter(x=results.index, y=results['price'], name="ETH Price", line=dict(color='#94a3b8', width=1), opacity=0.4), secondary_y=True)
        fig1.update_layout(title="Portfolio Equity Progression", hovermode="x unified", height=450)
        st.plotly_chart(fig1, use_container_width=True)

        st.subheader("🛡️ Hedge Engine Dynamics (LP vs CEX)")
        perp_equity_series = results['cex_wallet_balance'] + results['perp_pnl']
        
        fig2 = make_subplots(specs=[[{"secondary_y": True}]])
        fig2.add_trace(go.Scatter(x=results.index, y=results['lp_value'], name="LP Value (On-chain)", line=dict(color='#10b981', width=2)), secondary_y=False)
        fig2.add_trace(go.Scatter(x=results.index, y=perp_equity_series, name="Perp Margin (CEX)", line=dict(color='#3b82f6', width=2)), secondary_y=False)
        fig2.add_trace(go.Scatter(x=results.index, y=results['price'], name="ETH Price", line=dict(color='#94a3b8', width=1), opacity=0.25), secondary_y=True)
        fig2.update_layout(title="Component Equity Analysis (Mirroring)", hovermode="x unified", height=450)
        st.plotly_chart(fig2, use_container_width=True)

        # --- Stats Breakdown ---
        c_a, c_b = st.columns(2)
        with c_a:
            st.markdown("### 📊 Activity Stats")
            st.write(f"- LP Rebalances: {engine.lp.rebalance_count} times")
            st.write(f"- Hedge Trades: {engine.hedge_count} times")
            st.write(f"- Cross-Margin Sweeps: {engine.cross_rebalance_count} times")
            st.write(f"- Margin Call Rejects: `{len(engine.margin_call_events)}` 🚨")
        with c_b:
            st.markdown("### 💰 PnL Breakdown")
            st.write(f"- Gross LP Fees: `${engine.lp.accumulated_fees:,.2f}`")
            st.write(f"- Net Funding Rate: `${engine.perp.total_funding_pnl:,.2f}`")
            st.write(f"- Min Margin Buffer: `${results['cex_available_margin'].min():,.2f}`")

        # --- Download Button ---
        csv = results.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 Download Full CSV",
            data=csv,
            file_name=f"quant_full_result_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
            mime="text/csv",
            use_container_width=True
        )
else:
    st.info("👈 ตัวแปรที่ซ่อนอยู่ถูกลิงก์กับ config.yaml แล้ว กดปุ่ม RUN เพื่อเริ่มการจำลองผลได้เลยครับ")