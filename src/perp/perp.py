"""
src/perp/perp.py
โมดูล Perp (Perpetual Futures) สำหรับโปรเจกต์ Inventory LP Backtester

จำลองการทำงานของ CEX Futures โดยเน้นที่การจัดการสถานะ (Position), 
การคำนวณกำไรขาดทุนแบบ Real-time และการคิดค่าธรรมเนียม/Funding

ประวัติการแก้ไข:
- v1.0.1 (2026-02-20): ปรับปรุงโครงสร้างให้รองรับ Leverage และแก้ไข Mismatch กับ Test
"""

__version__ = "1.0.1"

from dataclasses import dataclass
from enum import Enum
from typing import Dict


class PositionSide(Enum):
    """ทิศทางของสถานะการเทรด"""
    LONG = "LONG"
    SHORT = "SHORT"


@dataclass
class Position:
    """ข้อมูลสถานะที่ถือครอง"""
    side: PositionSide
    size: float
    entry_price: float
    unrealized_pnl: float = 0.0
    margin_used: float = 0.0


@dataclass
class PerpConfig:
    """การตั้งค่าระบบ Perp (ต้องมีฟิลด์ leverage)"""
    leverage: float = 1.0
    maker_fee: float = 0.0002
    taker_fee: float = 0.0005


class PerpModule:
    """จำลองพอร์ตเทรด Perpetual Futures (Audit Version)"""

    def __init__(self, config: PerpConfig) -> None:
        self.config = config
        self.positions: Dict[PositionSide, Position] = {}
        self.current_market_price: float = 0.0
        self.total_trading_fees: float = 0.0
        self.total_funding_pnl: float = 0.0

    def update_market_price(self, new_price: float) -> None:
        """Mark-to-market คำนวณ PnL จริงตามการเคลื่อนที่ของราคา"""
        self.current_market_price = new_price
        for side, pos in self.positions.items():
            if side == PositionSide.LONG:
                pos.unrealized_pnl = pos.size * (new_price - pos.entry_price)
            else:
                pos.unrealized_pnl = pos.size * (pos.entry_price - new_price)

    def open_position(self, side: PositionSide, size_in_token: float) -> float:
        """เปิดสถานะ โดยคำนวณ Margin และ Trading Fee จริง"""
        notional_value: float = size_in_token * self.current_market_price
        trading_fee: float = notional_value * self.config.taker_fee
        margin_req: float = notional_value / self.config.leverage

        self.positions[side] = Position(
            side=side,
            size=size_in_token,
            entry_price=self.current_market_price,
            margin_used=margin_req
        )
        
        self.total_trading_fees += trading_fee
        self.update_market_price(self.current_market_price)
        return trading_fee

    def close_position(self, side: PositionSide) -> float:
        """ปิดสถานะ และคิดค่าธรรมเนียมตอนปิด"""
        if side not in self.positions:
            return 0.0
            
        pos = self.positions[side]
        trading_fee: float = (pos.size * self.current_market_price) * self.config.taker_fee
        
        del self.positions[side]
        self.total_trading_fees += trading_fee
        return trading_fee

    def apply_funding(self, funding_rate_pct: float) -> float:
        """คำนวณ Funding Rate โดยอิงจาก Notional Value ของทุก Position"""
        net_funding_pnl: float = 0.0
        for side, pos in self.positions.items():
            notional: float = pos.size * self.current_market_price
            payment: float = notional * funding_rate_pct
            # Long จ่าย Short รับ (ถ้า Rate บวก)
            net_funding_pnl += payment if side == PositionSide.SHORT else -payment
                
        self.total_funding_pnl += net_funding_pnl
        return net_funding_pnl

    def get_short_position_size(self) -> float:
        """Interface สำหรับ Strategy เพื่อเช็คสัดส่วนการ Hedge"""
        return self.positions[PositionSide.SHORT].size if PositionSide.SHORT in self.positions else 0.0

    def get_total_unrealized_pnl(self) -> float:
        """รวม PnL ทุกสถานะเพื่อส่งให้ Portfolio"""
        return sum(pos.unrealized_pnl for pos in self.positions.values())