"""
src/engine/backtest_engine.py
อัปเดต: v1.4.2 (Inventory Tracking Edition)
- เพิ่มการบันทึก perp_size และ lp_eth ลงใน History เพื่อวิเคราะห์ Delta Neutral
- รักษาระบบ Event Logging, Emergency Rescue และ Cross-Margin Sweep ครบถ้วน
"""

__version__ = "1.4.2"

import pandas as pd
from typing import List, Dict, Any
from datetime import datetime

from src.oracle.oracle import OracleModule
from src.lp.lp import LPModule
from src.perp.perp import PerpModule, PositionSide
from src.strategy.strategy import StrategyModule, StrategyConfig
from src.portfolio.portfolio import PortfolioModule, TransactionType, PortfolioState


class BacktestEngine:
    def __init__(
        self,
        oracle: OracleModule,
        lp: LPModule,
        perp: PerpModule,
        strategy: StrategyModule,
        portfolio: PortfolioModule
    ) -> None:
        self.oracle = oracle
        self.lp = lp
        self.perp = perp
        self.strategy = strategy
        self.portfolio = portfolio
        self.hedge_count = 0 
        self.margin_call_events: List[Dict[str, Any]] = [] 
        self.withdrawal_count = 0
        self.lp_initial_capital = lp.config.initial_capital
        
        self.cross_rebalance_count = 0
        self.total_swept_to_cex = 0.0
        self.total_swept_to_lp = 0.0

    def run(self, data_feed: pd.DataFrame, strategy_config: StrategyConfig, 
            funding_rate: float = 0.0001, 
            harvest_config: Dict[str, Any] = None,
            cross_rebalance_config: Dict[str, Any] = None,
            execution_interval_min: int = 1) -> pd.DataFrame:
            
        df = self.strategy.populate_indicators(data_feed, strategy_config)
        df = self.strategy.populate_signals(df)

        history: List[Dict[str, Any]] = []
        last_execution_time: datetime = datetime(1970, 1, 1) 
        last_funding_time: datetime = df['date'].iloc[0]
        last_cap_rebal_date = df['date'].iloc[0]
        last_withdrawal_date = df['date'].iloc[0]
        
        withdraw_enabled = str(harvest_config.get('enabled', 'false')).lower() == 'true' if harvest_config else False
        withdraw_freq = harvest_config.get('withdrawal_freq_days', 30) if harvest_config else 30
        withdraw_target = harvest_config.get('target_amount', 0) if harvest_config else 0
        
        cap_rebal_enabled = str(cross_rebalance_config.get('enabled', 'true')).lower() == 'true' if cross_rebalance_config else False
        cap_rebal_freq = cross_rebalance_config.get('freq_days', 30) if cross_rebalance_config else 30

        for _, row in df.iterrows():
            current_time: datetime = row['date']
            current_price: float = row['close']
            current_events: List[str] = []

            # 1. Mark-to-Market
            self.lp.update_price(current_price)
            self.perp.update_market_price(current_price)

            # 2. Collect LP Fees
            lp_fee: float = self.lp.collect_fee()
            if lp_fee > 0:
                self.portfolio.record_transaction(TransactionType.REVENUE_LP_FEE, lp_fee)

            # 3. Execution Logic (Lag)
            can_execute = (current_time - last_execution_time).total_seconds() >= (execution_interval_min * 60)

            if can_execute:
                # On-chain LP Rebalance
                rebalance_res = self.lp.check_and_rebalance()
                if rebalance_res.is_rebalanced:
                    self.portfolio.record_transaction(TransactionType.EXPENSE_GAS, -rebalance_res.gas_cost)
                    self.portfolio.record_transaction(TransactionType.EXPENSE_SLIPPAGE, -rebalance_res.slippage_cost)
                    current_events.append("LP_REBALANCE")

                # Off-chain Strategy & Hedge
                if not pd.isna(row['signal']):
                    orders = self.strategy.generate_orders(row, strategy_config)
                    for order in orders:
                        try:
                            self._execute_perp_order(order, current_price, current_events)
                        except ValueError as e:
                            if str(e) == "MARGIN_CALL":
                                deficit = self._attempt_rescue(order, current_price)
                                if deficit > 0:
                                    current_events.append("EMERGENCY_RESCUE")
                                    try:
                                        self._execute_perp_order(order, current_price, current_events)
                                    except ValueError:
                                        current_events.append("MARGIN_CALL_REJECT")
                                        self.margin_call_events.append({'timestamp': current_time, 'price': current_price})
                                else:
                                    current_events.append("MARGIN_CALL_REJECT")
                                    self.margin_call_events.append({'timestamp': current_time, 'price': current_price})

                last_execution_time = current_time 
                
            # 4. Cross-Margin Sweep
            if cap_rebal_enabled:
                if (current_time - last_cap_rebal_date).total_seconds() >= (cap_rebal_freq * 86400):
                    self._perform_capital_sweep(current_events)
                    last_cap_rebal_date = current_time

            # 5. Passive Income
            if withdraw_enabled:
                days_since_last = (current_time - last_withdrawal_date).days
                if days_since_last >= withdraw_freq:
                    current_lp_profit = self.lp.position_value - self.lp_initial_capital
                    if current_lp_profit > 0:
                        actual_withdraw = min(withdraw_target, current_lp_profit)
                        self.lp.position_value -= actual_withdraw
                        self.portfolio.record_transaction(TransactionType.WITHDRAWAL, -actual_withdraw)
                        self.withdrawal_count += 1
                        current_events.append(f"PASSIVE_WITHDRAWAL(${actual_withdraw:.0f})")
                        last_withdrawal_date = current_time

            # 6. Funding Rate
            if (current_time - last_funding_time).total_seconds() >= 8 * 3600:
                funding_pnl: float = self.perp.apply_funding(funding_rate)
                if funding_pnl != 0:
                    type_fund = TransactionType.REVENUE_FUNDING if funding_pnl > 0 else TransactionType.EXPENSE_FUNDING
                    self.portfolio.record_transaction(type_fund, funding_pnl)
                    current_events.append("FUNDING_PAYMENT")
                last_funding_time = current_time

            # 7. Record Full State
            state: PortfolioState = self.portfolio.get_state(
                current_time, 
                self.lp.position_value, 
                self.perp.get_total_unrealized_pnl(),
                self.perp.get_total_margin_used() 
            )
            state_dict = vars(state)
            state_dict['event'] = "|".join(current_events) if current_events else ""
            state_dict['price'] = current_price
            # [NEW] บันทึกขนาด Inventory ทั้งสองฝั่ง
            state_dict['perp_size'] = self.perp.get_short_position_size() # ปริมาณที่ Short อยู่ (ETH)
            state_dict['lp_eth'] = self.lp.get_eth_inventory()           # ปริมาณที่มีใน LP (ETH)
            history.append(state_dict)

        return pd.DataFrame(history)

    def _execute_perp_order(self, order, price, event_list):
        if order.action in ['HEDGE_ON', 'ADJUST_HEDGE']:
            current_short = self.perp.get_short_position_size()
            diff: float = order.target_size - current_short
            if diff > 0: 
                fee = self.perp.open_position(PositionSide.SHORT, diff, self.portfolio.cex_wallet_balance)
                self.portfolio.record_transaction(TransactionType.EXPENSE_PERP_FEE, -fee)
                self.hedge_count += 1
                event_list.append(f"HEDGE_INC({order.reason})")
            elif diff < 0 and order.action == 'ADJUST_HEDGE':
                size_to_close = abs(diff)
                realized_pnl, fee = self.perp.close_partial_position(PositionSide.SHORT, size_to_close)
                self.portfolio.record_transaction(TransactionType.DEPOSIT, realized_pnl)
                self.portfolio.record_transaction(TransactionType.EXPENSE_PERP_FEE, -fee)
                self.hedge_count += 1
                event_list.append(f"HEDGE_DEC({order.reason})")
        elif order.action == 'HEDGE_OFF':
            if PositionSide.SHORT in self.perp.positions:
                realized_pnl = self.perp.positions[PositionSide.SHORT].unrealized_pnl
                fee = self.perp.close_position(PositionSide.SHORT)
                self.portfolio.record_transaction(TransactionType.DEPOSIT, realized_pnl)
                self.portfolio.record_transaction(TransactionType.EXPENSE_PERP_FEE, -fee)
                self.hedge_count += 1
                event_list.append("HEDGE_OFF")

    def _attempt_rescue(self, order, price) -> float:
        current_short_size = self.perp.get_short_position_size()
        target_diff = abs(order.target_size - current_short_size)
        notional_needed = target_diff * price
        trading_fee = notional_needed * self.perp.config.taker_fee
        margin_needed = (notional_needed / self.perp.config.leverage) + trading_fee
        available_margin = self.portfolio.cex_wallet_balance + self.perp.get_total_unrealized_pnl() - self.perp.get_total_margin_used()
        deficit = (margin_needed - available_margin) + 20.0 
        current_lp_profit = self.lp.position_value - self.lp_initial_capital
        if current_lp_profit > deficit and deficit > 0:
            self.lp.position_value -= deficit
            self.portfolio.record_transaction(TransactionType.WITHDRAWAL, -deficit)
            self.portfolio.record_transaction(TransactionType.DEPOSIT, deficit)
            return deficit
        return 0.0

    def _perform_capital_sweep(self, event_list):
        current_lp = self.lp.position_value
        current_perp_pnl = self.perp.get_total_unrealized_pnl()
        current_cex = self.portfolio.cex_wallet_balance + current_perp_pnl
        total_eq = current_lp + current_cex
        initial_lp = self.lp_initial_capital
        initial_cex = self.portfolio.initial_capital - self.lp_initial_capital
        initial_total = initial_lp + initial_cex
        target_lp = total_eq * (initial_lp / initial_total)
        transfer_to_cex = current_lp - target_lp
        if transfer_to_cex > 0:
            self.lp.position_value -= transfer_to_cex
            self.portfolio.cex_wallet_balance += transfer_to_cex
            self.total_swept_to_cex += transfer_to_cex
            self.cross_rebalance_count += 1
            event_list.append(f"SWEEP_TO_CEX(${transfer_to_cex:.0f})")
        elif transfer_to_cex < 0:
            amt_to_move = abs(transfer_to_cex)
            available_in_cex = self.portfolio.cex_wallet_balance + current_perp_pnl - self.perp.get_total_margin_used()
            actual_transfer = min(amt_to_move, available_in_cex)
            if actual_transfer > 10.0:
                self.portfolio.cex_wallet_balance -= actual_transfer
                self.lp.position_value += actual_transfer
                self.total_swept_to_lp += actual_transfer
                self.cross_rebalance_count += 1
                event_list.append(f"SWEEP_TO_LP(${actual_transfer:.0f})")