import os
import sys
import requests
import itertools
import time

# Ensure 'db' directory is in path for module imports
sys.path.append("db") 

from logger import log 
import linter_runner
from graph_builder import GoKnowledgeGraph
from db import db
from git_helper import GitHelper 

# ================= CONFIGURATION =================
API_KEY_LIST = [
    "sk-****************************************" 
]

RULES_JSON_PATH = "team_rules.json"
MODEL_NAME = "deepseek-chat" 
API_URL = "https://api.deepseek.com/chat/completions"

# Project Root Directory
PROJECT_ROOT = "/root/work/project-zero/off-prem-general/common-services/xc1p-cluster-automation"
OUTPUT_REPORT_PATH = "review_report.md"

DEFAULT_MR_DESCRIPTION = """
Generic Refactor or Update. Please infer intent from code changes.
"""

# ================= UTILS =================
key_cycle = itertools.cycle(API_KEY_LIST)

def get_next_key():
    return next(key_cycle)

def save_report_to_file(content, file_path):
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        log.info(f"Report saved successfully to: {os.path.abspath(file_path)}")
    except Exception as e:
        log.error(f"Failed to save report: {e}")

# ================= MAIN LOGIC =================
def analyze_changes_with_deepseek():
    current_api_key = get_next_key()
    
    # --- Step 0: Git Analysis ---
    log.info("Step 0/6: Analyzing Git Repository...")
    try:
        git = GitHelper(PROJECT_ROOT)
        
        # 1. Get full project diff
        project_diff = git.get_project_diff()
        if not project_diff or len(project_diff.strip()) == 0:
            log.warning("No changes detected in Git. Exiting...")
            return

        # 2. Get list of changed files
        changed_files_rel = git.get_changed_files()
        changed_files_abs = [os.path.join(PROJECT_ROOT, f) for f in changed_files_rel]
        
        # 3. Get commit context
        git_intent = git.get_pr_description_context()
        mr_intent = git_intent if git_intent else DEFAULT_MR_DESCRIPTION
        
        log.info(f"Detected {len(changed_files_rel)} changed files.")
    except Exception as e:
        log.critical(f"Git Analysis Failed: {e}")
        sys.exit(1)

    # --- Step 1: Database & Rules ---
    log.info("Step 1/6: Initializing Database & Syncing Rules...")
    try:
        db.init_tables()
        db.sync_rules_from_json(RULES_JSON_PATH)
        team_rules_str = db.get_active_rules()
    except Exception as e:
        log.error(f"Database sync error: {e}")
        team_rules_str = "No specific rules available (DB Error)."

    # --- Step 2: Knowledge Graph & Impact Analysis ---
    log.info("Step 2/6: Building Knowledge Graph & Impact Analysis...")
    kg = GoKnowledgeGraph(PROJECT_ROOT)
    
    # NOTE: If you updated graph_builder.py to support incremental updates, 
    # uncomment the line below:
    # kg.parse_project(changed_files=changed_files_abs)
    kg.parse_project()
    
    impact_report = ""
    affected_functions = []

    # 1. Pre-filter affected functions to avoid performance issues
    log.info("🔍 Identifying affected functions...")
    for func_name, info in kg.definitions.items():
        # Check if the function's file is in the changed files list
        for changed_file in changed_files_rel:
            if changed_file in info['file']:
                affected_functions.append(func_name)
                break
    
    log.info(f"📊 Found {len(affected_functions)} affected functions. Generating impact report...")

    # 2. Generate report with a safety limit (Circuit Breaker)
    max_analyze_limit = 10 
    
    for i, func_name in enumerate(affected_functions):
        if i >= max_analyze_limit:
            log.warning(f"⚠️ Limit reached! Skipping remaining {len(affected_functions) - i} functions.")
            impact_report += f"\n... (Skipped remaining functions due to limit) ...\n"
            break

        log.info(f"   [{i+1}/{len(affected_functions)}] Analyzing impact for: {func_name}")
        try:
            report = kg.format_graph_report(func_name)
            if report:
                impact_report += report + "\n"
        except Exception as e:
            log.error(f"   ❌ Error analyzing {func_name}: {e}")

    if not impact_report:
        impact_report = "No significant dependency impact detected based on changed functions."

    # --- Step 3: Static Analysis ---
    log.info("Step 3/6: Running Static Analysis on Changed Files...")
    linter_report_str = ""
    issue_count = 0
    
    for file_path in changed_files_abs:
        if file_path.endswith(".go") and os.path.exists(file_path):
            log.info(f"Linting: {os.path.basename(file_path)}")
            issues = linter_runner.run_golangci_lint(file_path)
            if issues:
                linter_report_str += linter_runner.format_linter_report(issues, file_path) + "\n"
                issue_count += len(issues)
    
    if not linter_report_str:
        linter_report_str = "No static analysis issues found in changed files."

    # --- Step 4: Prompt Assembly ---
    log.info("Step 4/6: Assembling Contextual Prompt...")
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {current_api_key}"
    }

    system_prompt = "You are an expert Golang Code Reviewer and Software Architect."
    
    user_prompt = f"""
    Please perform a comprehensive Code Review based on the following Git Diff.
    
    ### 1. Business Intent (Commit Context):
    "{mr_intent}"

    ### 2. Team Coding Standards (FROM DATABASE):
    {team_rules_str}
    (INSTRUCTION: Strictly enforce these rules. If code violates them, mark as BLOCKER.)

    ### 3. Static Analysis Report (HARD TRUTH):
    {linter_report_str}
    (INSTRUCTION: You MUST address these linter errors if they appear in the diff.)

    ### 4. Code Impact Analysis (Knowledge Graph):
    {impact_report}

    ### 5. Git Diff (The Changes):
    ```diff
    {project_diff}
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
    
    ## Suggestions (Git Patch or Code Snippets)
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

    # --- Step 5: LLM Request ---
    log.info(f"Step 5/6: Sending request to DeepSeek ({MODEL_NAME})...")
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
            
            # --- Step 6: Save Result ---
            verdict = "PASS"
            if "Status: [BLOCKER]" in content:
                verdict = "BLOCKER"
            elif "Status: [WARN]" in content:
                verdict = "WARN"
            
            log.info(f"Step 6/6: Saving record to MySQL (Verdict: {verdict})...")
            try:
                # Using a generic batch name for diff reviews
                db.save_review_record("GIT_DIFF_BATCH", verdict, content)
            except Exception as e:
                log.error(f"DB Save failed: {e}")

            print("\n" + "="*30 + " AI Review Result " + "="*30 + "\n")
            print(content[:800] + "\n... (see review_report.md for full details) ...") 
            
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

    analyze_changes_with_deepseek()