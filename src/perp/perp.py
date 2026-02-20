"""
src/perp/perp.py
โมดูล Perp (Perpetual Futures) สำหรับโปรเจกต์ Inventory LP Backtester

ประวัติการแก้ไข:
- v1.0.2 (2026-02-20): [PM FIXED] แก้ไขบั๊ก Overwrite Position ให้เป็นระบบ Average Entry 
                       และเพิ่มระบบ Partial Close เพื่ออุดรูรั่วของ PnL
"""

__version__ = "1.0.2"

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
    """การตั้งค่าระบบ Perp"""
    leverage: float = 1.0
    maker_fee: float = 0.0002
    taker_fee: float = 0.0005


class PerpModule:
    """จำลองพอร์ตเทรด Perpetual Futures"""

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
        """เปิดสถานะใหม่ หรือ *เพิ่ม* ขนาดสถานะเดิมพร้อมถัวเฉลี่ยราคา (Average Entry)"""
        notional_value: float = size_in_token * self.current_market_price
        trading_fee: float = notional_value * self.config.taker_fee
        added_margin: float = notional_value / self.config.leverage

        if side in self.positions:
            # ของเดิมมีอยู่แล้ว ทำการถัวราคา (Average Entry Price)
            pos = self.positions[side]
            total_size = pos.size + size_in_token
            # สมการถัวเฉลี่ย: (ทุนเก่า + ทุนใหม่) / จำนวนเหรียญรวม
            new_entry = ((pos.size * pos.entry_price) + (size_in_token * self.current_market_price)) / total_size
            
            pos.size = total_size
            pos.entry_price = new_entry
            pos.margin_used += added_margin
        else:
            # เปิดไม้ใหม่เอี่ยม
            self.positions[side] = Position(
                side=side,
                size=size_in_token,
                entry_price=self.current_market_price,
                margin_used=added_margin
            )
        
        self.total_trading_fees += trading_fee
        self.update_market_price(self.current_market_price)
        return trading_fee

    def close_partial_position(self, side: PositionSide, size_to_close: float) -> tuple[float, float]:
        """ปิดสถานะบางส่วน และรับรู้กำไร/ขาดทุน (Realized PnL) ตามสัดส่วน"""
        if side not in self.positions:
            return 0.0, 0.0
            
        pos = self.positions[side]
        
        # ถ้าสั่งปิดมากกว่าหรือเท่ากับที่มี ให้ปิดทั้งหมดไปเลย
        if size_to_close >= pos.size:
            realized_pnl = pos.unrealized_pnl
            fee = self.close_position(side)
            return realized_pnl, fee
            
        # 1. คำนวณสัดส่วนที่จะปิด
        ratio = size_to_close / pos.size
        
        # 2. รับรู้กำไร/ขาดทุนตามสัดส่วน
        realized_pnl = pos.unrealized_pnl * ratio
        
        # 3. คิดค่าธรรมเนียมเฉพาะก้อนที่ปิด
        notional_closed = size_to_close * self.current_market_price
        fee = notional_closed * self.config.taker_fee
        
        # 4. หักลบเหรียญและ Margin ออกจากพอร์ต
        pos.size -= size_to_close
        pos.margin_used -= (pos.margin_used * ratio)
        
        self.total_trading_fees += fee
        self.update_market_price(self.current_market_price) # อัปเดต PnL ก้อนที่เหลือ
        
        return realized_pnl, fee

    def close_position(self, side: PositionSide) -> float:
        """ปิดสถานะทั้งหมด"""
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
            net_funding_pnl += payment if side == PositionSide.SHORT else -payment
                
        self.total_funding_pnl += net_funding_pnl
        return net_funding_pnl

    def get_short_position_size(self) -> float:
        return self.positions[PositionSide.SHORT].size if PositionSide.SHORT in self.positions else 0.0

    def get_total_unrealized_pnl(self) -> float:
        return sum(pos.unrealized_pnl for pos in self.positions.values())