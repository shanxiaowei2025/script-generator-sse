import asyncio
import os
from app.utils.pdf_generator import generate_script_pdf

async def main():
    task_id = "245a1d4d-a3be-4582-b8bf-f49880d52a23"
    
    # 合并第一集和第二集
    script_content = ""
    for ep_num in [1, 2]:
        file_path = f"app/storage/partial_contents/{task_id}_{ep_num}.txt"
        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
                script_content += content + "\n\n"
    
    # 生成PDF
    output_path = "/tmp/test_pdf_fixed.pdf"
    await generate_script_pdf(
        script_content=script_content,
        image_data={},
        output_path=output_path,
        task_id=task_id
    )
    print(f"PDF生成成功: {output_path}")

if __name__ == "__main__":
    asyncio.run(main()) 