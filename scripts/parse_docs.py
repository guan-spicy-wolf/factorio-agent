import os
import time
import json
import requests
from bs4 import BeautifulSoup

API_KEY = "sk-sp-598be07d838442f1aa5a706004c51d3b"
BASE_URL = "https://coding.dashscope.aliyuncs.com/v1/chat/completions"
MODEL = "qwen3.5-plus"

INPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "files", "auxiliary")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "docs", "auxiliary")

os.makedirs(OUTPUT_DIR, exist_ok=True)

PROMPT = """
你是一个高级程序员，正在为一个能够通过 RCON 玩 Factorio 的 AI Agent 提取《Factorio Mod 制作文档》。
我将提供给你一段从官方官方 HTML 文档中提取出来的粗略 HTML/文本。

请你：
1. 去除所有与导航、网站脚印、多余空白相关的废话。
2. 将核心技术内容重新组织为**极度结构化、清晰的 Markdown 格式**。
3. 重点保留：生命周期(Data Lifecycle)、如何组织文件结构(Mod structure)、设置(Settings)、原型的依赖关系等核心技术细节。
4. 如果包含代码段，请用标准 Markdown 的代码块（```lua 或 ```json）正确高亮。
5. **只输出 Markdown 结果，不要说“好的”、“这是结果”等任何废话。**
"""

def call_llm(content):
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": f"请把下面的内容转换为结构化 Markdown:\n\n{content}"}
        ]
    }
    
    response = requests.post(BASE_URL, headers=headers, json=payload)
    response.raise_for_status()
    data = response.json()
    return data["choices"][0]["message"]["content"]

def clean_html(html_str):
    soup = BeautifulSoup(html_str, 'html.parser')
    # 尝试找到主要内容区域，Factorio API 文档通常主要内容在 body 或特定的 div 里
    # 简单清理掉常见的无关标签
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
        
    # 获取存文本或半结构的 HTML 给 LLM 会比较省 token
    # 这里我们保留一些基础格式（如 pre, code, h1-h6）
    return soup.get_text(separator="\n", strip=True)

def process_file(filepath):
    filename = os.path.basename(filepath)
    md_filename = filename.replace(".html", ".md")
    output_path = os.path.join(OUTPUT_DIR, md_filename)
    
    # 支持断点续传
    if os.path.exists(output_path):
        print(f"[{filename}] 已经处理过，跳过。")
        return
        
    print(f"[{filename}] 开始处理...")
    with open(filepath, 'r', encoding='utf-8') as f:
        html_content = f.read()
        
    cleaned_content = clean_html(html_content)
    
    # 简单的 token 截断防护（如果极其长可以报错或分段，但辅助文档一般不会几万字）
    if len(cleaned_content) > 30000:
        cleaned_content = cleaned_content[:30000] + "\n...[内容过长截断]..."
        
    retries = 3
    for attempt in range(retries):
        try:
            markdown_out = call_llm(cleaned_content)
            
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(markdown_out)
                
            print(f"[{filename}] 处理成功！ -> {md_filename}")
            time.sleep(1) # 防触发并发限制
            break
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:
                print(f"[{filename}] 触发限流 429，等待 5 秒后重试...")
                time.sleep(5)
            else:
                print(f"[{filename}] HTTP 报错: {e}")
                time.sleep(2)
        except Exception as e:
            print(f"[{filename}] 未知报错: {e}")
            time.sleep(2)
            
def main():
    if not os.path.exists(INPUT_DIR):
        print(f"输入目录不存在: {INPUT_DIR}")
        return
        
    html_files = [f for f in os.listdir(INPUT_DIR) if f.endswith(".html")]
    print(f"找到 {len(html_files)} 个辅助文档等待处理。")
    
    for html_file in html_files:
        process_file(os.path.join(INPUT_DIR, html_file))
        
    print("所有文档处理完毕！")

if __name__ == "__main__":
    main()
