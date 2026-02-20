"""
โมดูล Strategy (The Brain) สำหรับ Inventory LP Backtester

ประวัติการแก้ไข (Version Control):
- v1.0.1 (2026-02-20): [PM FIXED] เพิ่มพารามิเตอร์ hedge_mode เพื่อรองรับ 'always' hedge อย่างสมบูรณ์
"""

__version__ = "1.0.1"
__author__ = "LP-Rebal-Coding (Senior Quant Developer)"
__date__ = "2026-02-20"

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
    # สวิตช์ควบคุมโหมด: 'always' (Hedge ตลอดเวลา) หรือ 'smart' (ใช้ AI/EMA)
    hedge_mode: str = 'smart' 


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
        
        # [PM FIXED]: ถ้าระบุว่า 'always' ให้บังคับ Signal เป็น -1 (มองลงเพื่อ Short ตลอดกาล)
        if getattr(config, 'hedge_mode', 'smart') == 'always':
            signal: int = -1
        else:
            signal: int = int(current_tick['signal'])
            
        pct_change: float = float(current_tick['pct_change'])
        timestamp: datetime = current_tick['date']
        
        actual_eth: float = self.lp.get_eth_inventory()
        current_short: float = self.perp.get_short_position_size()
        
        is_safety_trigger: bool = False
        
        if signal == 1 and pct_change <= -config.safety_net_pct:
            signal = -1
            is_safety_trigger = True
            
        target_short: float = actual_eth if signal == -1 else 0.0
        diff: float = abs(target_short - current_short)
        is_flip: bool = (target_short == 0.0 and current_short > 0.0) or (target_short > 0.0 and current_short == 0.0)
        
        needs_adjustment: bool = False
        
        if is_flip:
            needs_adjustment = True
        elif target_short > 0.0:
            drift_ratio: float = diff / target_short if target_short > 0 else 0.0
            if drift_ratio > config.hedge_threshold:
                needs_adjustment = True
                
        if needs_adjustment:
            action_type: str = 'HEDGE_ON' if target_short > 0 else 'HEDGE_OFF'
            if not is_flip and target_short > 0:
                action_type = 'ADJUST_HEDGE'
                
            reason: str = "Always Hedge Mode" if config.hedge_mode == 'always' else ("Safety Net Triggered" if is_safety_trigger else "Signal Flip" if is_flip else "Threshold Drift")
            
            orders.append(OrderEvent(
                timestamp=timestamp,
                target_module='PERP',
                action=action_type,
                target_size=target_short,
                reason=reason
            ))
            
        return orders