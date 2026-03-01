"""
src/perp/perp.py
โมดูลจัดการสถานะ Perpetual Futures (v1.1.0 - Realized PnL Edition)

อัปเดต:
- ระบบคำนวณราคาเข้าเฉลี่ย (Weighted Entry Price - VWAP)
- ระบบคืนค่า Realized PnL เมื่อมีการปิดหรือลดขนาดสถานะ (เพื่อป้องกัน PnL Leakage)
- ระบบคำนวณ Margin Used สำหรับเช็คความเสี่ยงพอร์ตแตก (Margin Call)
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional, Tuple

class PositionSide(Enum):
    """ทิศทางของสถานะการเทรด"""
    LONG = "LONG"
    SHORT = "SHORT"

@dataclass
class Position:
    """ข้อมูลรายละเอียดของสถานะที่เปิดอยู่"""
    side: PositionSide
    size: float         # ขนาดสถานะในหน่วย Token (เช่น ETH)
    entry_price: float  # ราคาเข้าเฉลี่ย (VWAP)
    unrealized_pnl: float = 0.0

@dataclass
class PerpConfig:
    """การตั้งค่าพารามิเตอร์ของโมดูล Perp"""
    leverage: float = 5.0
    taker_fee: float = 0.0005  # ค่าธรรมเนียม Taker (0.05%)

class PerpModule:
    def __init__(self, config: PerpConfig):
        self.config = config
        self.positions: Dict[PositionSide, Position] = {}
        self.current_market_price: float = 0.0
        self.total_trading_fees: float = 0.0
        self.total_funding_pnl: float = 0.0
        self.logger = logging.getLogger("PerpModule")

    def update_market_price(self, price: float):
        """อัปเดตราคาตลาดปัจจุบันและคำนวณกำไร/ขาดทุนที่ยังไม่รับรู้ (MTM)"""
        self.current_market_price = price
        for side, pos in self.positions.items():
            if side == PositionSide.LONG:
                pos.unrealized_pnl = pos.size * (price - pos.entry_price)
            else:
                pos.unrealized_pnl = pos.size * (pos.entry_price - price)

    def open_position(self, side: PositionSide, size_in_token: float, cex_wallet_balance: float) -> float:
        """
        เปิดสถานะใหม่หรือเพิ่มขนาดสถานะ (Average Entry)
        คืนค่า: ค่าธรรมเนียมการเทรด (Trading Fee) ที่เกิดขึ้น
        """
        if size_in_token <= 0: return 0.0
        
        notional_value = size_in_token * self.current_market_price
        trade_fee = notional_value * self.config.taker_fee
        
        # ตรวจสอบเบื้องต้นเรื่อง Margin (ความปลอดภัยชั้นแรก)
        required_margin = notional_value / self.config.leverage
        if (cex_wallet_balance - trade_fee) < required_margin:
            # หมายเหตุ: ในระบบจำลอง Engine จะเป็นคนดักจับ Exception นี้เพื่อบันทึกเหตุการณ์ Margin Call
            raise ValueError("MARGIN_CALL")

        if side in self.positions:
            # กรณีมีสถานะเดิมอยู่แล้ว ให้คำนวณราคาเฉลี่ยใหม่ (VWAP)
            pos = self.positions[side]
            old_total_cost = pos.size * pos.entry_price
            new_add_cost = size_in_token * self.current_market_price
            
            new_total_size = pos.size + size_in_token
            new_entry_price = (old_total_cost + new_add_cost) / new_total_size
            
            pos.size = new_total_size
            pos.entry_price = new_entry_price
        else:
            # เปิดสถานะใหม่
            self.positions[side] = Position(
                side=side, 
                size=size_in_token, 
                entry_price=self.current_market_price
            )

        self.total_trading_fees += trade_fee
        self.update_market_price(self.current_market_price)
        return trade_fee

    def close_partial_position(self, side: PositionSide, size_to_close: float) -> Tuple[float, float]:
        """
        [CORE FUNCTION] ลดขนาดหรือปิดสถานะบางส่วน
        คืนค่า: (Realized PnL, Trade Fee)
        """
        if side not in self.positions:
            return 0.0, 0.0
            
        pos = self.positions[side]
        actual_close_size = min(size_to_close, pos.size)
        
        # 1. คำนวณ Realized PnL จากส่วนที่ปิดจริง (เทียบกับ Entry Price ของตำแหน่งนั้น)
        if side == PositionSide.LONG:
            realized_pnl = actual_close_size * (self.current_market_price - pos.entry_price)
        else:
            realized_pnl = actual_close_size * (pos.entry_price - self.current_market_price)
            
        # 2. คำนวณค่าธรรมเนียม Taker Fee สำหรับออเดอร์ปิด
        trade_fee = (actual_close_size * self.current_market_price) * self.config.taker_fee
        
        # 3. อัปเดตข้อมูลสถานะ
        self.total_trading_fees += trade_fee
        pos.size -= actual_close_size
        
        # ถ้าปิดจนหมด ให้ลบสถานะออกจากรายการ
        if pos.size <= 1e-9: # กันค่าจุกจิกจาก Floating Point
            del self.positions[side]
        else:
            self.update_market_price(self.current_market_price)
            
        return realized_pnl, trade_fee

    def apply_funding(self, rate: float) -> float:
        """
        คิดค่าธรรมเนียมการถือครองสถานะ (Funding Rate)
        คืนค่า: จำนวนเงิน Funding ที่ได้รับ (+) หรือต้องจ่าย (-)
        """
        net_funding = 0.0
        for side, pos in self.positions.items():
            notional = pos.size * self.current_market_price
            payment = notional * rate
            
            # ใน Binance Futures: 
            # ถ้า Rate เป็นบวก (+) -> ฝั่ง Long จ่ายให้ฝั่ง Short
            if side == PositionSide.SHORT:
                net_funding += payment
            else:
                net_funding -= payment
                
        self.total_funding_pnl += net_funding
        return net_funding

    def get_short_position_size(self) -> float:
        """คืนค่าขนาดของสถานะ Short ปัจจุบัน (สำหรับใช้ในระบบ Hedge)"""
        pos = self.positions.get(PositionSide.SHORT)
        return pos.size if pos else 0.0

    def get_total_unrealized_pnl(self) -> float:
        """รวมกำไร/ขาดทุนที่ยังไม่รับรู้ของทุกสถานะที่เปิดอยู่"""
        return sum(pos.unrealized_pnl for pos in self.positions.values())

    def get_total_margin_used(self) -> float:
        """คำนวณจำนวนเงินประกัน (Margin) ที่ถูกล็อกไว้ทั้งหมด"""
        total_margin = 0.0
        for pos in self.positions.values():
            # Initial Margin = Notional Value / Leverage
            total_margin += (pos.size * self.current_market_price) / self.config.leverage
        return total_margin