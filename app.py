"""
app.py (Quant Lab Dashboard v1.5.6 Master)
- ระบบจำลองกลยุทธ์ Inventory-based LP & Smart Hedge
- ซิงค์พารามิเตอร์อัตโนมัติจาก config.yaml
- วิเคราะห์ Residual Risk และ PnL Decomposition รายละเอียดสูง
- รองรับระบบบัญชีแบบ Realized PnL และ Fee Scaling Fix
"""

import os
import yaml
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime

# นำเข้า Core Modules จากโครงสร้างโปรเจกต์
from src.oracle.oracle import OracleModule, OracleConfig
from src.lp.lp import LPModule, LPConfig
from src.perp.perp import PerpModule, PerpConfig
from src.strategy.strategy import StrategyModule, StrategyConfig
from src.portfolio.portfolio import PortfolioModule, TransactionType
from src.engine.backtest_engine import BacktestEngine

# --- 1. Configuration Management ---
def load_default_config():
    """โหลดค่าเริ่มต้นจากไฟล์ config.yaml ถ้ามี"""
    if os.path.exists('config.yaml'):
        with open('config.yaml', 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    return {}

cfg = load_default_config()

def get_c(section, key, fallback):
    """Helper สำหรับดึงค่า Config แบบมีค่า Fallback"""
    return cfg.get(section, {}).get(key, fallback)

# --- 2. UI Styling ---
st.set_page_config(page_title="Quant Lab: LP & Smart Hedge", layout="wide", page_icon="🏎️")

st.markdown("""
    <style>
    .main { background-color: #f8fafc; }
    .stMetric { background-color: white; padding: 20px; border-radius: 15px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1); border: 1px solid #e2e8f0; }
    .reportview-container .main { color: #1e293b; }
    h1, h2, h3 { color: #1e293b; font-weight: 800; }
    </style>
    """, unsafe_allow_html=True)

# --- 3. Sidebar: Command Center ---
with st.sidebar:
    st.header("🎮 Dashboard Control")
    st.caption("v1.5.6 Master | Engine v1.8.5")
    
    with st.expander("📈 Market Simulator", expanded=True):
        start_price = st.number_input("ETH Start Price ($)", value=float(get_c('market', 'start_price', 2000.0)))
        days = st.slider("Duration (Days)", 7, 365, int(get_c('market', 'days_to_run', 90)))
        vol_def = float(get_c('market', 'annual_volatility', 0.8)) * 100
        volatility = st.slider("Annual Volatility (%)", 10, 150, int(vol_def)) / 100
        seed = st.number_input("Random Seed", value=int(get_c('market', 'seed', 42)))

    with st.expander("🚜 LP Settings (Uniswap V3)", expanded=True):
        lp_cap = st.number_input("LP Capital ($)", value=float(get_c('capital', 'lp_capital', 10000.0)))
        base_apr = st.slider("Base Pool APR (%)", 1.0, 50.0, float(get_c('lp', 'base_apr', 0.05)*100)) / 100
        range_w = st.slider("Range Width (±%)", 1.0, 50.0, float(get_c('lp', 'range_width', 0.10)*100)) / 100
        rebal_t = st.slider("Rebalance Trigger (%)", 5, 50, int(get_c('lp', 'rebalance_threshold', 0.20)*100)) / 100

    with st.expander("🛡️ Smart Hedge Guard", expanded=True):
        perp_cap = st.number_input("CEX Margin ($)", value=float(get_c('capital', 'perp_capital', 5000.0)))
        leverage = st.number_input("Leverage (x)", value=float(get_c('capital', 'leverage', 5.0)))
        hedge_mode = st.selectbox("Hedge Mode", ['always', 'smart'], index=0 if get_c('strategy', 'hedge_mode', 'always')=='always' else 1)
        hedge_t = st.slider("Hedge Adjustment Threshold (%)", 1, 30, int(get_c('strategy', 'hedge_threshold', 0.10)*100)) / 100
        exec_lag = st.select_slider("Execution Lag (Min)", options=[1, 5, 15, 30, 60], value=int(get_c('execution', 'interval_minutes', 5)))

# --- 4. Simulation Logic Hook ---
def run_simulation():
    # Setup Config Objects
    o_cfg = OracleConfig(start_price=start_price, days=days, annual_volatility=volatility, seed=seed)
    lp_cfg = LPConfig(
        initial_capital=lp_cap, 
        range_width=range_w, 
        rebalance_threshold=rebal_t, 
        base_apr=base_apr,
        gas_fee=float(get_c('costs', 'gas_fee_usd', 2.0)),
        slippage=float(get_c('costs', 'slippage', 0.001))
    )
    p_cfg = PerpConfig(leverage=leverage, taker_fee=float(get_c('costs', 'perp_taker_fee', 0.0005)))
    s_cfg = StrategyConfig(hedge_mode=hedge_mode, hedge_threshold=hedge_t)

    # Initialize Modules
    oracle = OracleModule()
    lp = LPModule(lp_cfg, start_price)
    perp = PerpModule(p_cfg)
    strategy = StrategyModule(lp, perp)
    portfolio = PortfolioModule(lp_cap + perp_cap)
    portfolio.allocate_to_lp(lp_cap)
    
    # Engine Setup
    engine = BacktestEngine(oracle, lp, perp, strategy, portfolio)
    data = oracle.generate_data(o_cfg)
    funding_rate = float(get_c('costs', 'funding_rate_8h', 0.0001))
    
    # Execute Run
    results = engine.run(
        data, s_cfg, 
        funding_rate=funding_rate, 
        execution_interval_min=exec_lag, 
        record_all_ticks=True
    )
    return results, engine, lp

# --- 5. Main Dashboard View ---
st.title("🚀 Quant Lab: LP & Smart Hedge Command Center")
st.write(f"วิเคราะห์กลยุทธ์ Delta Neutral บน Uniswap V3 (Arbitrum) และ Binance Futures")

if st.button("▶️ RUN MONTE CARLO SIMULATION", use_container_width=True):
    with st.spinner("กำลังประมวลผลอัลกอริทึมและจำลองสภาวะตลาด..."):
        res, engine, lp_final = run_simulation()
        
        # --- Metrics Calculation ---
        init_total = lp_cap + perp_cap
        final_equity = res['net_equity'].iloc[-1]
        total_withdrawn = res['total_withdrawn'].iloc[-1]
        total_wealth = final_equity + total_withdrawn
        roi_pct = ((total_wealth / init_total) - 1) * 100
        cagr = (pow(1 + (roi_pct/100), 365/days) - 1) * 100 if roi_pct > -100 else -100
        
        wealth_series = res['net_equity'] + res['total_withdrawn']
        mdd = ((wealth_series - wealth_series.cummax()) / wealth_series.cummax()).min() * 100

        # --- Metric Cards Row ---
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Wealth Created", f"${total_wealth:,.2f}", f"{roi_pct:+.2f}% ROI")
        c2.metric("Annualized CAGR", f"{cagr:.2f}%")
        c3.metric("Max Drawdown", f"{mdd:.2f}%", delta_color="inverse")
        c4.metric("Effective LP APR", f"{lp_final.config.base_apr * lp_final.multiplier * 100:.1f}%")

        # --- Chart 1: Equity Curve ---
        st.subheader("📈 Portfolio Performance Analysis")
        fig_equity = make_subplots(specs=[[{"secondary_y": True}]])
        fig_equity.add_trace(go.Scatter(x=res.index, y=res['net_equity'], name="Net Equity (Live)", line=dict(color='#6366f1', width=3)), secondary_y=False)
        fig_equity.add_trace(go.Scatter(x=res.index, y=wealth_series, name="Total Wealth (Inc. Fees)", line=dict(color='#10b981', width=2, dash='dot')), secondary_y=False)
        fig_equity.add_trace(go.Scatter(x=res.index, y=res['price'], name="ETH Price", line=dict(color='#94a3b8', width=1), opacity=0.3), secondary_y=True)
        fig_equity.update_layout(height=450, hovermode="x unified", legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
        st.plotly_chart(fig_equity, use_container_width=True)

        # --- Chart 2 & 3: Hedge Dynamics ---
        st.subheader("🛡️ Hedge Dynamics & Residual Risk")
        col_chart, col_stats = st.columns([2, 1])
        
        with col_chart:
            # Mirroring Effect (LP vs Perp)
            fig_hedge = make_subplots(specs=[[{"secondary_y": True}]])
            fig_hedge.add_trace(go.Scatter(x=res.index, y=res['lp_value'], name="LP Value (Long)", line=dict(color='#10b981', width=2)), secondary_y=False)
            perp_eq = res['cex_wallet_balance'] + res['perp_pnl']
            fig_hedge.add_trace(go.Scatter(x=res.index, y=perp_eq, name="Perp Margin (Short)", line=dict(color='#3b82f6', width=2)), secondary_y=False)
            fig_hedge.update_layout(height=350, title="Mirroring Effect: On-chain vs CEX")
            st.plotly_chart(fig_hedge, use_container_width=True)
            
            # Residual Delta (Fixed Plotly Area)
            fig_delta = go.Figure()
            fig_delta.add_trace(go.Scatter(
                x=res.index, y=res['residual_delta'], 
                name="Residual Delta (ETH Gap)", 
                fill='tozeroy', mode='lines', 
                line=dict(color='#f43f5e')
            ))
            fig_delta.update_layout(height=200, title="Residual Risk (Delta Gap)", yaxis_title="ETH Amount")
            st.plotly_chart(fig_delta, use_container_width=True)

        with col_stats:
            st.markdown("### 📋 Activity Audit")
            st.write(f"- **LP Rebalances:** {engine.lp.rebalance_count} ครั้ง")
            st.write(f"- **Hedge Trades:** {engine.hedge_count} ครั้ง")
            st.info(f"**Max Residual Delta:** {res['residual_delta'].abs().max():.4f} ETH")
            
            st.markdown("### 💰 PnL Decomposition")
            # ดึงข้อมูลจาก Ledger ของ Portfolio
            ledger = engine.portfolio.ledgers
            total_fee = ledger[TransactionType.REVENUE_LP_FEE]
            total_funding = ledger[TransactionType.REVENUE_FUNDING] + ledger[TransactionType.EXPENSE_FUNDING]
            total_cost = abs(ledger[TransactionType.EXPENSE_GAS]) + \
                         abs(ledger[TransactionType.EXPENSE_PERP_FEE]) + \
                         abs(ledger[TransactionType.EXPENSE_SLIPPAGE])
            
            st.write(f"➕ Gross LP Fees: `${total_fee:,.2f}`")
            st.write(f"➕ Net Funding: `${total_funding:,.2f}`")
            st.write(f"➖ Operational Costs: `$-{total_cost:,.2f}`")
            
            # คำนวณ IL และ Residual Impact (ผลต่างที่เหลือจากกำไรจริง)
            net_profit = total_wealth - init_total
            il_residual_impact = net_profit - (total_fee + total_funding - total_cost)
            
            st.write(f"📉 IL & Residual Impact: `${il_residual_impact:,.2f}`")
            st.divider()
            st.success(f"**Final Net Profit:** `${net_profit:,.2f}`")

        # --- Data Export ---
        st.divider()
        csv = res.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 Download Tick-by-Tick Audit CSV",
            data=csv,
            file_name=f"quant_lab_audit_{seed}_{datetime.now().strftime('%Y%m%d')}.csv",
            mime='text/csv',
            use_container_width=True
        )
else:
    # Landing Info
    st.info("💡 ปรับพารามิเตอร์ที่ Sidebar แล้วกดปุ่มด้านบนเพื่อเริ่มการ Backtest ครับ")
    st.image("https://img.freepik.com/free-vector/data-inform-chart-graphics-business-statistics-concept_53876-119102.jpg", 
             use_column_width=True, caption="Simulation Baseline v1.5.6 Master")