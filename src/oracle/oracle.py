"""
src/oracle/oracle.py
เครื่องมือสร้างและดึงข้อมูลกราฟราคา (Market Data Oracle)
อัปเดต: Full Code Version (รองรับ 5m Resolution, CCXT Real Data, และ Local CSV Cache)
"""

import os
import time
import numpy as np
import pandas as pd
from dataclasses import dataclass
from datetime import datetime, timedelta
import ccxt

@dataclass
class OracleConfig:
    start_price: float = 2000.0
    days: int = 120
    annual_volatility: float = 0.5
    seed: int = 42
    timeframe: str = '5m' # รองรับ '1m', '5m', '15m', '1h'
    
    # ตั้งค่าสำหรับโหมดข้อมูลจริง (Real Market Data)
    use_real_data: bool = False
    symbol: str = 'ETH/USDT'
    exchange_id: str = 'binance'
    data_dir: str = 'data'

class OracleModule:
    def __init__(self):
        self.logger = self._setup_logger()

    def _setup_logger(self):
        import logging
        logger = logging.getLogger("OracleModule")
        if not logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter('%(levelname)s - %(message)s')
            handler.setFormatter(formatter)
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
        return logger

    def generate_data(self, config: OracleConfig) -> pd.DataFrame:
        """
        Main Entry Point สำหรับ BacktestEngine
        เลือกว่าจะ Generate กราฟจำลอง (GBM) หรือดึงกราฟจริง (CCXT)
        """
        if config.use_real_data:
            return self.fetch_real_data(config)
        else:
            return self.simulate_gbm_data(config)

    def simulate_gbm_data(self, config: OracleConfig) -> pd.DataFrame:
        """
        สร้างกราฟจำลองด้วยสมการ Geometric Brownian Motion (GBM)
        พร้อมรองรับ Resolution หลายระดับ และจัดทำข้อมูล OHLCV ครบถ้วน
        """
        if config.seed is not None:
            np.random.seed(config.seed)
            
        # กำหนดจำนวนแท่งต่อวันตาม Timeframe
        tf_map = {
            '1m': 24 * 60,
            '5m': 24 * 12,
            '15m': 24 * 4,
            '1h': 24,
            '1d': 1
        }
        steps_per_day = tf_map.get(config.timeframe, 24 * 12) # Default 5m
        freq_str = config.timeframe.replace('m', 'min').replace('h', 'h')
        if freq_str == '1min': freq_str = 'min'

        total_steps = config.days * steps_per_day
        dt = 1.0 / (365.0 * steps_per_day)
        
        mu = 0.05 # ค่าคาดหวังผลตอบแทน (Drift)
        sigma = config.annual_volatility
        
        # 1. คำนวณราคาปิด (Close Price) Vectorized
        shocks = np.random.normal(0, np.sqrt(dt), total_steps)
        log_returns = (mu - 0.5 * sigma**2) * dt + sigma * shocks
        close_prices = config.start_price * np.exp(np.cumsum(log_returns))
        close_prices = np.insert(close_prices, 0, config.start_price)
        
        # 2. สร้าง OHLCV สมบูรณ์ (Open, High, Low, Close, Volume)
        open_prices = np.roll(close_prices, 1)
        open_prices[0] = config.start_price
        
        # จำลองไส้เทียน (Wicks) อิงตาม Volatility
        high_prices = np.maximum(open_prices, close_prices) * (1 + np.abs(np.random.normal(0, sigma*0.1*dt, len(close_prices))))
        low_prices = np.minimum(open_prices, close_prices) * (1 - np.abs(np.random.normal(0, sigma*0.1*dt, len(close_prices))))
        volumes = np.random.uniform(100, 5000, len(close_prices))
        
        # 3. จัดการ Timestamp (ให้จบที่เวลาปัจจุบัน แล้วย้อนกลับไป)
        end_date = datetime.now().replace(minute=0, second=0, microsecond=0)
        start_date = end_date - timedelta(days=config.days)
        dates = pd.date_range(end=end_date, periods=len(close_prices), freq=freq_str)
        
        df = pd.DataFrame({
            'date': dates,
            'open': open_prices,
            'high': high_prices,
            'low': low_prices,
            'close': close_prices,
            'volume': volumes
        })
        
        # self.logger.info(f"Simulated {len(df)} GBM ticks (Resolution: {config.timeframe}, Vol: {sigma*100}%)")
        return df

    def fetch_real_data(self, config: OracleConfig) -> pd.DataFrame:
        """
        ดึงข้อมูลตลาดจริงจาก CEX ผ่าน CCXT (พร้อมระบบ Caching ลง Local)
        """
        os.makedirs(config.data_dir, exist_ok=True)
        safe_symbol = config.symbol.replace('/', '_')
        cache_file = os.path.join(config.data_dir, f"{config.exchange_id}_{safe_symbol}_{config.timeframe}_{config.days}d.csv")
        
        # โหลดจาก Cache ถ้ามี (ประหยัดเวลาและ Bandwidth)
        if os.path.exists(cache_file):
            self.logger.info(f"Loading real market data from cache: {cache_file}")
            df = pd.read_csv(cache_file, parse_dates=['date'])
            return df
            
        self.logger.info(f"Fetching {config.days} days of real data for {config.symbol} from {config.exchange_id}...")
        
        try:
            exchange_class = getattr(ccxt, config.exchange_id)
            exchange = exchange_class({'enableRateLimit': True})
        except AttributeError:
            raise ValueError(f"Exchange {config.exchange_id} is not supported by CCXT.")

        # คำนวณเวลาเริ่ม
        end_time = exchange.milliseconds()
        start_time = end_time - (config.days * 24 * 60 * 60 * 1000)
        
        all_ohlcv = []
        current_start = start_time
        
        # Loop ดึงข้อมูลจนครบ (เพราะ API มี Limit การดึงต่อครั้ง)
        while current_start < end_time:
            try:
                ohlcv = exchange.fetch_ohlcv(config.symbol, config.timeframe, since=current_start, limit=1000)
                if not ohlcv:
                    break
                    
                all_ohlcv.extend(ohlcv)
                current_start = ohlcv[-1][0] + 1 # ขยับเวลาไปแท่งถัดไป
                time.sleep(exchange.rateLimit / 1000) # Sleep ป้องกันโดน API แบน
                
            except Exception as e:
                self.logger.error(f"Error fetching data: {e}")
                break
                
        if not all_ohlcv:
            self.logger.warning("Failed to fetch real data. Falling back to GBM Simulation.")
            return self.simulate_gbm_data(config)
            
        df = pd.DataFrame(all_ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['date'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.drop(columns=['timestamp'], inplace=True)
        
        # บันทึก Cache ลง CSV
        df.to_csv(cache_file, index=False)
        self.logger.info(f"Saved real market data to cache: {cache_file}")
        
        return df