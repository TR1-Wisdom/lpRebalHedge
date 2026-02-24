"""
src/engine/backtest_engine.py
โมดูล Backtest Engine (Cross-Margin Architecture v1.8.0)

อัปเดต: v1.8.0 (Recharge Shield & Compound Yield)
- [NEW] LP ➔ PERP (Recharge Shield): ดึงเงินจาก LP ไปอุ้ม CEX ทันทีเพื่อกันพอร์ตแตก (Emergency Sweep)
- [NEW] PERP ➔ LP (Compound Yield): โยกกำไรส่วนเกินจาก CEX กลับไปปั่น LP ตามรอบ 30 วัน
- [FIX] ระบบทบต้น: ค่าธรรมเนียมที่ LP ทำได้จะถูกเก็บทบต้นในฝั่ง DeFi ก่อนโยกย้าย
- [FIX] ป้องกันกำไรทิพย์: หารยอด APR ให้เป็นต่อ Tick เสมอเพื่อความแม่นยำ 100%
"""

__version__ = "1.8.0"
__author__ = "LP-Rebal-Coding (Senior Quant Developer)"

import pandas as pd
import logging
from typing import List, Dict, Any, Union, Optional
from datetime import datetime, timedelta

from src.oracle.oracle import OracleModule
from src.lp.lp import LPModule
from src.perp.perp import PerpModule, PositionSide
from src.strategy.strategy import StrategyModule, StrategyConfig
from src.portfolio.portfolio import PortfolioModule, TransactionType, PortfolioState

logger = logging.getLogger(__name__)

class BacktestEngine:
    """เครื่องยนต์หลักในการรัน Simulation แบบ Event-driven"""

    def __init__(
        self,
        oracle: OracleModule,
        lp_list: Union[LPModule, List[LPModule]],
        perp: PerpModule,
        strategy: StrategyModule,
        portfolio: PortfolioModule
    ) -> None:
        self.oracle = oracle
        self.lp_list = lp_list if isinstance(lp_list, list) else [lp_list]
        self.perp = perp
        self.strategy = strategy
        self.portfolio = portfolio

        # สถิติและ Trackers
        self.hedge_count = 0 
        self.margin_call_events: List[Dict[str, Any]] = [] 
        self.withdrawal_count = 0
        self.cross_rebalance_count = 0
        
        # บันทึกฐานทุนของแต่ละฝั่งเพื่อใช้คำนวณส่วนเกิน/ส่วนขาด
        self.lp_initial_capital = sum(getattr(lp.config, 'initial_capital', 0.0) for lp in self.lp_list)
        self.perp_initial_capital = self.portfolio.cex_wallet_balance
        
        self.last_transfer_time: Optional[datetime] = None

    def run(
        self, 
        data_feed: pd.DataFrame, 
        strategy_config: StrategyConfig, 
        funding_rate: float = 0.0001,
        withdraw_passive_income: bool = False,
        auto_transfer_interval_days: int = 30
    ) -> pd.DataFrame:
        """รัน Simulation Loop"""
        df = self.strategy.populate_indicators(data_feed, strategy_config)
        df = self.strategy.populate_signals(df)

        history: List[PortfolioState] = []
        last_funding_time: datetime = df['date'].iloc[0]
        self.last_transfer_time = df['date'].iloc[0]
        last_withdrawal_date: datetime = df['date'].iloc[0]

        # 5 นาที = 105,120 ticks/year (ใช้หาร APR)
        ticks_per_year = 365 * 24 * 12 

        for _, row in df.iterrows():
            current_time: datetime = row['date']
            current_price: float = row['close']

            # 1. อัปเดตราคาตลาด
            self.perp.update_market_price(current_price)
            
            # 2. กระบวนการฝั่ง LP (DeFi)
            total_lp_market_value: float = 0.0
            for lp in self.lp_list:
                lp.update_price(current_price)
                
                # เก็บค่าธรรมเนียมและทบต้น (Compound) ไว้ใน LP ก่อน
                fee_raw = lp.collect_fee()
                fee_normalized = fee_raw / ticks_per_year if fee_raw > 0 else 0.0
                
                if fee_normalized > 0:
                    lp.position_value += fee_normalized
                    self.portfolio.record_transaction(TransactionType.REVENUE_LP_FEE, fee_normalized)
                
                rebal = lp.check_and_rebalance()
                if rebal.is_rebalanced:
                    self.portfolio.record_transaction(TransactionType.EXPENSE_GAS, -rebal.gas_cost)
                    self.portfolio.record_transaction(TransactionType.EXPENSE_SLIPPAGE, -rebal.slippage_cost)
                
                total_lp_market_value += lp.position_value

            # 3. Continuous Safety Check (Proactive Shield)
            self._check_and_recharge_shield(current_time)

            # 4. Periodic Compound Yield (PERP ➔ LP)
            if auto_transfer_interval_days > 0:
                if (current_time - self.last_transfer_time).days >= auto_transfer_interval_days:
                    self._compound_yield(current_time)
                    self.last_transfer_time = current_time

            # 5. Withdraw Passive Income (ดึงเงินออกจากระบบ)
            if withdraw_passive_income:
                if (current_time - last_withdrawal_date).days >= 30: 
                    # ให้ถอนเงินจากฝั่ง CEX ถ้ามีเงินเหลือมากพอ
                    if self.portfolio.cex_wallet_balance > (self.perp_initial_capital * 1.5):
                        withdraw_amt = 500.0
                        self.portfolio.record_transaction(TransactionType.WITHDRAWAL, -withdraw_amt)
                        self.portfolio.cex_wallet_balance -= withdraw_amt
                        self.withdrawal_count += 1
                        last_withdrawal_date = current_time

            # 6. Strategy Signal Execution (เปิด/ปิด Hedge)
            if not pd.isna(row['signal']):
                orders = self.strategy.generate_orders(row, strategy_config)
                for order in orders:
                    try:
                        self._execute_perp_order(order)
                    except ValueError as e:
                        if str(e) == "MARGIN_CALL":
                            # [EMERGENCY] พอร์ตกำลังจะแตก! สั่งดึงเงินก้อนใหญ่จาก LP ทันที
                            recharged = self._recharge_shield(current_time, emergency=True)
                            if recharged:
                                # ลองเปิดออเดอร์อีกครั้งหลังเติมเงิน
                                try:
                                    self._execute_perp_order(order)
                                except ValueError:
                                    pass

            # 7. Apply Funding Rate
            if (current_time - last_funding_time).total_seconds() >= 8 * 3600:
                funding_pnl = self.perp.apply_funding(funding_rate)
                if funding_pnl != 0:
                    txn = TransactionType.REVENUE_FUNDING if funding_pnl > 0 else TransactionType.EXPENSE_FUNDING
                    self.portfolio.record_transaction(txn, funding_pnl)
                    self.portfolio.cex_wallet_balance += funding_pnl
                last_funding_time = current_time

            # 8. บันทึก Portfolio State (ยอดอัปเดตแบบเรียลไทม์)
            state = self.portfolio.get_state(
                current_time, 
                sum(lp.position_value for lp in self.lp_list), 
                self.perp.get_total_unrealized_pnl(),
                self.perp.get_total_margin_used()
            )
            history.append(state)

        return pd.DataFrame([vars(h) for h in history])

    def _check_and_recharge_shield(self, current_time: datetime) -> None:
        """ตรวจสอบความปลอดภัย CEX เชิงรุก หากเสี่ยงอันตรายให้เติม Shield ทันที"""
        avail_margin = self.portfolio.cex_wallet_balance + self.perp.get_total_unrealized_pnl() - self.perp.get_total_margin_used()
        safe_margin_level = self.perp_initial_capital * 0.10 # ต้องมี Margin เหลืออย่างน้อย 10%
        
        if avail_margin < safe_margin_level:
            self._recharge_shield(current_time, emergency=False)

    def _recharge_shield(self, current_time: datetime, emergency: bool = False) -> bool:
        """
        [สถานการณ์ที่ 1] LP ➔ PERP (Recharge Shield)
        โยกกำไร (หรือเงินทุน) จากฝั่ง LP ไปเติม "สายป่าน" ให้ฝั่ง CEX เพื่อป้องกันพอร์ตแตก
        """
        perp_pnl = self.perp.get_total_unrealized_pnl()
        margin_used = self.perp.get_total_margin_used()
        avail_margin = self.portfolio.cex_wallet_balance + perp_pnl - margin_used
        
        # เป้าหมายการเติมเงิน (เติมให้กลับมาอยู่ในระดับปลอดภัย หรือ เติมหนักถ้าฉุกเฉิน)
        target_margin = self.perp_initial_capital * (0.50 if emergency else 0.20)
        shortfall = target_margin - avail_margin
        
        if shortfall > 0:
            current_lp_total = sum(lp.position_value for lp in self.lp_list)
            # ดึงเงินจาก LP (ดึงได้สูงสุด 20% ของขนาด LP เพื่อไม่ให้กระทบ DeFi หนักเกินไป)
            max_sweep = current_lp_total * 0.20
            sweep_amt = min(shortfall, max_sweep)
            
            if sweep_amt > 0:
                # ลดมูลค่า LP ลง (สมมติว่าดึงจาก Tier แรกเป็นหลัก)
                self.lp_list[0].position_value -= sweep_amt
                
                # โอนเข้าเป็น Margin ใน CEX
                self.portfolio.cex_wallet_balance += sweep_amt
                
                event_type = "EMERGENCY: LP ➔ PERP" if emergency else "LP ➔ PERP (Recharge Shield)"
                self.margin_call_events.append({
                    'timestamp': current_time, 
                    'event': event_type, 
                    'amount': sweep_amt
                })
                self.cross_rebalance_count += 1
                return True
        return False

    def _compound_yield(self, current_time: datetime) -> None:
        """
        [สถานการณ์ที่ 2] PERP ➔ LP (Compound Yield)
        โยกเงินส่วนเกินกลับไปเพิ่มขนาดพอร์ต LP เพื่อปั่นค่าธรรมเนียมให้โตขึ้น
        """
        # คำนวณความมั่งคั่งฝั่ง CEX ปัจจุบัน
        cex_equity = self.portfolio.cex_wallet_balance + self.perp.get_total_unrealized_pnl()
        
        # เงินเหลือล้น (Excess) คือส่วนที่เกินจากทุนเริ่มต้น CEX
        excess = cex_equity - self.perp_initial_capital
        
        # ถ้ามีเงินส่วนเกินมากกว่า 10% ของทุนเริ่มต้น ค่อยทำ Compound เพื่อประหยัดค่าโอน
        if excess > (self.perp_initial_capital * 0.10):
            # ดึงกำไรกลับ 50% ของส่วนเกิน
            sweep_amt = excess * 0.50
            
            # ต้องแน่ใจว่า CEX Wallet มีเงินสด(Realized) มากพอให้ดึงออก
            if sweep_amt <= self.portfolio.cex_wallet_balance:
                # ถอนออกจาก CEX
                self.portfolio.cex_wallet_balance -= sweep_amt
                
                # โอนเข้าไปทบต้นในฝั่ง LP (DeFi)
                self.lp_list[0].position_value += sweep_amt
                
                self.margin_call_events.append({
                    'timestamp': current_time, 
                    'event': 'PERP ➔ LP (Compound Yield)', 
                    'amount': sweep_amt
                })
                self.cross_rebalance_count += 1

    def _execute_perp_order(self, order) -> None:
        """จัดการการซื้อขายฝั่ง CEX พร้อมหักค่าธรรมเนียมจาก Wallet โดยตรง"""
        current_size = self.perp.get_short_position_size()
        balance = self.portfolio.cex_wallet_balance
        
        if order.action == 'HEDGE_ON':
            diff = order.target_size - current_size
            if diff > 0:
                fee = self.perp.open_position(PositionSide.SHORT, diff, balance)
                self.portfolio.record_transaction(TransactionType.EXPENSE_PERP_FEE, -fee)
                self.portfolio.cex_wallet_balance -= fee
                self.hedge_count += 1
                
        elif order.action == 'HEDGE_OFF':
            if current_size > 0:
                realized_pnl = self.perp.positions[PositionSide.SHORT].unrealized_pnl
                fee = self.perp.close_position(PositionSide.SHORT)
                
                # รับรู้กำไร/ขาดทุนเป็นเงินสด
                self.portfolio.record_transaction(TransactionType.DEPOSIT, realized_pnl)
                self.portfolio.cex_wallet_balance += (realized_pnl - fee)
                self.hedge_count += 1

        elif order.action == 'ADJUST_HEDGE':
            diff = order.target_size - current_size
            if diff > 0: 
                fee = self.perp.open_position(PositionSide.SHORT, diff, balance)
                self.portfolio.record_transaction(TransactionType.EXPENSE_PERP_FEE, -fee)
                self.portfolio.cex_wallet_balance -= fee
                self.hedge_count += 1
            elif diff < 0: 
                realized_pnl, fee = self.perp.close_partial_position(PositionSide.SHORT, abs(diff))
                self.portfolio.record_transaction(TransactionType.DEPOSIT, realized_pnl)
                self.portfolio.cex_wallet_balance += (realized_pnl - fee)
                self.hedge_count += 1