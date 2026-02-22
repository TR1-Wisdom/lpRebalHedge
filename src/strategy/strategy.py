"""
src/strategy/strategy.py
โมดูล Strategy (The Brain) สำหรับ Inventory LP Backtester

ประวัติการแก้ไข:
- v1.0.3 (2026-02-22): [PM FIXED] อัปเกรด Safety Net ให้เป็นระบบอิสระ (Toggle ON/OFF) 
                       และเพิ่มระบบ Override Threshold สำหรับโหมด Always Hedge
"""

__version__ = "1.0.3"
__author__ = "LP-Rebal-Coding (Senior Quant Developer)"
__date__ = "2026-02-22"

import numpy as np
import pandas as pd
from typing import Protocol, List
from dataclasses import dataclass
from datetime import datetime


class ILPModule(Protocol):
    def get_eth_inventory(self) -> float:
        ...

class IPerpModule(Protocol):
    def get_short_position_size(self) -> float:
        ...

@dataclass
class StrategyConfig:
    ema_period: int = 200
    safety_net_pct: float = 0.02
    hedge_threshold: float = 0.05
    hedge_mode: str = 'smart' 
    use_safety_net: bool = True  # [PM ADDED] สวิตช์เปิด/ปิด Safety Net สำหรับทุกโหมด


@dataclass
class OrderEvent:
    timestamp: datetime
    target_module: str
    action: str
    target_size: float
    reason: str


class StrategyModule:
    def __init__(self, lp_module: ILPModule, perp_module: IPerpModule) -> None:
        self.lp = lp_module
        self.perp = perp_module

    def populate_indicators(self, dataframe: pd.DataFrame, config: StrategyConfig) -> pd.DataFrame:
        df = dataframe.copy()
        df['ema'] = df['close'].ewm(span=config.ema_period, adjust=False).mean()
        df['pct_change'] = df['close'].pct_change().fillna(0.0)
        return df

    def populate_signals(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        df = dataframe.copy()
        df['signal'] = np.where(df['close'] > df['ema'], 1, -1)
        return df

    def generate_orders(self, current_tick: pd.Series, config: StrategyConfig) -> List[OrderEvent]:
        orders: List[OrderEvent] = []
        
        pct_change: float = float(current_tick['pct_change'])
        timestamp: datetime = current_tick['date']
        
        # --- 1. Base Signal (Trend or Always) ---
        if getattr(config, 'hedge_mode', 'smart') == 'always':
            signal: int = -1
        else:
            signal: int = int(current_tick['signal'])
            
        # --- 2. Safety Net Logic (Independent Layer) ---
        is_safety_trigger: bool = False
        force_adjust: bool = False
        
        if getattr(config, 'use_safety_net', True):
            # ถ้าราคากระชากแรงกว่าค่าที่ตั้งไว้ (ไม่ว่าจะขึ้นหรือลง)
            if abs(pct_change) >= config.safety_net_pct:
                is_safety_trigger = True
                force_adjust = True # เตะปลั๊ก Threshold บังคับปรับ Hedge ให้เป๊ะทันที
                
                # กรณีพิเศษสำหรับ Smart Mode: ถ้าเป็นขาขึ้นแต่โดนทุบหนัก ให้พลิกกลับมา Short
                if signal == 1 and pct_change <= -config.safety_net_pct:
                    signal = -1
                    
        # --- 3. Position Sizing & Drift Check ---
        actual_eth: float = self.lp.get_eth_inventory()
        current_short: float = self.perp.get_short_position_size()
        
        target_short: float = actual_eth if signal == -1 else 0.0
        diff: float = abs(target_short - current_short)
        is_flip: bool = (target_short == 0.0 and current_short > 0.0) or (target_short > 0.0 and current_short == 0.0)
        
        needs_adjustment: bool = False
        
        if is_flip:
            needs_adjustment = True
        elif target_short > 0.0:
            drift_ratio: float = diff / target_short if target_short > 0 else 0.0
            
            # [PM FIXED] ถ้าระยะห่างเกิน Threshold "หรือ" โดน Safety Net บังคับทำงาน
            if drift_ratio > config.hedge_threshold or force_adjust:
                # กันกรณีที่ของตรงกันเป๊ะอยู่แล้วไม่ต้องส่งออเดอร์ให้เสียเวลา
                if diff > 0.0001: 
                    needs_adjustment = True
                
        if needs_adjustment:
            action_type: str = 'HEDGE_ON' if target_short > 0 else 'HEDGE_OFF'
            if not is_flip and target_short > 0:
                action_type = 'ADJUST_HEDGE'
                
            reason: str = "Safety Net Triggered" if is_safety_trigger else ("Signal Flip" if is_flip else "Threshold Drift")
            
            orders.append(OrderEvent(
                timestamp=timestamp,
                target_module='PERP',
                action=action_type,
                target_size=target_short,
                reason=reason
            ))
            
        return orders