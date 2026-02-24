"""
check_connection.py
สคริปต์ตรวจสอบ Inventory สำหรับพอร์ต USDC/USDT (V3)
อัปเดต: จับคู่คีย์ is_in_range ให้ตรงกับ Manager
"""

import os
from dotenv import load_dotenv
from src.utils.SafeWeb3 import SafeWeb3
from src.lp.uniswap_v3_manager import UniswapPositionManager

def audit_stable_pool():
    load_dotenv()
    
    # 1. เชื่อมต่อ RPC
    alchemy_url = os.getenv("ALCHEMY_RPC_URL")
    if not alchemy_url:
        print("[ERROR] ไม่พบ ALCHEMY_RPC_URL ในไฟล์ .env")
        return
        
    sw3 = SafeWeb3([alchemy_url])
    manager = UniswapPositionManager(sw3)
    
    # 2. ตั้งค่า Token ID (ดึงจาก .env)
    TOKEN_ID = int(os.getenv("LP_TOKEN_ID", "0"))
    if TOKEN_ID == 0:
        print("[!] กรุณาระบุ LP_TOKEN_ID ในไฟล์ .env ก่อนครับ")
        return
    
    # 3. ที่อยู่ Pool USDC/USDT V3 0.01%
    STABLE_POOL_ADDR = "0xbE3aD6a5669Dc0B8b12FeBC03608860C31E2eef6" 

    print("="*55)
    print(f"📊 ตรวจสอบพอร์ต V3 Stablecoin (USDC/USDT)")
    print("="*55)
    
    try:
        res = manager.get_inventory_balances(TOKEN_ID, STABLE_POOL_ADDR)
        
        if "error" in res:
            print(f"[!] ตรวจพบข้อผิดพลาด: {res['error']}")
            return

        print(f"[*] Position ID : {res['token_id']}")
        
        # [FIXED] เรียกใช้คีย์ is_in_range (ป้องกัน Key Error ด้วย .get())
        in_range_status = res.get('is_in_range', False)
        print(f"[*] สถานะ Range : {'🟢 In Range (ทำงานปกติ)' if in_range_status else '🔴 Out of Range (หลุดกรอบ)'}")
        
        print("-" * 35)
        print(f"💰 ยอดเงินจริงในพอร์ต (Total Inventory):")
        print(f"   [Active LP]   USDC: {res['active_amount0']:,.4f} | USDT: {res['active_amount1']:,.4f}")
        print(f"   [Uncollected] USDC: {res['owed_amount0']:,.4f} | USDT: {res['owed_amount1']:,.4f}")
        print("-" * 35)
        print(f"   ✅ TOTAL USDC : {res['total_amount0']:,.4f}")
        print(f"   ✅ TOTAL USDT : {res['total_amount1']:,.4f}")
        print("-" * 35)
        
        total_value = res['total_amount0'] + res['total_amount1']
        print(f"💵 มูลค่ารวมโดยประมาณ : ${total_value:,.4f}")
        print(f"⚡ RPC Latency       : {res['latency_ms']} ms")
        print("="*55)
        print("[SUCCESS] ระบบ 'ตา' อ่านค่าจาก V3 ได้สมบูรณ์แบบครับ!")
        
    except Exception as e:
        print(f"[ERROR] เกิดข้อผิดพลาดอย่างไม่คาดคิด: {e}")

if __name__ == "__main__":
    audit_stable_pool()