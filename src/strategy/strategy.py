"""
โมดูล Strategy (The Brain) สำหรับ Inventory LP Backtester

ทำหน้าที่เป็นสมองกลในการวิเคราะห์ข้อมูลราคา (OHLCV) เพื่อสร้างสัญญาณ (Signals) 
และคำนวณขนาดของ Position ในการทำ Smart Hedge ควบคู่กับระบบ Safety Net

ประวัติการแก้ไข (Version Control):
- v1.0.0 (2026-02-20): สร้าง Strategy Module รองรับ Freqtrade, Safety Net และ Delta Threshold
"""

__version__ = "1.0.0"
__author__ = "LP-Rebal-Coding (Senior Quant Developer)"
__date__ = "2026-02-20"

import numpy as np
import pandas as pd
from typing import Protocol, List
from dataclasses import dataclass
from datetime import datetime


class ILPModule(Protocol):
    """
    Interface (Protocol) สำหรับจำลอง Dependency ของ LP Module
    เพื่อให้ Strategy Module เข้าถึงข้อมูล Inventory แบบ Loosely Coupled
    """
    def get_eth_inventory(self) -> float:
        """ดึงค่าปริมาณ ETH ที่ถือครองจริงใน Liquidity Pool"""
        ...


class IPerpModule(Protocol):
    """
    Interface (Protocol) สำหรับจำลอง Dependency ของ Perp Module
    เพื่อให้ Strategy Module เข้าถึงสถานะของ Short Position
    """
    def get_short_position_size(self) -> float:
        """ดึงค่าขนาดของ Short Position ในปัจจุบัน"""
        ...


@dataclass
class StrategyConfig:
    """
    Data Transfer Object (DTO) สำหรับเก็บค่าพารามิเตอร์ของกลยุทธ์
    
    Attributes:
        ema_period (int): จำนวนแท่งสำหรับคำนวณ Exponential Moving Average
        safety_net_pct (float): เกณฑ์เปอร์เซ็นต์ติดลบในแท่งเดียวเพื่อกระตุก Safety Net (เช่น 0.02 = 2%)
        hedge_threshold (float): เกณฑ์ความเบี่ยงเบนของ Delta ที่ยอมได้ก่อนต้องปรับ Hedge (เช่น 0.05 = 5%)
    """
    ema_period: int = 200
    safety_net_pct: float = 0.02
    hedge_threshold: float = 0.05


@dataclass
class OrderEvent:
    """
    Data Transfer Object (DTO) สำหรับแพ็กเกจคำสั่งที่ส่งออกจากสมองกล (Strategy)
    
    Attributes:
        timestamp (datetime): เวลาที่ออกคำสั่ง
        target_module (str): โมดูลเป้าหมาย ('LP' หรือ 'PERP')
        action (str): ประเภทการทำงาน ('HEDGE_ON', 'HEDGE_OFF', 'ADJUST_HEDGE')
        target_size (float): ขนาดเป้าหมายของการปรับ Position
        reason (str): เหตุผลในการทริกเกอร์คำสั่ง (ใช้วิเคราะห์ผล Backtest)
    """
    timestamp: datetime
    target_module: str
    action: str
    target_size: float
    reason: str


class StrategyModule:
    """
    คลาส StrategyModule สำหรับจัดการตรรกะการเทรด (Stateless Engine)
    
    รองรับโครงสร้างแบบ Freqtrade โดยรับ DataFrame เข้ามาประมวลผล Indicator และ Signal
    และทำการประสานงานกับข้อมูลจาก LP/Perp Interfaces ผ่าน Dependency Injection
    """

    def __init__(self, lp_module: ILPModule, perp_module: IPerpModule) -> None:
        """
        เชื่อมต่อกับโมดูลอื่นๆ ผ่าน Dependency Injection (DI)

        Args:
            lp_module (ILPModule): อินเทอร์เฟซของ LP Module
            perp_module (IPerpModule): อินเทอร์เฟซของ Perp Module
        """
        self.lp = lp_module
        self.perp = perp_module

    def populate_indicators(self, dataframe: pd.DataFrame, config: StrategyConfig) -> pd.DataFrame:
        """
        คำนวณ Technical Indicators ตามมาตรฐานที่ Freqtrade รองรับ

        Args:
            dataframe (pd.DataFrame): ข้อมูลราคารูปแบบ OHLCV
            config (StrategyConfig): ค่าพารามิเตอร์ของกลยุทธ์

        Returns:
            pd.DataFrame: DataFrame ที่เพิ่มคอลัมน์ Indicators เรียบร้อยแล้ว
        """
        df = dataframe.copy()
        
        # คำนวณ EMA สำหรับจับ Trend
        df['ema'] = df['close'].ewm(span=config.ema_period, adjust=False).mean()
        
        # คำนวณ % การเปลี่ยนแปลงเพื่อใช้เป็นตัวตรวจจับของ Safety Net
        df['pct_change'] = df['close'].pct_change().fillna(0.0)
        
        return df

    def populate_signals(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        """
        แปลง Indicators ให้กลายเป็น Binary Signals

        Args:
            dataframe (pd.DataFrame): DataFrame ที่ผ่านการรัน populate_indicators มาแล้ว

        Returns:
            pd.DataFrame: DataFrame ที่มีคอลัมน์ 'signal' (1 = Bullish, -1 = Bearish)
        """
        df = dataframe.copy()
        
        # เงื่อนไขพื้นฐาน: ถ้าราคาปัจจุบัน > EMA คือขาขึ้น (1), น้อยกว่าคือขาลง (-1)
        df['signal'] = np.where(df['close'] > df['ema'], 1, -1)
        
        return df

    def generate_orders(self, current_tick: pd.Series, config: StrategyConfig) -> List[OrderEvent]:
        """
        ประเมินสภาวะตลาดที่ Tick ปัจจุบันเพื่อออกคำสั่ง (Order Events) ควบคุม Hedge

        Args:
            current_tick (pd.Series): ข้อมูลราคา 1 แถว ณ เวลาปัจจุบัน (ต้องมี date, signal, pct_change)
            config (StrategyConfig): ค่าพารามิเตอร์ของกลยุทธ์

        Returns:
            List[OrderEvent]: รายการคำสั่งที่ต้องส่งให้ Engine ไป Execute (ถ้ามี)
        """
        orders: List[OrderEvent] = []
        
        signal: int = int(current_tick['signal'])
        pct_change: float = float(current_tick['pct_change'])
        timestamp: datetime = current_tick['date']
        
        actual_eth: float = self.lp.get_eth_inventory()
        current_short: float = self.perp.get_short_position_size()
        
        is_safety_trigger: bool = False
        
        # 1. กลไก Safety Net (Circuit Breaker)
        # ถ้าระบบมองขึ้น (Unhedged) แต่กราฟทุบแรงเกินเกณฑ์ ให้บังคับสับสวิตช์เป็นป้องกันตัว (Hedge) ทันที
        if signal == 1 and pct_change <= -config.safety_net_pct:
            signal = -1
            is_safety_trigger = True
            
        # 2. คำนวณ Target Short Size
        # มองลง (-1) -> ป้องกันเต็ม 100% ของของที่มี
        # มองขึ้น (1) -> ปิดเกราะรับกำไรส่วนต่างราคา
        target_short: float = actual_eth if signal == -1 else 0.0
        
        # 3. Delta Threshold & Re-sizing Logic
        diff: float = abs(target_short - current_short)
        is_flip: bool = (target_short == 0.0 and current_short > 0.0) or (target_short > 0.0 and current_short == 0.0)
        
        needs_adjustment: bool = False
        
        if is_flip:
            # กรณีกลับหน้าเล่น (Flip) ไม่ต้องสน Threshold ให้ปรับทันที
            needs_adjustment = True
        elif target_short > 0.0:
            # กรณี Hedge On อยู่ ให้เช็คว่า Inventory ปัจจุบันกับ Short Size ถ่างกันเกิน Threshold หรือไม่
            drift_ratio: float = diff / target_short if target_short > 0 else 0.0
            if drift_ratio > config.hedge_threshold:
                needs_adjustment = True
                
        if needs_adjustment:
            action_type: str = 'HEDGE_ON' if target_short > 0 else 'HEDGE_OFF'
            
            # ถ้าระบบให้ Hedge อยู่แล้ว แต่ปรับไซส์ (ไม่ได้ Flip) ใช้คำว่า ADJUST
            if not is_flip and target_short > 0:
                action_type = 'ADJUST_HEDGE'
                
            reason: str = "Safety Net Triggered" if is_safety_trigger else "Signal Flip" if is_flip else "Threshold Drift"
            
            orders.append(OrderEvent(
                timestamp=timestamp,
                target_module='PERP',
                action=action_type,
                target_size=target_short,
                reason=reason
            ))
            
        return orders