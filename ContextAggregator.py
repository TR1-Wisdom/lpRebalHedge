import os
from datetime import datetime

# --- Configuration ---
# กำหนดชื่อโครงการที่นี่ เพื่อให้ชื่อไฟล์ output ไม่ซ้ำกันในแต่ละโปรเจกต์
DEFAULT_PROJECT_NAME = "lpReablHedge" 

PROJECT_ROOT = "."  # เริ่มต้นที่โฟลเดอร์ปัจจุบัน
DEFAULT_DOCS_DIR = "docs"

# ระบุนามสกุลไฟล์ที่ต้องการ (คุมเฉพาะประเภทไฟล์ตามที่ PD ต้องการ)
EXTENSIONS = ['.py', '.md', '.sql']

# โฟลเดอร์ที่ไม่ต้องการสแกน (เพื่อความสะอาดของข้อมูลและประสิทธิภาพ)
EXCLUDE_DIRS = {'.git', '__pycache__', '.venv', 'node_modules', '.vscode', 'dist', 'build'}

# ไฟล์ที่ไม่ต้องการรวม
EXCLUDE_FILES = {'aggregate_context.py', 'ContextAggregator.py'}

def aggregate_project():
    # สร้าง timestamp สำหรับชื่อไฟล์
    timestamp = datetime.now().strftime('%Y%m%d_%H%M')
    
    # --- ปรับปรุงการสร้างชื่อไฟล์: เอาชื่อโครงการขึ้นก่อนตามต้องการ ---
    # รูปแบบใหม่: lpReablHedge_Context_20260222_1659.md
    default_name = f"{DEFAULT_PROJECT_NAME}_Context_{timestamp}.md"
    
    print(f"--- Context Aggregator for Gemini (Auto-Recursive Mode) ---")
    print(f"Project Name: {DEFAULT_PROJECT_NAME}")
    print(f"Scanning from: {os.path.abspath(PROJECT_ROOT)}")
    print(f"Default filename: {default_name}")
    
    user_input = input("Enter output filename (Press Enter for default): ").strip()
    
    filename = user_input if user_input else default_name
    if not filename.endswith('.md'):
        filename += '.md'
        
    output_path = os.path.join(DEFAULT_DOCS_DIR, filename)

    # เตรียมเนื้อหาไฟล์
    content = f"# PROJECT MASTER CONTEXT (Single Source of Truth)\n"
    content += f"Project: {DEFAULT_PROJECT_NAME}\n"
    content += f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    content += f"Version/Tag: {filename}\n\n"
    content += "เอกสารนี้รวม Code และ Logic ล่าสุดจากทุกโฟลเดอร์ย่อยเพื่อให้ DPy ทำงานต่อได้อย่างแม่นยำ\n\n"

    file_count = 0
    # เริ่มการสแกนแบบ Recursive ทั้งหมด
    for root, dirs, files in os.walk(PROJECT_ROOT):
        # ตัดโฟลเดอร์ที่ไม่ต้องการออกจากการสแกน
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]

        for file in files:
            # กรองตามนามสกุลไฟล์ และข้ามไฟล์ที่ขึ้นต้นด้วยชื่อโครงการตามด้วย _Context_ หรืออยู่ในรายการยกเว้น
            if any(file.endswith(ext) for ext in EXTENSIONS) and \
               file not in EXCLUDE_FILES and \
               not file.startswith(f"{DEFAULT_PROJECT_NAME}_Context_") and \
               not file.startswith("master_context_"): # กันเหนียวไฟล์เก่า
                
                file_path = os.path.join(root, file)
                relative_path = os.path.relpath(file_path, PROJECT_ROOT)
                
                # ข้ามไฟล์ที่อยู่ในโฟลเดอร์ output เองเพื่อป้องกันการเขียนทับซ้ำซ้อน
                if relative_path.startswith(DEFAULT_DOCS_DIR + os.sep) and file == filename:
                    continue

                content += f"## File: {relative_path}\n"
                content += "```" + (file.split('.')[-1] if '.' in file else '') + "\n"
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        content += f.read()
                    file_count += 1
                except Exception as e:
                    content += f"Error reading file {relative_path}: {str(e)}"
                content += "\n```\n\n"

    # สร้างโฟลเดอร์เก็บเอกสารถ้ายังไม่มี
    if not os.path.exists(DEFAULT_DOCS_DIR):
        os.makedirs(DEFAULT_DOCS_DIR)

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print(f"\n[Success] Scanned all directories.")
    print(f"Aggregated {file_count} files into: {output_path}")

if __name__ == "__main__":
    aggregate_project()