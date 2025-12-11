import subprocess
import json
import os
from logger import log, log_json  

def run_golangci_lint(target_path):
    """
    run golangci-lint 
    """
    if not os.path.exists(target_path):
        log.error(f"❌ Target path not found: {target_path}")
        return []

    if os.path.isfile(target_path):
        work_dir = os.path.dirname(target_path)
    else:
        work_dir = target_path

    log.info(f"🔍 Starting static analysis in: {work_dir}")
    
    # --out-format=json
    # --issues-exit-code=0
    cmd = ["golangci-lint", "run", ".", "--out-format=json"]
    
    try:
        result = subprocess.run(
            cmd, 
            cwd=work_dir, 
            capture_output=True, 
            text=True
        )
        
        output = result.stdout.strip()
        stderr = result.stderr.strip()

        if output:
            log_json("📋 [Linter Raw Output]", output, level=10) # 10 = DEBUG
        else:
            log.debug("📋 [Linter Raw Output]: (Empty)")

        if stderr:
            log.warning(f"⚠️ Linter Stderr: {stderr}")

        if not output:
            return []
            
        data = json.loads(output)
        issues = data.get("Issues", [])
        
        log.info(f"✅ Static Analysis finished. Found {len(issues)} issues.")
        return issues
        
    except FileNotFoundError:
        log.critical("❌ 'golangci-lint' command not found. Please install it first.")
        return []
    except json.JSONDecodeError:
        log.error(f"❌ Failed to parse Linter JSON. Raw content starts with: {output[:50]}...")
        return []
    except Exception as e:
        log.error(f"❌ Unexpected error running linter: {e}")
        return []

def format_linter_report(issues, target_filename=None):

    if not issues:
        return "No static analysis errors found. (Code passed the linter)"
    
    relevant_issues = issues
    if target_filename:
        base_name = os.path.basename(target_filename)
        relevant_issues = [i for i in issues if base_name in i['Pos']['Filename']]

    if not relevant_issues:
        return "No static analysis errors found in the target file."

    report = "### 🛑 Static Analysis Report (HARD TRUTH)\n"
    report += "The following issues were detected by the compiler/linter. **You MUST address them:**\n"
    
    for i, issue in enumerate(relevant_issues):
        if i >= 15: 
            report += f"\n... and {len(relevant_issues) - 15} more issues truncated."
            break
            
        file_pos = f"{issue['Pos']['Filename']}:{issue['Pos']['Line']}"
        linter_name = issue.get('FromLinter', 'linter')
        text = issue.get('Text', 'Unknown error')
        
        report += f"{i+1}. [{linter_name}] {file_pos}\n"
        report += f"   Error: {text}\n"
        
    return report

if __name__ == "__main__":
    test_path = "/root/work/project-zero/off-prem-general/common-services/xc1p-cluster-automation/sourceCode/service/profile/checker.go"
    issues = run_golangci_lint(test_path)
    print(format_linter_report(issues, test_path))