"""
Review Pipeline: orchestrates the full review flow.

Flow:
  1. Git Discovery  -> changed files + diff + intent
  2. Static Analysis -> linter / hard truth (NO tokens spent)
  3. Team Rules      -> load from DB/JSON
  4. Sub-Agent       -> cheap model summarizes full files for context
  5. Parent-Agent    -> strong model reviews diff with all context
  6. Persistence     -> save structured result to DB + file
"""

import json
import os
import sys
from typing import Any, Dict, List

sys.path.append("db")

from config import Config
from logger import log
from git_helper import GitHelper
from linter_runner import format_linter_report, run_all_linters
from agents.summarizer import FileSummarizer
from agents.reviewer import CodeReviewer
from graph_builder import KnowledgeGraph
from db import db


class ReviewPipeline:
    def __init__(self, project_root: str):
        self.project_root = project_root
        self.git = GitHelper(project_root)
        self.summarizer = FileSummarizer()
        self.reviewer = CodeReviewer()
        self.kg = KnowledgeGraph(project_root) if Config.ENABLE_KG else None

    def run(self, target_branch: str = None) -> Dict[str, Any]:
        """Execute the full review pipeline."""

        # ===== Step 0: Git Discovery =====
        log.info("=" * 50)
        log.info("Step 0: Git Discovery")
        if not target_branch:
            target_branch = Config.TARGET_BRANCH or self.git.get_default_branch()

        if Config.GIT_MODE == "patch":
            # Gerrit-style: review latest commit only
            changed_files_rel = self.git.get_latest_commit_files()
            diff = self.git.get_latest_commit_diff()
        else:
            # PR-style: diff against target branch
            changed_files_rel = self.git.get_changed_files(target_branch)
            diff = self.git.get_project_diff(target_branch)

        if not changed_files_rel:
            log.warning("No changed files detected. Exiting.")
            return {"verdict": "PASS", "summary": "No changes to review.", "issues": []}

        if not diff or not diff.strip():
            log.warning("Empty diff. Exiting.")
            return {"verdict": "PASS", "summary": "Empty diff.", "issues": []}

        diff_truncated = False
        if len(diff) > Config.MAX_DIFF_LENGTH:
            log.warning(
                f"Diff truncated: {len(diff)} -> {Config.MAX_DIFF_LENGTH} chars"
            )
            diff = diff[: Config.MAX_DIFF_LENGTH]
            diff_truncated = True

        intent = self.git.get_pr_description_context()
        log.info(f"Files: {len(changed_files_rel)} | Diff chars: {len(diff)}")

        # ===== Step 1: Static Analysis (Hard Truth, Zero Tokens) =====
        log.info("Step 1: Static Analysis")
        static_report = "Static analysis disabled."
        linter_issues: List[Dict[str, Any]] = []

        if Config.ENABLE_LINTER:
            linter_issues = run_all_linters(changed_files_rel, self.project_root)
            if linter_issues:
                static_report = format_linter_report(linter_issues)
                log.info(f"Static analysis found {len(linter_issues)} issues")
            else:
                static_report = "No static analysis issues found."
                log.info("Static analysis clean")
        else:
            log.info("Linter disabled by config")

        # ===== Step 2: Team Rules =====
        log.info("Step 2: Loading Team Rules")
        try:
            db.init_tables()
            db.sync_rules_from_json(Config.RULES_JSON_PATH)
            team_rules = db.get_active_rules()
        except Exception as e:
            log.error(f"DB error: {e}")
            team_rules = "No team rules available."

        # ===== Step 2.5: Impact Radius (Blast Radius) =====
        log.info("Step 2.5: Impact Radius Analysis")
        impact_report = "Impact analysis disabled."
        impacted_files = []
        if self.kg and changed_files_rel:
            try:
                abs_changed = [
                    os.path.join(self.project_root, f)
                    for f in changed_files_rel
                ]
                # Incrementally update graph for changed files
                self.kg.parse_project(changed_files=abs_changed)
                impact_data = self.kg.get_impact_data(abs_changed)
                impact_report = self.kg.get_impact_report(abs_changed)
                impacted_files = impact_data.get("impacted_files", [])
                log.info(
                    f"Impact: {impact_data.get('seed_count', 0)} changed nodes, "
                    f"{impact_data.get('total_impacted', 0)} impacted nodes, "
                    f"{len(impacted_files)} impacted files"
                )
            except Exception as e:
                log.error(f"Impact analysis failed: {e}")
                impact_report = f"Impact analysis error: {e}"

        # ===== Step 3: Sub-Agent Summarization (Cheap) =====
        log.info("Step 3: File Summarization (Sub-Agent)")
        summaries = []
        for f in changed_files_rel:
            abs_path = os.path.join(self.project_root, f)
            if not os.path.exists(abs_path):
                continue
            try:
                with open(abs_path, "r", encoding="utf-8", errors="ignore") as fh:
                    content = fh.read()
                summary = self.summarizer.summarize(f, content)
                summaries.append(summary)
            except Exception as e:
                log.warning(f"Failed to summarize {f}: {e}")

        # ===== Step 4: Parent-Agent Review (Strong) =====
        log.info("Step 4: Code Review (Parent-Agent)")
        review_result = self.reviewer.review(
            diff=diff,
            file_summaries=summaries,
            static_analysis=static_report,
            team_rules=team_rules,
            intent=intent,
            impact_analysis=impact_report,
        )

        # Enrich result with pipeline metadata
        review_result["static_analysis"] = {
            "enabled": Config.ENABLE_LINTER,
            "issues_found": len(linter_issues),
        }
        review_result["files_reviewed"] = changed_files_rel
        review_result["diff_truncated"] = diff_truncated

        # ===== Step 5: Persistence =====
        log.info("Step 5: Saving Results")
        try:
            verdict = review_result.get("verdict", "WARN")
            db.save_review_record(
                "GIT_DIFF_BATCH", verdict, json.dumps(review_result, ensure_ascii=False)
            )
        except Exception as e:
            log.error(f"DB save failed: {e}")

        return review_result
