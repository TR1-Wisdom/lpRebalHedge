"""
risk_calculator.py
เครื่องมือประเมินความเสี่ยงและคำนวณเงินทุนสำรอง (Margin Requirement) ทางทฤษฎี
ใช้หลักการ Value at Risk (VaR) อิงตาม Geometric Brownian Motion

อัปเดต: รองรับตัวแปร rebalance_freq_days เพื่อเพิ่ม Capital Efficiency
"""

import os
import yaml
import math
from typing import Any
from scipy.stats import norm

def load_config(file_path: str = 'config.yaml') -> dict[str, Any]:
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"⚠️ ไม่พบไฟล์ตั้งค่า: {file_path}")
    with open(file_path, 'r', encoding='utf-8') as file:
        return yaml.safe_load(file)

def calculate_safe_margin():
    print("="*65)
    print("🛡️ QUANT LAB: Capital Efficiency & Margin Requirement")
    print("="*65)
    
    cfg = load_config()
    
    # 1. ดึงพารามิเตอร์
    lp_capital = float(cfg['capital']['lp_capital'])
    leverage = float(cfg['capital']['leverage'])
    
    # [PM ADDED]: อ่านค่ารอบการโอนเงินข้ามพอร์ต (ถ้าไม่ได้ตั้งใน yaml ให้ใช้ days_to_run แทน)
    rebal_freq_str = cfg.get('capital', {}).get('rebalance_freq_days', None)
    days_to_run = float(cfg['market']['days_to_run'])
    
    capital_rebalance_days = float(rebal_freq_str) if rebal_freq_str else days_to_run
    
    volatility = float(cfg['market']['annual_volatility'])
    
    # ใช้รอบการโอนเงิน เป็น Horizon ในการคำนวณความเสี่ยง (ยิ่งสั้น ยิ่งใช้เงินน้อย)
    years_horizon = capital_rebalance_days / 365.0 
    
    initial_short_size_usd = lp_capital / 2.0  # สัดส่วน Short เริ่มต้น (50% ของ LP)
    base_margin = initial_short_size_usd / leverage
    
    print(f"[*] LP Capital (On-chain) : ${lp_capital:,.2f}")
    print(f"[*] Leverage Target       : {leverage}x")
    print(f"[*] Annual Volatility     : {volatility*100:.0f}%")
    print(f"[*] Simulation Length     : {days_to_run} Days")
    print(f"[*] Capital Rebalance Freq: {capital_rebalance_days} Days (คำนวณความเสี่ยงที่กรอบเวลานี้)")
    print("-" * 65)
    print(f"[>] Base Margin Req.      : ${base_margin:,.2f} (เงินค้ำประกันขั้นต่ำสุด ณ วันแรก)")
    print("-" * 65)

    # 2. คำนวณ Value at Risk (VaR) ที่ระดับความมั่นใจต่างๆ
    confidence_levels = [0.90, 0.95, 0.99, 0.999]
    
    print(f"📊 คาดการณ์เงินสดที่จะถูกสูบออก (Max Drawdown in CEX)")
    print(f"   หากราคาตลาด 'พุ่งขึ้น' ภายในระยะเวลา {capital_rebalance_days} วัน:")
    print("")
    
    for conf in confidence_levels:
        z_score = norm.ppf(conf)
        
        # คำนวณราคาพุ่งสูงสุดที่เป็นไปได้ทางทฤษฎีในกรอบเวลา Rebalance Freq
        max_log_return = (volatility * math.sqrt(years_horizon)) * z_score
        max_price_multiplier = math.exp(max_log_return)
        max_up_pct = max_price_multiplier - 1.0
        
        # ขาดทุนฝั่ง Perp = กำไรฝั่ง LP = LP_Capital * (max_up_pct / 2)
        expected_perp_loss = lp_capital * (max_up_pct / 2.0)
        
        # เงินค้ำประกันที่ต้องมีทั้งหมด = Base Margin + เงินที่ขาดทุน
        safe_perp_capital = base_margin + expected_perp_loss
        
        print(f"🔹 ระดับความปลอดภัย {conf*100:.1f}% (Z={z_score:.2f})")
        print(f"   - ราคาอาจพุ่งไปถึง      : +{max_up_pct*100:.1f}%")
        print(f"   - ขาดทุนฝั่ง Short สะสม : -${expected_perp_loss:,.2f}")
        print(f"   👉 แนะนำวางเงินใน CEX  : ${safe_perp_capital:,.2f}")
        print("")

    print("="*65)
    print("💡 PM Note:")
    print("หากคุณตั้ง Capital Rebalance Freq สั้นลง (เช่น 30 วัน) คุณจะใช้เงิน CEX น้อยลง")
    print("แต่คุณ 'ต้อง' มีวินัยในการถอนกำไรจาก LP มาเติม CEX ทุกๆ 30 วันด้วยนะครับ!")
    print("="*65)

if __name__ == "__main__":
    calculate_safe_margin()