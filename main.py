import os
import sys
import requests
import itertools
import time

sys.path.append("db") 

from logger import log 
import linter_runner
from graph_builder import GoKnowledgeGraph
from db import db


API_KEY_LIST = [
    "***************************"
]

RULES_JSON_PATH = "team_rules.json"
MODEL_NAME = "deepseek-chat" 
API_URL = "https://api.deepseek.com/chat/completions"

TARGET_FILE_PATH = "/root/work/project-zero/off-prem-general/common-services/xc1p-cluster-automation/sourceCode/service/profile/checker.go"
PROJECT_ROOT = "/root/work/project-zero/off-prem-general/common-services/xc1p-cluster-automation/sourceCode/service/profile/"
OUTPUT_REPORT_PATH = "review_report.md"

MR_DESCRIPTION = """
Before creating the solution profile, all the parameters for creating the template which include firmware policy, config pattern, os profile will be obtained. 
We need to combine and check these parameters according to the rules in the flavor. 
If any of them do not meet the rules, the subsequent creation of the solution profile will be prevented.
"""


key_cycle = itertools.cycle(API_KEY_LIST)

def get_next_key():
    return next(key_cycle)

def read_code_from_file(file_path):
    try:
        if not os.path.exists(file_path):
            log.critical(f"File not found: {file_path}")
            sys.exit(1)
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
            log.info(f"Read code file: {os.path.basename(file_path)} ({len(content)} bytes)")
            return content
    except Exception as e:
        log.critical(f"Failed to read file: {e}")
        sys.exit(1)

def save_report_to_file(content, file_path):
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        log.info(f"Report saved successfully to: {os.path.abspath(file_path)}")
    except Exception as e:
        log.error(f"Failed to save report: {e}")


def analyze_with_deepseek(source_code):
    current_api_key = get_next_key()
    
    log.info("Step 0/5: Initializing Database & Syncing Rules...")
    try:
        # 1. Create tables if missing
        db.init_tables()
        # 2. Load rules from JSON into DB (This updates the DB every time you run)
        db.sync_rules_from_json(RULES_JSON_PATH)
        # 3. Fetch rules from DB for the Prompt
        team_rules_str = db.get_active_rules()
    except Exception as e:
        log.error(f"Database sync error: {e}")
        team_rules_str = "No specific rules available (DB Error)."

    log.info("Step 1/5: Building Knowledge Graph...")
    kg = GoKnowledgeGraph(PROJECT_ROOT)
    kg.parse_project()
    
    target_filename = os.path.basename(TARGET_FILE_PATH)
    impact_report = ""
    
    for func_name, info in kg.definitions.items():
        if target_filename in info['file']:
            report = kg.format_graph_report(func_name)
            if report:
                impact_report += report + "\n"
    
    if not impact_report:
        impact_report = "No significant dependencies found."

    log.info("Step 2/5: Running Static Analysis...")
    linter_issues = linter_runner.run_golangci_lint(TARGET_FILE_PATH)
    linter_report_str = linter_runner.format_linter_report(linter_issues, TARGET_FILE_PATH)
    
    log.info("Step 3/5: Assembling Contextual Prompt...")
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {current_api_key}"
    }

    system_prompt = "You are an expert Golang Code Reviewer and Software Architect."
    
    user_prompt = f"""
    Please perform a comprehensive Code Review.
    
    ### 1. Business Intent (MR Description):
    "{MR_DESCRIPTION}"

    ### 2. Team Coding Standards (FROM DATABASE):
    {team_rules_str}
    (INSTRUCTION: Strictly enforce these rules. If code violates them, mark as BLOCKER.)

    ### 3. Static Analysis Report (HARD TRUTH):
    {linter_report_str}
    (INSTRUCTION: You MUST address these linter errors.)

    ### 4. Code Impact Analysis (Knowledge Graph):
    {impact_report}

    ### 5. Source Code:
    ```go
    {source_code}
    ```

    ### Output Format Requirements:
    Please output a strictly structured Markdown report:
    
    # Code Review Report
    
    ## Verdict
    Status: [PASS / WARN / BLOCKER]
    
    ## Verification Results
    - Static Analysis: ...
    - Team Rules Check: ...
    - Logic & Intent Check: ...
    
    ## Suggestions
    ...
    """

    payload = {
        "model": MODEL_NAME,
        "messages": [
            { "role": "system", "content": system_prompt },
            { "role": "user", "content": user_prompt }
        ],
        "stream": False,
        "temperature": 0.1
    }

    log.info(f"Step 4/5: Sending request to DeepSeek ({MODEL_NAME})...")
    start_time = time.time()

    try:
        response = requests.post(API_URL, headers=headers, json=payload, timeout=120)
        duration = time.time() - start_time
        
        if response.status_code == 200:
            result = response.json()
            content = result['choices'][0]['message']['content']
            
            usage = result.get("usage", {})
            log.info(f"Analysis Complete! Time: {duration:.2f}s | Tokens: {usage.get('total_tokens', 'N/A')}")
            
            save_report_to_file(content, OUTPUT_REPORT_PATH)
            
            verdict = "PASS"
            if "Status: [BLOCKER]" in content:
                verdict = "BLOCKER"
            elif "Status: [WARN]" in content:
                verdict = "WARN"
            
            log.info(f"Step 5/5: Saving record to MySQL (Verdict: {verdict})...")
            try:
                db.save_review_record(TARGET_FILE_PATH, verdict, content)
            except Exception as e:
                log.error(f"DB Save failed: {e}")

            print("\n" + "="*30 + " AI Review Result " + "="*30 + "\n")
            print(content[:500] + "\n... (see review_report.md for full details) ...") 
            
        else:
            log.error(f"API Request Failed: Status {response.status_code}")
            log.debug(f"Response Body: {response.text}")

    except Exception as e:
        log.critical(f"Network Exception: {e}")

if __name__ == "__main__":
    if "db" not in sys.path:
        sys.path.append("db")
        
    try:
        import requests
    except ImportError:
        print("Missing dependency. Please run: pip install requests pymysql")
        sys.exit(1)

    code_content = read_code_from_file(TARGET_FILE_PATH)
    analyze_with_deepseek(code_content)