"""
src/engine/backtest_engine.py
โมดูล Backtest Engine (The Orchestrator) สำหรับโปรเจกต์ Inventory LP Backtester

ประวัติการแก้ไข:
- v1.0.3 (2026-02-20): [PM FIXED] อัปเดต Logic การทำ ADJUST_HEDGE ขาลง ให้ใช้ close_partial_position ประหยัดค่า Fee
"""

__version__ = "1.0.3"

import pandas as pd
from typing import List
from datetime import datetime

from src.oracle.oracle import OracleModule
from src.lp.lp import LPModule
from src.perp.perp import PerpModule, PositionSide
from src.strategy.strategy import StrategyModule, StrategyConfig
from src.portfolio.portfolio import PortfolioModule, TransactionType, PortfolioState


class BacktestEngine:
    """คลาสศูนย์กลางในการรัน Backtest แบบ Event-driven"""

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

    def run(self, data_feed: pd.DataFrame, strategy_config: StrategyConfig, funding_rate: float = 0.0001) -> pd.DataFrame:
        df = self.strategy.populate_indicators(data_feed, strategy_config)
        df = self.strategy.populate_signals(df)

        history: List[PortfolioState] = []
        last_funding_time: datetime = df['date'].iloc[0]

        for _, row in df.iterrows():
            current_time: datetime = row['date']
            current_price: float = row['close']

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
                self.portfolio.record_transaction(TransactionType.EXPENSE_TRADING_FEE, -rebalance_res.slippage_cost)

            # --- STEP 4: Strategy Execution ---
            if not pd.isna(row['signal']):
                orders = self.strategy.generate_orders(row, strategy_config)
                
                for order in orders:
                    if order.action == 'HEDGE_ON':
                        diff: float = order.target_size - self.perp.get_short_position_size()
                        if diff > 0:
                            fee = self.perp.open_position(PositionSide.SHORT, diff)
                            self.portfolio.record_transaction(TransactionType.EXPENSE_TRADING_FEE, -fee)
                            
                    elif order.action == 'HEDGE_OFF':
                        self._close_all_shorts()

                    elif order.action == 'ADJUST_HEDGE':
                        diff: float = order.target_size - self.perp.get_short_position_size()
                        if diff > 0:
                            # ขาดของ -> เปิด Short เพิ่ม (Perp v1.0.2 จะทำการถัวเฉลี่ยให้เอง)
                            fee = self.perp.open_position(PositionSide.SHORT, diff)
                            self.portfolio.record_transaction(TransactionType.EXPENSE_TRADING_FEE, -fee)
                        elif diff < 0:
                            # ของเกิน -> [FIXED] ใช้ระบบปิดบางส่วน (Partial Close)
                            size_to_close = abs(diff)
                            realized_pnl, fee = self.perp.close_partial_position(PositionSide.SHORT, size_to_close)
                            self.portfolio.record_transaction(TransactionType.DEPOSIT, realized_pnl)
                            self.portfolio.record_transaction(TransactionType.EXPENSE_TRADING_FEE, -fee)

            # --- STEP 5: Funding Rate ---
            if current_time.hour % 8 == 0 and current_time != last_funding_time:
                funding_pnl: float = self.perp.apply_funding(funding_rate)
                if funding_pnl > 0:
                    self.portfolio.record_transaction(TransactionType.REVENUE_FUNDING, funding_pnl)
                elif funding_pnl < 0:
                    self.portfolio.record_transaction(TransactionType.EXPENSE_FUNDING, funding_pnl)
                last_funding_time = current_time

            # --- STEP 6: Recording ---
            lp_val: float = self.lp.position_value
            perp_pnl: float = self.perp.get_total_unrealized_pnl()
            
            state: PortfolioState = self.portfolio.get_state(current_time, lp_val, perp_pnl)
            history.append(state)

        return pd.DataFrame([vars(h) for h in history])

    def _close_all_shorts(self) -> None:
        """Helper สำหรับปิดสถานะ Short ทั้งหมดและบันทึก PnL ลงบัญชี"""
        if PositionSide.SHORT in self.perp.positions:
            realized_pnl = self.perp.positions[PositionSide.SHORT].unrealized_pnl
            fee = self.perp.close_position(PositionSide.SHORT)
            self.portfolio.record_transaction(TransactionType.DEPOSIT, realized_pnl)
            self.portfolio.record_transaction(TransactionType.EXPENSE_TRADING_FEE, -fee)