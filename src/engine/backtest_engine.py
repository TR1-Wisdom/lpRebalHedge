"""
src/engine/backtest_engine.py
โมดูล Backtest Engine

อัปเดต: v1.0.6 เพิ่มระบบจดบันทึก Margin Call รายละเอียดสูง และระบบถอนเงินตามเงื่อนไข PD
"""

__version__ = "1.0.6"

import pandas as pd
from typing import List, Dict, Any
from datetime import datetime, timedelta

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
        self.margin_call_events: List[Dict[str, Any]] = [] # เก็บประวัติ Margin Call
        self.withdrawal_count = 0
        self.lp_initial_capital = lp.config.initial_capital

    def run(self, data_feed: pd.DataFrame, strategy_config: StrategyConfig, 
            funding_rate: float = 0.0001, 
            harvest_config: Dict[str, Any] = None) -> pd.DataFrame:
            
        df = self.strategy.populate_indicators(data_feed, strategy_config)
        df = self.strategy.populate_signals(df)

        history: List[PortfolioState] = []
        last_funding_time: datetime = df['date'].iloc[0]
        
        # สำหรับระบบถอนเงิน
        last_withdrawal_date = df['date'].iloc[0]
        withdraw_enabled = harvest_config.get('enabled', False) if harvest_config else False
        withdraw_freq = harvest_config.get('withdrawal_freq_days', 30) if harvest_config else 30
        withdraw_target = harvest_config.get('target_amount', 0) if harvest_config else 0

        for _, row in df.iterrows():
            current_time: datetime = row['date']
            current_price: float = row['close']
            rebalanced_this_tick = False

            # --- STEP 1: Price Sync ---
            self.lp.update_price(current_price)
            self.perp.update_market_price(current_price)

            # --- STEP 2: Yield Generation ---
            lp_fee: float = self.lp.collect_fee()
            if lp_fee > 0:
                self.portfolio.record_transaction(TransactionType.REVENUE_LP_FEE, lp_fee)

            # --- STEP 3: LP Rebalance ---
            rebalance_res = self.lp.check_and_rebalance()
            if rebalance_res.is_rebalanced:
                self.portfolio.record_transaction(TransactionType.EXPENSE_GAS, -rebalance_res.gas_cost)
                self.portfolio.record_transaction(TransactionType.EXPENSE_SLIPPAGE, -rebalance_res.slippage_cost)
                rebalanced_this_tick = True

            # --- STEP 4: Strategy Execution & Margin Check ---
            if not pd.isna(row['signal']):
                orders = self.strategy.generate_orders(row, strategy_config)
                for order in orders:
                    try:
                        if order.action in ['HEDGE_ON', 'ADJUST_HEDGE']:
                            current_short = self.perp.get_short_position_size()
                            diff: float = order.target_size - current_short
                            
                            if diff > 0: # ต้องการเปิด Short เพิ่ม
                                fee = self.perp.open_position(PositionSide.SHORT, diff, self.portfolio.idle_cash)
                                self.portfolio.record_transaction(TransactionType.EXPENSE_PERP_FEE, -fee)
                                self.hedge_count += 1
                            elif diff < 0 and order.action == 'ADJUST_HEDGE':
                                size_to_close = abs(diff)
                                realized_pnl, fee = self.perp.close_partial_position(PositionSide.SHORT, size_to_close)
                                self.portfolio.record_transaction(TransactionType.DEPOSIT, realized_pnl)
                                self.portfolio.record_transaction(TransactionType.EXPENSE_PERP_FEE, -fee)
                                self.hedge_count += 1
                                
                        elif order.action == 'HEDGE_OFF':
                            self._close_all_shorts()

                    except ValueError as e:
                        if str(e) == "MARGIN_CALL":
                            # [Audit Fix 1] บันทึกรายละเอียด Margin Call แบบเจาะลึก
                            notional_needed = abs(order.target_size - self.perp.get_short_position_size()) * current_price
                            margin_needed = notional_needed / self.perp.config.leverage
                            self.margin_call_events.append({
                                'timestamp': current_time,
                                'price': current_price,
                                'available_margin': self.portfolio.idle_cash + self.perp.get_total_unrealized_pnl(),
                                'margin_needed': margin_needed
                            })

            # --- STEP 5: Withdrawal Logic (Harvesting) ---
            # เงื่อนไข: ครบเวลา + เกิด Rebalance + มีกำไรสะสม
            if withdraw_enabled and rebalanced_this_tick:
                days_since_last = (current_time - last_withdrawal_date).days
                if days_since_last >= withdraw_freq:
                    # เช็คกำไรสะสมใน LP
                    current_lp_profit = self.lp.position_value - self.lp_initial_capital
                    if current_lp_profit > 0:
                        actual_withdraw = min(withdraw_target, current_lp_profit)
                        # ดึงเงินออกจาก LP
                        self.lp.position_value -= actual_withdraw
                        # บันทึกเป็นธุรกรรมถอนออกจากพอร์ต (Passive Income)
                        self.portfolio.record_transaction(TransactionType.WITHDRAWAL, -actual_withdraw)
                        
                        self.withdrawal_count += 1
                        last_withdrawal_date = current_time

            # --- STEP 6: Funding Rate ---
            if current_time.hour % 8 == 0 and current_time != last_funding_time:
                funding_pnl: float = self.perp.apply_funding(funding_rate)
                if funding_pnl > 0:
                    self.portfolio.record_transaction(TransactionType.REVENUE_FUNDING, funding_pnl)
                elif funding_pnl < 0:
                    self.portfolio.record_transaction(TransactionType.EXPENSE_FUNDING, funding_pnl)
                last_funding_time = current_time

            # --- STEP 7: Recording ---
            state: PortfolioState = self.portfolio.get_state(current_time, self.lp.position_value, self.perp.get_total_unrealized_pnl())
            history.append(state)

        return pd.DataFrame([vars(h) for h in history])

    def _close_all_shorts(self) -> None:
        if PositionSide.SHORT in self.perp.positions:
            realized_pnl = self.perp.positions[PositionSide.SHORT].unrealized_pnl
            fee = self.perp.close_position(PositionSide.SHORT)
            self.portfolio.record_transaction(TransactionType.DEPOSIT, realized_pnl)
            self.portfolio.record_transaction(TransactionType.EXPENSE_PERP_FEE, -fee)
            self.hedge_count += 1