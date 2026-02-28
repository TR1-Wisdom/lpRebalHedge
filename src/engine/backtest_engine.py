"""
src/engine/backtest_engine.py
Engine v1.8.1: Universal Compatibility Fix
อัปเกรด: 
1. เพิ่มสวิตช์ record_all_ticks เพื่อให้ใช้ร่วมกับ UI (app.py) และ Optimizer ได้พร้อมกัน
2. คืนค่า Column ทั้งหมดที่ Dashboard v1.5.1 ต้องการใช้วาดกราฟ Dual-Charts
"""

__version__ = "1.8.1"

import pandas as pd
import numpy as np
from typing import Dict, Any, List

from src.portfolio.portfolio import TransactionType
from src.perp.perp import PositionSide

class BacktestEngine:
    def __init__(self, oracle, lp, perp, strategy, portfolio):
        self.oracle = oracle
        self.lp = lp
        self.perp = perp
        self.strategy = strategy
        self.portfolio = portfolio
        
        self.lp_rebalance_cooldown_timer = 0
        self.skew_duration_counter = 0
        self.hedge_grace_timer = 0
        
        self.hedge_count = 0
        self.cross_rebalance_count = 0
        self.withdrawal_count = 0
        self.total_swept_to_cex = 0.0
        self.total_swept_to_lp = 0.0
        self.margin_call_events: List[Dict] = []

    def run(self, data: pd.DataFrame, strat_cfg, funding_rate: float, 
            harvest_config: dict = None, cross_rebalance_config: dict = None, 
            execution_interval_min: int = 5, record_all_ticks: bool = True) -> pd.DataFrame:
        
        records = data.to_dict('records')
        history = []
        
        if harvest_config is None: harvest_config = {}
        if cross_rebalance_config is None: cross_rebalance_config = {}
        
        cross_rebal_enabled = cross_rebalance_config.get('enabled', False)
        cross_rebal_freq = cross_rebalance_config.get('freq_days', 0)
        cross_rebal_ticks = int((cross_rebal_freq * 24 * 60) / execution_interval_min) if cross_rebal_freq > 0 else 0
        ticks_per_8h = int((8 * 60) / execution_interval_min) or 1

        data = self.strategy.populate_indicators(data, strat_cfg)
        data = self.strategy.populate_signals(data)
        
        signals = data['signal'].values
        pct_changes = data['pct_change'].values
        
        lp_threshold = self.lp.config.rebalance_threshold
        lp_cooldown_setting = 8 #(เช่น 8 ticks = 40 นาที)
        lp_time_threshold = 4   #(ต้องเอียงกี่ tick ถึงจะขยับ)
        
        for i, row in enumerate(records):
            current_price = float(row['close'])
            timestamp = row['date']
            signal = int(signals[i])
            pct_change = float(pct_changes[i])
            event_str = ""
            
            self.lp.update_price(current_price)
            self.perp.update_market_price(current_price)
            
            lp_fee = self.lp.collect_fee()
            if lp_fee > 0: self.portfolio.record_transaction(TransactionType.REVENUE_LP_FEE, lp_fee)
            
            if i > 0 and i % ticks_per_8h == 0:
                funding_pnl = self.perp.apply_funding(funding_rate)
                if funding_pnl != 0:
                    cat = TransactionType.REVENUE_FUNDING if funding_pnl > 0 else TransactionType.EXPENSE_FUNDING
                    self.portfolio.record_transaction(cat, funding_pnl)

            # PHASE A: LP 
            if self.lp_rebalance_cooldown_timer > 0:
                self.lp_rebalance_cooldown_timer -= 1
            
            is_skewed = abs(self.lp.skew - 0.5) > lp_threshold
            is_out_of_range = (current_price <= self.lp.range_lower or current_price >= self.lp.range_upper)
            
            if is_skewed and self.lp_rebalance_cooldown_timer == 0:
                self.skew_duration_counter += 1
                if is_out_of_range or (self.skew_duration_counter >= lp_time_threshold):
                    rebal_res = self.lp.check_and_rebalance()
                    if rebal_res.is_rebalanced:
                        self.portfolio.record_transaction(TransactionType.EXPENSE_SLIPPAGE, -rebal_res.slippage_cost)
                        self.portfolio.record_transaction(TransactionType.EXPENSE_GAS, -rebal_res.gas_cost)
                        self.lp_rebalance_cooldown_timer = lp_cooldown_setting
                        self.skew_duration_counter = 0
                        self.hedge_grace_timer = 2 
                        event_str += "LP_REBALANCE "
            else:
                self.skew_duration_counter = 0

            # PHASE B: HEDGE
            target_mode = getattr(strat_cfg, 'hedge_mode', 'smart')
            effective_signal = -1 if target_mode == 'always' else signal
            
            if getattr(strat_cfg, 'use_safety_net', True) and abs(pct_change) >= strat_cfg.safety_net_pct:
                if effective_signal == 1 and pct_change <= -strat_cfg.safety_net_pct:
                    effective_signal = -1
                    event_str += "SAFETY_NET "

            actual_eth = self.lp.get_eth_inventory()
            current_short = self.perp.get_short_position_size()
            target_short = actual_eth if effective_signal == -1 else 0.0
            
            diff = abs(target_short - current_short)
            needs_adjust = False
            
            if (target_short == 0 and current_short > 0) or (target_short > 0 and current_short == 0):
                needs_adjust = True
            elif target_short > 0 and (diff / target_short) > strat_cfg.hedge_threshold:
                if diff > 0.0001: 
                    needs_adjust = True
                
            if needs_adjust:
                try:
                    if self.hedge_grace_timer > 0:
                        target_short = current_short + ((target_short - current_short) * 0.5)
                    
                    if target_short > current_short:
                        fee = self.perp.open_position(PositionSide.SHORT, target_short - current_short, self.portfolio.cex_wallet_balance)
                        self.portfolio.record_transaction(TransactionType.EXPENSE_PERP_FEE, -fee)
                    else:
                        pnl, fee = self.perp.close_partial_position(PositionSide.SHORT, current_short - target_short)
                        self.portfolio.record_transaction(TransactionType.EXPENSE_PERP_FEE, -fee)
                        self.portfolio.record_transaction(TransactionType.DEPOSIT, pnl)
                    
                    self.hedge_count += 1
                    event_str += "ADJUST_HEDGE "
                except ValueError as e:
                    if str(e) == "MARGIN_CALL":
                        self._handle_margin_call(timestamp, current_price, target_short)
                        event_str += "MARGIN_CALL_REJECT "
            
            if self.hedge_grace_timer > 0: self.hedge_grace_timer -= 1

            # PHASE C: RESCUE & SWEEP
            margin_used = self.perp.get_total_margin_used()
            available_margin = self.portfolio.cex_wallet_balance + self.perp.get_total_unrealized_pnl() - margin_used
            
            if available_margin < 0:
                rescue = min(abs(available_margin) * 1.5, self.lp.accumulated_fees * 0.8)
                if rescue > 0:
                    self.lp.accumulated_fees -= rescue
                    self.portfolio.record_transaction(TransactionType.SWEEP_TO_CEX, rescue)
                    event_str += "EMERGENCY_RESCUE "
            
            if cross_rebal_enabled and i > 0 and i % cross_rebal_ticks == 0:
                self._execute_cross_margin_sweep()
                event_str += "CROSS_SWEEP "

            # PHASE D: RECORD (Universal Fix)
            is_event = event_str != ""
            is_last = i == len(records) - 1
            
            # [FIX] คืนค่า Columns ครบถ้วน เพื่อให้ app.py ใช้วาดกราฟได้
            if record_all_ticks or is_event or is_last:
                margin_used_final = self.perp.get_total_margin_used()
                p_state = self.portfolio.get_state(timestamp, self.lp.position_value, self.perp.get_total_unrealized_pnl(), margin_used_final)
                
                history.append({
                    'timestamp': p_state.timestamp,
                    'price': current_price,
                    'lp_eth': self.lp.get_eth_inventory(),
                    'perp_size': self.perp.get_short_position_size(),
                    'residual_delta': self.lp.get_eth_inventory() - self.perp.get_short_position_size(),
                    'lp_value': p_state.lp_value,
                    'perp_pnl': p_state.perp_pnl,
                    'net_equity': p_state.net_equity,
                    'cex_wallet_balance': p_state.cex_wallet_balance,
                    'cex_available_margin': p_state.cex_available_margin,
                    'total_fees_collected': p_state.total_fees_collected,
                    'total_costs': p_state.total_costs,
                    'total_withdrawn': p_state.total_withdrawn,
                    'event': event_str.strip()
                })

        return pd.DataFrame(history)

    def _handle_margin_call(self, timestamp, price, target_size):
        margin_used = self.perp.get_total_margin_used()
        avail = self.portfolio.cex_wallet_balance + self.perp.get_total_unrealized_pnl() - margin_used
        self.margin_call_events.append({'timestamp': timestamp, 'price': price, 'target_size': target_size, 'available_margin': avail})
        
    def _execute_cross_margin_sweep(self):
        lp_val, perp_cash = self.lp.position_value, self.portfolio.cex_wallet_balance
        total = lp_val + perp_cash
        if total <= 0: return
        target_lp_val = total * (self.portfolio.lp_allocated_cash / self.portfolio.initial_capital)
        diff = lp_val - target_lp_val
        if abs(diff) > total * 0.05:
            sweep = diff * 0.5
            if sweep > 0: 
                self.lp.position_value -= sweep
                self.portfolio.record_transaction(TransactionType.SWEEP_TO_CEX, sweep)
            else: 
                sweep = abs(sweep)
                if self.portfolio.cex_wallet_balance > sweep:
                    self.portfolio.record_transaction(TransactionType.SWEEP_TO_LP, -sweep)
                    self.lp.position_value += sweep
            self.cross_rebalance_count += 1