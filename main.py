"""
Code Review Agent - Entry Point.

Usage:
  export LLM_API_KEY="your-key"
  export LLM_PROVIDER="kimi"  # default
  export PROJECT_ROOT="/path/to/repo"
  python main.py

Environment variables:
  LLM_PROVIDER        - Primary LLM provider (kimi, deepseek, claude, openai)
  LLM_API_KEY         - API key for primary provider
  LLM_MODEL           - Override default model
  SUB_LLM_PROVIDER    - Sub-agent provider (defaults to same as primary)
  SUB_LLM_API_KEY     - Sub-agent API key (defaults to primary)
  PROJECT_ROOT        - Path to git repository
  ENABLE_LINTER       - "true" or "false"
  OUTPUT_FORMAT       - "json" or "markdown"
  GIT_MODE            - "pr" (diff vs branch) or "patch" (HEAD commit)
  TARGET_BRANCH       - Target branch for PR mode
"""

import json
import os
import sys

sys.path.append("db")

from config import Config
from logger import log
from review_pipeline import ReviewPipeline


def main():
    # ---- Validate config ----
    if not Config.LLM_API_KEY:
        log.critical(
            "LLM_API_KEY is not set. Export it as an environment variable."
        )
        sys.exit(1)

    project_root = Config.PROJECT_ROOT
    if not os.path.isdir(os.path.join(project_root, ".git")):
        log.critical(f"Not a git repository: {project_root}")
        sys.exit(1)

    # ---- Run pipeline ----
    pipeline = ReviewPipeline(project_root)
    result = pipeline.run()

    # ---- Save report ----
    output_path = Config.OUTPUT_REPORT_PATH
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            if Config.OUTPUT_FORMAT == "json":
                json.dump(result, f, ensure_ascii=False, indent=2)
            else:
                f.write(_render_markdown(result))
        log.info(f"Report saved to {os.path.abspath(output_path)}")
    except Exception as e:
        log.error(f"Failed to write report: {e}")

    # ---- Console summary ----
    issues = result.get("issues", [])
    blockers = [i for i in issues if i.get("severity") == "BLOCKER"]
    warns = [i for i in issues if i.get("severity") == "WARN"]
    infos = len(issues) - len(blockers) - len(warns)

    print("\n" + "=" * 50)
    print(f"Verdict : {result.get('verdict', 'N/A')}")
    print(f"Summary : {result.get('summary', '')}")
    print(f"Issues  : {len(blockers)} BLOCKER | {len(warns)} WARN | {infos} INFO")
    meta = result.get("_meta", {})
    print(f"Model   : {meta.get('model', 'N/A')} | "
          f"Time: {meta.get('duration_sec', 'N/A')}s")
    print("=" * 50)

    # ---- Exit codes for CI/CD ----
    if blockers:
        sys.exit(1)
    if result.get("verdict") == "WARN":
        sys.exit(2)
    sys.exit(0)


def _render_markdown(result: dict) -> str:
    lines = ["# Code Review Report\n"]
    lines.append(f"**Verdict:** {result.get('verdict', 'N/A')}\n")
    lines.append(f"**Summary:** {result.get('summary', '')}\n")
    lines.append("## Issues\n")
    for issue in result.get("issues", []):
        sev = issue.get("severity", "INFO")
        cat = issue.get("category", "general")
        fp = issue.get("file", "unknown")
        line = issue.get("line", 0)
        lines.append(f"### [{sev}] {cat} - {fp}:{line}\n")
        lines.append(f"{issue.get('message', '')}\n")
        lines.append(f"**Suggestion:** {issue.get('suggestion', '')}\n")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
