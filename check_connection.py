"""
สคริปต์ตรวจสอบ Inventory สำหรับพอร์ต ETH/USDC (V3)
อัปเดต: รองรับ Pool ETH/USDC บน Arbitrum ตามที่พาร์ทเนอร์ระบุ
"""

import os
from dotenv import load_dotenv
from src.utils.SafeWeb3 import SafeWeb3
from src.lp.uniswap_v3_manager import UniswapPositionManager

def audit_eth_usdc_pool():
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
    
    # 3. ที่อยู่ Pool ETH/USDC V3 ที่พาร์ทเนอร์เปิด
    POOL_ADDR = "0xC6962004f452bE9203591991D15f6b388e09E8D0" 

    print("="*55)
    print(f"📊 ตรวจสอบพอร์ต V3 (ETH/USDC)")
    print("="*55)
    
    try:
        res = manager.get_inventory_balances(TOKEN_ID, POOL_ADDR)
        
        if "error" in res:
            print(f"[!] ตรวจพบข้อผิดพลาด: {res['error']}")
            return

        print(f"[*] Position ID : {res['token_id']}")
        
        in_range_status = res.get('is_in_range', False)
        print(f"[*] สถานะ Range : {'🟢 In Range (ทำงานปกติ)' if in_range_status else '🔴 Out of Range (หลุดกรอบ)'}")
        
        print("-" * 35)
        print(f"💰 ยอดเงินจริงในพอร์ต (Total Inventory):")
        # บน Arbitrum: Token0 คือ WETH (18 Decimals) และ Token1 คือ USDC (6 Decimals)
        print(f"   [Active LP]   WETH: {res['active_amount0']:,.6f} | USDC: {res['active_amount1']:,.4f}")
        print(f"   [Uncollected] WETH: {res['owed_amount0']:,.6f} | USDC: {res['owed_amount1']:,.4f}")
        print("-" * 35)
        print(f"   ✅ TOTAL WETH : {res['total_amount0']:,.6f}")
        print(f"   ✅ TOTAL USDC : {res['total_amount1']:,.4f}")
        print("-" * 35)
        
        print(f"⚡ RPC Latency       : {res['latency_ms']} ms")
        print("="*55)
        print("[SUCCESS] ระบบ 'ตา' อ่านค่า ETH/USDC ได้สมบูรณ์แบบครับ!")
        
        # แจ้งเตือนเรื่อง Residual Risk สำหรับคู่เหรียญที่มีความผันผวน
        if res['latency_ms'] > 500:
            print(f"\n[QUANT INSIGHT] ⚠️ Latency อยู่ที่ {res['latency_ms']} ms")
            print("หากตลาดเหวี่ยงรุนแรง อาจมีความเสี่ยงจากข้อมูลขาค้าง (Residual Risk) แนะนำให้ติดตามค่านี้อย่างใกล้ชิดครับ")
        else:
            print("\n[QUANT INSIGHT] ⚡ Latency ต่ำกว่า 500 ms (ความเร็วระดับปลอดภัยสำหรับ Direct Control)")
            
    except Exception as e:
        print(f"[ERROR] เกิดข้อผิดพลาดอย่างไม่คาดคิด: {e}")

if __name__ == "__main__":
    audit_eth_usdc_pool()