import os
import sys
import json
import requests
import itertools
import time

# ================= 配置区域 =================

# 1. API Key 列表
API_KEY_LIST = [
    "use your api key" 
]

# 2. 模型名称
MODEL_NAME = "deepseek-chat" 

# 3. DeepSeek API 地址
API_URL = "https://api.deepseek.com/chat/completions"

# 4. 目标代码文件路径
TARGET_FILE_PATH = "/root/work/project-zero/off-prem-general/common-services/xc1p-cluster-automation/sourceCode/service/profile/checker.go"

# 5. [新增] 报告保存路径
OUTPUT_REPORT_PATH = "review_report.md"

# 6. MR 描述
MR_DESCRIPTION = """
Before creating the solution profile, all the parameters for creating the template which include firmware policy, config pattern, os profile will be obtained. 
We need to combine and check these parameters according to the rules in the flavor. 
If any of them do not meet the rules, the subsequent creation of the solution profile will be prevented.
"""

# ================= 核心逻辑 =================

key_cycle = itertools.cycle(API_KEY_LIST)

def get_next_key():
    return next(key_cycle)

def read_code_from_file(file_path):
    """读取文件内容"""
    try:
        if not os.path.exists(file_path):
            print(f"❌ 错误: 找不到文件: {file_path}")
            sys.exit(1)
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        print(f"❌ 读取文件失败: {e}")
        sys.exit(1)

def save_report_to_file(content, file_path):
    """[新增] 将内容保存到本地文件"""
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"✅ 报告已成功保存至文件: {os.path.abspath(file_path)}")
    except Exception as e:
        print(f"❌ 保存报告失败: {e}")

def analyze_with_deepseek(source_code):
    # 1. 获取 Key
    current_api_key = get_next_key()
    
    # 2. 设置 Headers
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {current_api_key}"
    }

    # 3. 构造 Prompt (加入了格式要求，方便后续查看)
    system_prompt = "You are an expert Golang Code Reviewer."
    user_prompt = f"""
    Please analyze this Go code based on the MR intent.

    ### MR Intent:
    {MR_DESCRIPTION}

    ### Code:
    ```go
    {source_code}
    ```

    Please verify if the code implements the checks for firmware policy, config pattern, and os profile correctly based on the intent.
    Verify logic, concurrency safety, and error handling.

    **Output Format Requirement:**
    Please output in standard Markdown format.
    Start with a summary verdict (PASS/BLOCKER/WARN).
    Then verify each requirement.
    Finally list any code quality issues.
    """

    # 4. 构造 Body
    payload = {
        "model": MODEL_NAME,
        "messages": [
            { "role": "system", "content": system_prompt },
            { "role": "user", "content": user_prompt }
        ],
        "stream": False,
        "temperature": 0.0 
    }

    print(f"🚀 [Key: ...{current_api_key[-4:]}] 正在请求 DeepSeek ({MODEL_NAME})...")

    try:
        # 5. 发起请求
        response = requests.post(API_URL, headers=headers, json=payload, timeout=120)
        
        # 6. 处理响应
        if response.status_code == 200:
            result = response.json()
            content = result['choices'][0]['message']['content']
            
            # [修改点] 先打印到控制台
            print("\n" + "="*30 + " DeepSeek 分析报告 " + "="*30 + "\n")
            print(content)
            print("\n" + "="*30 + " 结束 " + "="*30 + "\n")

            # [修改点] 再保存到文件
            save_report_to_file(content, OUTPUT_REPORT_PATH)
            
        else:
            print(f"❌ 请求失败 (Status {response.status_code}):")
            print(response.text)

    except Exception as e:
        print(f"❌ 网络请求异常: {e}")

if __name__ == "__main__":
    # 检查 requests 库
    try:
        import requests
    except ImportError:
        print("❌ 缺少 requests 库，请运行: pip install requests")
        sys.exit(1)

    # 运行
    code_content = read_code_from_file(TARGET_FILE_PATH)
    analyze_with_deepseek(code_content)