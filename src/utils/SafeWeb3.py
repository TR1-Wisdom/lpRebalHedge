"""
src/utils/SafeWeb3.py
คลาสยูทิลิตี้สำหรับจัดการการเชื่อมต่อ Web3 แบบปลอดภัย (Retry & Failover)
"""

import time
from web3 import Web3

# --- การจัดการ Middleware สำหรับ Web3.py ทุกเวอร์ชัน (v5, v6, v7+) ---
try:
    # สำหรับ Web3.py v7+ (เปลี่ยนชื่อใหม่เพื่อความชัดเจน)
    from web3.middleware import ExtraDataToPOAMiddleware
    poa_middleware = ExtraDataToPOAMiddleware
except ImportError:
    try:
        # สำหรับ Web3.py v6
        from web3.middleware import geth_poa_middleware
        poa_middleware = geth_poa_middleware
    except ImportError:
        # Fallback กรณีไม่พบ Middleware (Arbitrum ปกติรันได้แม้ไม่มีตัวนี้)
        poa_middleware = None

class SafeWeb3:
    def __init__(self, rpc_urls):
        self.rpc_urls = rpc_urls
        self.current_rpc_index = 0
        self.w3 = self._connect()

    def _connect(self):
        """วนลูปหา RPC ที่ใช้งานได้จากรายการที่มี"""
        for i in range(len(self.rpc_urls)):
            idx = (self.current_rpc_index + i) % len(self.rpc_urls)
            url = self.rpc_urls[idx]
            try:
                w3 = Web3(Web3.HTTPProvider(url))
                if w3.is_connected():
                    self.current_rpc_index = idx
                    
                    # ฉีด Middleware เพื่อรองรับโครงสร้าง Block ของ Network L2
                    if poa_middleware:
                        w3.middleware_onion.inject(poa_middleware, layer=0)
                        
                    return w3
            except Exception as e:
                # ถ้าเชื่อมไม่ได้ให้เงียบไว้ แล้วไปลอง RPC ถัดไป
                continue
                
        raise Exception("❌ ไม่สามารถเชื่อมต่อกับ RPC ใดๆ ในรายการได้เลย กรุณาเช็คอินเทอร์เน็ตหรือ API Key")

    def call_contract_safe(self, contract_func, retries=3):
        """เรียกอ่านข้อมูลจาก Smart Contract แบบมีระบบลองใหม่ (Retry) เมื่อเครือข่ายแกว่ง"""
        for attempt in range(retries):
            try:
                return contract_func.call()
            except Exception as e:
                if attempt == retries - 1:
                    raise e
                time.sleep(2) # รอ 2 วินาทีก่อนลองใหม่