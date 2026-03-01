"""
src/engine/backtest_engine.py
Engine v1.8.5: Master Production Edition
- รวม Fixes ทั้งหมด: Fee Scaling, Realized PnL, Schema Alignment
- กู้คืนฟีเจอร์: Cross-Margin, Emergency Rescue, Harvesting, Detailed Event Logging
"""

__version__ = "1.8.5"

import pandas as pd
import numpy as np
from typing import Dict, Any, List, Optional
from src.portfolio.portfolio import TransactionType
from src.perp.perp import PositionSide

class BacktestEngine:
    def __init__(self, oracle, lp, perp, strategy, portfolio):
        self.oracle = oracle
        self.lp = lp
        self.perp = perp
        self.strategy = strategy
        self.portfolio = portfolio
        
        # คุมสถานะตัวนับภายใน (Internal State Counters)
        self.lp_rebalance_cooldown_timer = 0
        self.skew_duration_counter = 0
        self.hedge_grace_timer = 0
        
        # ตัวชี้วัดสถิติและการ Audit
        self.hedge_count = 0
        self.cross_rebalance_count = 0
        self.withdrawal_count = 0
        self.total_swept_to_cex = 0.0
        self.total_swept_to_lp = 0.0
        self.margin_call_events: List[Dict] = []
        self.total_fees_collected = 0.0

    def run(self, data: pd.DataFrame, strat_cfg, funding_rate: float, 
            harvest_config: Optional[Dict] = None, 
            cross_rebalance_config: Optional[Dict] = None, 
            execution_interval_min: int = 5, 
            record_all_ticks: bool = True) -> pd.DataFrame:
        """
        รันการจำลอง Backtest แบบ Full Feature ระดับ Production
        """
        records = data.to_dict('records')
        history = []
        
        # [FIX] การคำนวณจำนวน Tick ใน 1 ปี เพื่อใช้ทอนค่าธรรมเนียม
        ticks_per_year = (365 * 24 * 60) / execution_interval_min
        
        # จัดการ Config เสริม
        h_cfg = harvest_config or {}
        cr_cfg = cross_rebalance_config or {}
        
        ticks_per_8h = int((8 * 60) / execution_interval_min) or 1
        cross_rebal_ticks = int((cr_cfg.get('freq_days', 15) * 24 * 60) / execution_interval_min)

        # เตรียมสัญญาณเทคนิค (Strategy Indicators)
        data = self.strategy.populate_indicators(data, strat_cfg)
        data = self.strategy.populate_signals(data)
        signals = data['signal'].values
        
        lp_threshold = self.lp.config.rebalance_threshold
        
        for i, row in enumerate(records):
            current_price = float(row['close'])
            timestamp = row['date']
            signal = int(signals[i])
            event_str = ""
            
            # --- 1. MTM & INCOME UPDATE ---
            self.lp.update_price(current_price)
            self.perp.update_market_price(current_price)
            
            # [FIXED] คิดรายได้ LP Fee แบบ Scaled (ป้องกัน Exponential Error)
            fee_tick = self.lp.get_annual_fee_rate() / ticks_per_year
            if fee_tick > 0:
                self.portfolio.record_transaction(TransactionType.REVENUE_LP_FEE, fee_tick)
                self.total_fees_collected += fee_tick
                self.lp.accumulated_fees += fee_tick # สำรองไว้ในโมดูลเพื่อ Rescue

            # คิดค่า Funding ราย 8 ชั่วโมง
            if i > 0 and i % ticks_per_8h == 0:
                funding_pnl = self.perp.apply_funding(funding_rate)
                if funding_pnl != 0:
                    cat = TransactionType.REVENUE_FUNDING if funding_pnl > 0 else TransactionType.EXPENSE_FUNDING
                    self.portfolio.record_transaction(cat, funding_pnl)

            # --- 2. PHASE A: LP REBALANCE ---
            if self.lp_rebalance_cooldown_timer > 0:
                self.lp_rebalance_cooldown_timer -= 1
            
            is_skewed = abs(self.lp.skew - 0.5) > lp_threshold
            is_out_of_range = (current_price <= self.lp.range_lower or current_price >= self.lp.range_upper)
            
            if is_skewed and self.lp_rebalance_cooldown_timer == 0:
                self.skew_duration_counter += 1
                if is_out_of_range or (self.skew_duration_counter >= 3):
                    res = self.lp.check_and_rebalance()
                    if res.is_rebalanced:
                        self.portfolio.record_transaction(TransactionType.EXPENSE_SLIPPAGE, -res.slippage_cost)
                        self.portfolio.record_transaction(TransactionType.EXPENSE_GAS, -res.gas_cost)
                        self.lp_rebalance_cooldown_timer = 8
                        self.skew_duration_counter = 0
                        self.hedge_grace_timer = 2
                        event_str += "LP_REBALANCE "
            else:
                self.skew_duration_counter = 0

            # --- 3. PHASE B: SMART HEDGE ---
            target_mode = getattr(strat_cfg, 'hedge_mode', 'always')
            effective_signal = -1 if target_mode == 'always' else signal
            
            actual_eth = self.lp.get_eth_inventory()
            current_short = self.perp.get_short_position_size()
            target_short = actual_eth if effective_signal == -1 else 0.0
            
            diff = abs(target_short - current_short)
            if (target_short == 0 and current_short > 0) or (target_short > 0 and current_short == 0) or \
               (target_short > 0 and (diff / target_short) > strat_cfg.hedge_threshold and diff > 0.0001):
                
                if self.hedge_grace_timer == 0:
                    try:
                        if target_short > current_short:
                            # เปิดเพิ่ม
                            fee = self.perp.open_position(PositionSide.SHORT, target_short - current_short, self.portfolio.cex_wallet_balance)
                            self.portfolio.record_transaction(TransactionType.EXPENSE_PERP_FEE, -fee)
                        else:
                            # [FIXED] ลดขนาด: นำ Realized PnL เข้า Wallet (ป้องกัน PnL Leakage)
                            pnl, fee = self.perp.close_partial_position(PositionSide.SHORT, current_short - target_short)
                            self.portfolio.record_transaction(TransactionType.EXPENSE_PERP_FEE, -fee)
                            self.portfolio.record_transaction(TransactionType.DEPOSIT, pnl)
                        
                        self.hedge_count += 1
                        event_str += "ADJUST_HEDGE "
                    except ValueError:
                        event_str += "MARGIN_CALL_REJECT "
            
            if self.hedge_grace_timer > 0: self.hedge_grace_timer -= 1

            # --- 4. PHASE C: CAPITAL MANAGEMENT (RESCUE, SWEEP, HARVEST) ---
            m_used = self.perp.get_total_margin_used()
            u_pnl = self.perp.get_total_unrealized_pnl()
            available_margin = self.portfolio.cex_wallet_balance + u_pnl - m_used
            
            # A. Emergency Rescue (ดึงกำไร LP ช่วย CEX)
            if available_margin < 0:
                rescue = min(abs(available_margin) * 1.2, self.lp.accumulated_fees)
                if rescue > 0:
                    self.lp.accumulated_fees -= rescue
                    self.portfolio.record_transaction(TransactionType.SWEEP_TO_CEX, rescue)
                    event_str += "EMERGENCY_RESCUE "

            # B. Cross-Margin Sweep (รักษาสมดุลทุนตามรอบ)
            if cr_cfg.get('enabled', False) and i > 0 and i % cross_rebal_ticks == 0:
                target_ratio = cr_cfg.get('target_cex_ratio', 0.3) # เช่น ให้ CEX ถือ 30% ของพอร์ต
                total_val = self.portfolio.get_net_equity(self.lp.position_value, u_pnl)
                ideal_cex = total_val * target_ratio
                
                sweep_amount = ideal_cex - self.portfolio.cex_wallet_balance
                if sweep_amount > 0: # ดึงจาก LP เข้า CEX
                    can_pull = min(sweep_amount, self.lp.accumulated_fees)
                    self.lp.accumulated_fees -= can_pull
                    self.portfolio.record_transaction(TransactionType.SWEEP_TO_CEX, can_pull)
                    self.total_swept_to_cex += can_pull
                else: # โยกจาก CEX เข้า LP (Compounding)
                    to_lp = abs(sweep_amount)
                    self.portfolio.record_transaction(TransactionType.SWEEP_TO_LP, -to_lp)
                    self.lp.position_value += to_lp
                    self.total_swept_to_lp += to_lp
                event_str += "CROSS_SWEEP "

            # C. Profit Harvesting (ถอนกำไรออกไปกินใช้)
            if h_cfg.get('enabled', False) and i > 0 and i % int((h_cfg.get('withdrawal_freq_days', 30)*1440)/execution_interval_min) == 0:
                withdraw = h_cfg.get('target_amount', 0)
                if self.lp.accumulated_fees >= withdraw:
                    self.lp.accumulated_fees -= withdraw
                    self.portfolio.record_transaction(TransactionType.WITHDRAWAL, -withdraw)
                    self.withdrawal_count += 1
                    event_str += "HARVEST_PROFIT "

            # --- 5. PHASE D: RECORDING ---
            if record_all_ticks or event_str != "" or i == len(records)-1:
                p_state = self.portfolio.get_state(timestamp, self.lp.position_value, u_pnl, m_used)
                history.append({
                    'timestamp': p_state.timestamp,
                    'price': current_price,
                    'lp_eth': actual_eth,
                    'perp_size': current_short,
                    'residual_delta': actual_eth - current_short,
                    'lp_value': p_state.lp_value,
                    'perp_pnl': p_state.perp_pnl,
                    'net_equity': p_state.net_equity,
                    'cex_wallet_balance': p_state.cex_wallet_balance,
                    'cex_available_margin': p_state.cex_available_margin,
                    'total_withdrawn': p_state.total_withdrawn, # [FIXED] กัน KeyError
                    'event': event_str.strip()
                })

        return pd.DataFrame(history)