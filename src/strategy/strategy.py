"""
src/strategy/strategy.py
โมดูล Strategy (The Brain) - Ultimate Version

อัปเดต:
- รองรับ Multi-Tier LP (List) และ Backward Compatibility (Single LP)
- เพิ่มระบบ Anti-Whipsaw (Hysteresis Band) ลดสัญญาณหลอกช่วงตลาด Sideway
"""

__version__ = "1.2.0"
__author__ = "LP-Rebal-Coding (Senior Quant Developer)"

import numpy as np
import pandas as pd
from typing import Protocol, List, Union
from dataclasses import dataclass
from datetime import datetime


class ILPModule(Protocol):
    """Interface สำหรับ LP Module เพื่อให้ Strategy เรียกใช้งานได้โดยไม่ยึดติด Implementation"""
    def get_eth_inventory(self) -> float:
        ...

class IPerpModule(Protocol):
    """Interface สำหรับ Perp Module"""
    def get_short_position_size(self) -> float:
        ...

@dataclass
class StrategyConfig:
    """คอนฟิกสำหรับกลยุทธ์การเทรด"""
    ema_period: int = 200
    safety_net_pct: float = 0.02
    hedge_threshold: float = 0.05
    hedge_mode: str = 'smart'
    hysteresis_band_pct: float = 0.005  # [NEW] Anti-Whipsaw Band (0.5%)


@dataclass
class OrderEvent:
    """Data Transfer Object สำหรับคำสั่งเทรด"""
    timestamp: datetime
    target_module: str
    action: str
    target_size: float
    reason: str


class StrategyModule:
    """สมองของระบบ ทำหน้าที่วิเคราะห์สัญญาณและสั่งการ Hedge"""

    def __init__(self, lp_modules: Union[ILPModule, List[ILPModule]], perp_module: IPerpModule) -> None:
        """
        Initializes StrategyModule.

        Args:
            lp_modules: โมดูล LP (รองรับทั้งแบบ Object เดี่ยวๆ หรือแบบ List)
            perp_module: โมดูล Perp ที่ใช้ในการ Hedge
        """
        if isinstance(lp_modules, list):
            self.lp_modules = lp_modules
        else:
            self.lp_modules = [lp_modules]
            
        self.perp = perp_module

    def populate_indicators(self, dataframe: pd.DataFrame, config: StrategyConfig) -> pd.DataFrame:
        """คำนวณ Indicators พื้นฐานพร้อมจุดอ้างอิง Hysteresis Band"""
        df = dataframe.copy()
        df['ema'] = df['close'].ewm(span=config.ema_period, adjust=False).mean()
        # [NEW] สร้าง Band บนและล่างเพื่อดักจับ Whipsaw
        df['upper_band'] = df['ema'] * (1 + config.hysteresis_band_pct)
        df['lower_band'] = df['ema'] * (1 - config.hysteresis_band_pct)
        df['pct_change'] = df['close'].pct_change().fillna(0.0)
        return df

    def populate_signals(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        """สร้างสัญญาณเทรดโดยใช้ Hysteresis Logic เพื่อลด Over-trading"""
        df = dataframe.copy()
        
        # [NEW] สัญญาณจะเปลี่ยนก็ต่อเมื่อทะลุ Band เท่านั้น หากอยู่ตรงกลางให้คงสถานะเดิม (Forward Fill)
        conditions = [
            df['close'] > df['upper_band'],
            df['close'] < df['lower_band']
        ]
        choices = [1, -1]
        
        df['raw_signal'] = np.select(conditions, choices, default=np.nan)
        df['signal'] = df['raw_signal'].ffill().fillna(-1)  # เริ่มต้นให้เอียงไปทางป้องกันความเสี่ยง (-1)
        
        return df

    def generate_orders(self, current_tick: pd.Series, config: StrategyConfig) -> List[OrderEvent]:
        """สร้างคำสั่งเทรดโดยพิจารณาจาก Inventory สุทธิของทุก LP Tier"""
        orders: List[OrderEvent] = []
        
        if getattr(config, 'hedge_mode', 'smart') == 'always':
            signal: int = -1
        else:
            signal: int = int(current_tick['signal'])
            
        pct_change: float = float(current_tick['pct_change'])
        timestamp: datetime = current_tick['date']
        
        # รวม Inventory จากทุก Tier
        total_actual_eth: float = sum(lp.get_eth_inventory() for lp in self.lp_modules)
        current_short_size: float = self.perp.get_short_position_size()
        
        is_safety_trigger: bool = False
        
        # Safety Net Logic
        if signal == 1 and pct_change <= -config.safety_net_pct:
            signal = -1
            is_safety_trigger = True
            
        target_short_size: float = total_actual_eth if signal == -1 else 0.0
        diff: float = abs(target_short_size - current_short_size)
        
        is_flip: bool = (target_short_size == 0.0 and current_short_size > 0.0) or \
                        (target_short_size > 0.0 and current_short_size == 0.0)
        
        needs_adjustment: bool = False
        
        if is_flip:
            needs_adjustment = True
        elif target_short_size > 0.0:
            drift_ratio: float = diff / target_short_size if target_short_size > 0 else 0.0
            if drift_ratio > config.hedge_threshold:
                needs_adjustment = True
                
        if needs_adjustment:
            action_type: str = 'HEDGE_ON' if target_short_size > 0 else 'HEDGE_OFF'
            if not is_flip and target_short_size > 0:
                action_type = 'ADJUST_HEDGE'
                
            reason: str = "Multi-Tier Re-delta" if not is_flip else "Signal Flip"
            if is_safety_trigger: reason = "Safety Net Triggered"
            
            orders.append(OrderEvent(
                timestamp=timestamp,
                target_module='PERP',
                action=action_type,
                target_size=target_short_size,
                reason=reason
            ))
            
        return orders