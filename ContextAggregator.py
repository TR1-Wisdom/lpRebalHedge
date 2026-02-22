import os
from datetime import datetime

# --- Configuration ---
PROJECT_ROOT = "."  # ทางไปโฟลเดอร์โปรเจกต์ของคุณ
DEFAULT_DOCS_DIR = "docs"
# ระบุโฟลเดอร์ที่ต้องการรวม
INCLUDE_DIRS = ['core_logic', 'docs', 'tests']
# ระบุนามสกุลไฟล์ที่ต้องการ
EXTENSIONS = ['.py', '.md', '.sql']
# ไฟล์หรือโฟลเดอร์ที่ไม่เอา
EXCLUDE_FILES = ['aggregate_context.py'] # จะกรองไฟล์ output ออกอัตโนมัติภายหลัง

def aggregate_project():
    # --- Input custom filename for backup/versioning ---
    default_name = f"master_context_{datetime.now().strftime('%Y%m%d_%H%M')}.md"
    print(f"--- Context Aggregator for Gemini ---")
    print(f"Default filename: {default_name}")
    user_input = input("Enter output filename (Press Enter to use default, or type e.g., 'v1.md'): ").strip()
    
    filename = user_input if user_input else default_name
    # ตรวจสอบนามสกุลไฟล์
    if not filename.endswith('.md'):
        filename += '.md'
        
    output_path = os.path.join(DEFAULT_DOCS_DIR, filename)

    content = f"# PROJECT MASTER CONTEXT (Single Source of Truth)\n"
    content += f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    content += f"Version/Tag: {filename}\n\n"
    content += "เอกสารนี้รวม Code และ Logic ล่าสุดเพื่อให้ DPy ทำงานต่อได้อย่างแม่นยำ\n\n"

    file_count = 0
    for root, dirs, files in os.walk(PROJECT_ROOT):
        # กรองเฉพาะโฟลเดอร์ที่ต้องการ
        if not any(dir_path in root for dir_path in INCLUDE_DIRS):
            continue

        for file in files:
            # ไม่รวมไฟล์ output ของเราเอง และไฟล์ใน EXCLUDE_FILES
            if any(file.endswith(ext) for ext in EXTENSIONS) and \
               file not in EXCLUDE_FILES and \
               not file.startswith("master_context_"):
                
                file_path = os.path.join(root, file)
                relative_path = os.path.relpath(file_path, PROJECT_ROOT)
                
                content += f"## File: {relative_path}\n"
                content += "```" + (file.split('.')[-1] if '.' in file else '') + "\n"
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        content += f.read()
                    file_count += 1
                except Exception as e:
                    content += f"Error reading file: {str(e)}"
                content += "\n```\n\n"

    # สร้างโฟลเดอร์ docs ถ้ายังไม่มี
    if not os.path.exists(DEFAULT_DOCS_DIR):
        os.makedirs(DEFAULT_DOCS_DIR)

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print(f"\n[Success] Aggregated {file_count} files into: {output_path}")
    print(f"You can now upload this file to Gemini DPy's Knowledge Base.")

if __name__ == "__main__":
    aggregate_project()