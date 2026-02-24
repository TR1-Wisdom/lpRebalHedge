"""
src/engine/backtest_engine.py
อัปเดต: v1.4.3 (Emergency Fix - Anti-Regression Edition)
- แก้ไข Logic การคำนวณ Margin Snapshot ให้รวม Unrealized PnL (Fix ROI -1400% Bug)
- เพิ่มระบบ Emergency Margin Rescue แบบจำกัดวงเงิน (ป้องกันการสูบเงินจาก LP จนพัง)
- รักษาระบบ Event Logging, Inventory Tracking และ Cross-Margin Sweep ไว้ครบ 100%
"""

__version__ = "1.4.3"

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
        
        # Counters & Stats
        self.hedge_count = 0 
        self.margin_call_events: List[Dict[str, Any]] = [] 
        self.withdrawal_count = 0
        self.lp_initial_capital = lp.config.initial_capital
        
        # Cross-Margin Stats
        self.cross_rebalance_count = 0
        self.total_swept_to_cex = 0.0
        self.total_swept_to_lp = 0.0

    def get_margin_snapshot(self) -> Dict[str, float]:
        """
        [FIXED] คำนวณสถานะ Margin ที่แท้จริง (หัวใจสำคัญที่ Sr. Programmer ทำพลาด)
        ต้องรวม Unrealized PnL เข้าไปในอำนาจการซื้อ (Buying Power) เสมอ
        """
        unrealized_pnl = self.perp.get_total_unrealized_pnl()
        margin_used = self.perp.get_total_margin_used()
        wallet_balance = self.portfolio.cex_wallet_balance
        
        # Available Margin = เงินสดในมือ + กำไร(หรือขาดทุน)ที่ยังไม่รับรู้ - เงินค้ำประกันที่โดนล็อก
        available = wallet_balance + unrealized_pnl - margin_used
        
        return {
            "available": available,
            "wallet": wallet_balance,
            "pnl": unrealized_pnl,
            "used": margin_used
        }

    def run(self, data_feed: pd.DataFrame, strategy_config: StrategyConfig, 
            funding_rate: float = 0.0001, 
            harvest_config: Dict[str, Any] = None,
            cross_rebalance_config: Dict[str, Any] = None,
            execution_interval_min: int = 1) -> pd.DataFrame:
            
        # 0. เตรียมอินดิเคเตอร์และสัญญาณ
        df = self.strategy.populate_indicators(data_feed, strategy_config)
        df = self.strategy.populate_signals(df)

        history: List[Dict[str, Any]] = []
        last_execution_time: datetime = datetime(1970, 1, 1) 
        last_funding_time: datetime = df['date'].iloc[0]
        last_cap_rebal_date = df['date'].iloc[0]
        last_withdrawal_date = df['date'].iloc[0]
        
        # Parsing Configs
        withdraw_enabled = str(harvest_config.get('enabled', 'false')).lower() == 'true' if harvest_config else False
        withdraw_freq = harvest_config.get('withdrawal_freq_days', 30) if harvest_config else 30
        withdraw_target = harvest_config.get('target_amount', 0) if harvest_config else 0
        
        cap_rebal_enabled = str(cross_rebalance_config.get('enabled', 'true')).lower() == 'true' if cross_rebalance_config else False
        cap_rebal_freq = cross_rebalance_config.get('freq_days', 30) if cross_rebalance_config else 30

        for _, row in df.iterrows():
            current_time: datetime = row['date']
            current_price: float = row['close']
            current_events: List[str] = []

            # 1. Mark-to-Market (อัปเดตมูลค่าพอร์ตตามราคาปัจจุบัน)
            self.lp.update_price(current_price)
            self.perp.update_market_price(current_price)

            # 2. เก็บ Fee (หัวใจของ ROI - เก็บทุก Tick)
            lp_fee: float = self.lp.collect_fee()
            if lp_fee > 0:
                self.portfolio.record_transaction(TransactionType.REVENUE_LP_FEE, lp_fee)

            # 3. Execution Block (ตรวจสอบความหน่วงเครือข่าย)
            can_execute = (current_time - last_execution_time).total_seconds() >= (execution_interval_min * 60)

            if can_execute:
                # 3.1 Check On-chain Rebalance (LP)
                rebalance_res = self.lp.check_and_rebalance()
                if rebalance_res.is_rebalanced:
                    self.portfolio.record_transaction(TransactionType.EXPENSE_GAS, -rebalance_res.gas_cost)
                    self.portfolio.record_transaction(TransactionType.EXPENSE_SLIPPAGE, -rebalance_res.slippage_cost)
                    current_events.append("LP_REBALANCE")

                # 3.2 Check Off-chain Hedge (Perp)
                if not pd.isna(row['signal']):
                    orders = self.strategy.generate_orders(row, strategy_config)
                    for order in orders:
                        try:
                            self._execute_perp_order(order, current_price, current_events)
                        except ValueError as e:
                            if str(e) == "MARGIN_CALL":
                                # พยายามกู้ชีพโดยดึงกำไรจาก LP
                                deficit_filled = self._attempt_rescue(order, current_price)
                                if deficit_filled > 0:
                                    current_events.append(f"EMERGENCY_RESCUE(${deficit_filled:.0f})")
                                    # ลองสั่งใหม่หลังเติมเงินแล้ว
                                    try:
                                        self._execute_perp_order(order, current_price, current_events)
                                    except ValueError:
                                        self._log_margin_failure(current_time, current_price, order, current_events)
                                else:
                                    self._log_margin_failure(current_time, current_price, order, current_events)

                last_execution_time = current_time 
                
            # 4. Cross-Margin Sweep (รักษาสมดุลทุนระหว่าง LP และ CEX ทุกๆ X วัน)
            if cap_rebal_enabled:
                if (current_time - last_cap_rebal_date).total_seconds() >= (cap_rebal_freq * 86400):
                    self._perform_capital_sweep(current_events)
                    last_cap_rebal_date = current_time

            # 5. Passive Income Withdrawal
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

            # 6. Apply Funding Rate (ทุก 8 ชั่วโมง)
            if (current_time - last_funding_time).total_seconds() >= 8 * 3600:
                funding_pnl: float = self.perp.apply_funding(funding_rate)
                if funding_pnl != 0:
                    type_fund = TransactionType.REVENUE_FUNDING if funding_pnl > 0 else TransactionType.EXPENSE_FUNDING
                    self.portfolio.record_transaction(type_fund, funding_pnl)
                    current_events.append("FUNDING_PAYMENT")
                last_funding_time = current_time

            # 7. บันทึกสถานะ State ลงใน History
            snap = self.get_margin_snapshot()
            state: PortfolioState = self.portfolio.get_state(
                current_time, 
                self.lp.position_value, 
                snap["pnl"], 
                snap["used"]
            )
            state_dict = vars(state)
            state_dict['event'] = "|".join(current_events) if current_events else ""
            state_dict['price'] = current_price
            state_dict['perp_size'] = self.perp.get_short_position_size()
            state_dict['lp_eth'] = self.lp.get_eth_inventory()
            history.append(state_dict)

        return pd.DataFrame(history)

    def _log_margin_failure(self, time, price, order, events):
        """บันทึกข้อมูล Audit Log เมื่อไม่สามารถเปิด Hedge ได้เนื่องจาก Margin ไม่พอ"""
        snap = self.get_margin_snapshot()
        current_size = self.perp.get_short_position_size()
        target_diff = abs(order.target_size - current_size)
        notional_needed = target_diff * price
        margin_needed = (notional_needed / self.perp.config.leverage) + (notional_needed * self.perp.config.taker_fee)
        
        self.margin_call_events.append({
            'timestamp': time,
            'price': price,
            'available_margin': snap["available"],
            'margin_needed': margin_needed
        })
        events.append("MARGIN_CALL_REJECT")

    def _execute_perp_order(self, order, price, event_list):
        """Helper สำหรับการจัดการธุรกรรมบน CEX"""
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
        """
        [FIXED] กู้ชีพเฉพาะเมื่อมีกำไรสะสมใน LP และป้องกันการถอนจนติดลบ
        เพื่อป้องกัน Infinite Rescue Loop ที่ทำให้พอร์ตพัง
        """
        snap = self.get_margin_snapshot()
        current_short_size = self.perp.get_short_position_size()
        target_diff = abs(order.target_size - current_short_size)
        
        notional_needed = target_diff * price
        margin_needed = (notional_needed / self.perp.config.leverage) + (notional_needed * self.perp.config.taker_fee)
        
        # คำนวณส่วนขาด (Deficit)
        deficit = (margin_needed - snap["available"]) + 50.0 # เพิ่ม Safety Buffer
        
        # กฎเหล็ก: กู้ได้ไม่เกิน 80% ของกำไรสะสมฝั่ง LP เท่านั้น เพื่อไม่ให้กินทุนต้น
        current_lp_profit = self.lp.position_value - self.lp_initial_capital
        max_allowed_rescue = max(0, current_lp_profit * 0.8)
        
        actual_rescue = min(deficit, max_allowed_rescue)
        
        if actual_rescue > 10.0: # ทำเฉพาะยอดที่คุ้มค่าโอน
            self.lp.position_value -= actual_rescue
            self.portfolio.record_transaction(TransactionType.WITHDRAWAL, -actual_rescue)
            self.portfolio.record_transaction(TransactionType.DEPOSIT, actual_rescue)
            return actual_rescue
            
        return 0.0

    def _perform_capital_sweep(self, event_list):
        """รักษาสมดุลพอร์ตตามสัดส่วนทุนตั้งต้น (เช่น LP 10k : CEX 5k)"""
        snap = self.get_margin_snapshot()
        current_lp = self.lp.position_value
        current_cex_equity = snap["wallet"] + snap["pnl"]
        total_eq = current_lp + current_cex_equity
        
        initial_lp = self.lp_initial_capital
        initial_cex = self.portfolio.initial_capital - initial_lp
        initial_total = initial_lp + initial_cex
        
        target_lp = total_eq * (initial_lp / initial_total)
        transfer_to_cex = current_lp - target_lp
        
        if transfer_to_cex > 10.0:
            # LP กำไรเกินเป้า -> โอนไปช่วย CEX
            self.lp.position_value -= transfer_to_cex
            self.portfolio.cex_wallet_balance += transfer_to_cex
            self.total_swept_to_cex += transfer_to_cex
            self.cross_rebalance_count += 1
            event_list.append(f"SWEEP_LP_TO_CEX(${transfer_to_cex:.0f})")
        elif transfer_to_cex < -10.0:
            # CEX กำไรเยอะ -> โอนกลับไปทบต้นที่ LP
            amt_to_move = abs(transfer_to_cex)
            # เช็คว่าเงินสดใน CEX พอให้โอนไหม (หัก margin ล็อกไว้)
            actual_transfer = min(amt_to_move, snap["available"])
            
            if actual_transfer > 10.0:
                self.portfolio.cex_wallet_balance -= actual_transfer
                self.lp.position_value += actual_transfer
                self.total_swept_to_lp += actual_transfer
                self.cross_rebalance_count += 1
                event_list.append(f"SWEEP_CEX_TO_LP(${actual_transfer:.0f})")