"""
Parent-agent: Code Reviewer.
Uses a strong model to perform deep review based on:
- Static analysis results (hard truth)
- File summaries (context from sub-agent)
- Git diff (the actual changes)
- Team rules (hard constraints)
- PR / commit intent
"""

import json
import time
from typing import Any, Dict, List, Optional

from config import Config, get_llm_config
from llm_client import OpenAICompatibleClient, create_client
from logger import log


_REVIEW_SYSTEM_PROMPT = """You are an expert code reviewer and software architect.
Review the code DIFF in the context of the full files. Follow these rules strictly:

1. ONLY flag issues in lines that are part of the diff (added/modified).
2. Use the provided full-file summaries to understand surrounding context.
3. Treat static analysis results as HARD TRUTH. Do not contradict them.
4. Treat team rules as HARD CONSTRAINTS. Flag violations explicitly.
5. Treat impact analysis as CONTEXT: if a change has wide blast radius, be extra careful about breaking downstream callers.
6. Focus severity on: severe bugs, logic errors, security vulnerabilities, performance issues.
7. Do NOT nitpick style unless it violates a team rule.
8. Every issue must include a concrete fix suggestion or refactored code snippet.
9. Be concise. One issue per problem.

Output MUST be valid JSON with this exact schema:
{
  "verdict": "PASS" | "WARN" | "BLOCKER",
  "summary": "1-2 sentence overall assessment in Chinese",
  "issues": [
    {
      "severity": "BLOCKER" | "WARN" | "INFO",
      "category": "bug" | "security" | "performance" | "architecture" | "style",
      "file": "relative/path/to/file",
      "line": 42,
      "message": "Clear problem description in Chinese",
      "suggestion": "Concrete fix or code snippet in Chinese",
      "confidence": 0.95
    }
  ]
}"""


class CodeReviewer:
    """Strong parent-agent that performs the final code review."""

    def __init__(self, client: Optional[OpenAICompatibleClient] = None):
        cfg = get_llm_config()
        self.client = client or create_client(cfg)
        self.model = cfg["model"]

    def review(
        self,
        diff: str,
        file_summaries: List[Dict[str, Any]],
        static_analysis: str,
        team_rules: str,
        intent: str,
        impact_analysis: str = "",
    ) -> Dict[str, Any]:
        """Execute the review and return structured results."""

        summary_section = self._format_summaries(file_summaries)

        impact_section = f"\n### Impact Analysis (Blast Radius)\n{impact_analysis}\n\n" if impact_analysis else ""

        user_content = (
            f"### Business Intent / Commit Context\n{intent}\n\n"
            f"### Team Rules (Hard Constraints)\n{team_rules}\n\n"
            f"### Static Analysis Results (Hard Truth)\n{static_analysis}\n\n"
            f"{impact_section}"
            f"### Changed File Summaries (Context)\n{summary_section}\n\n"
            f"### Git Diff (Changes to Review)\n```diff\n{diff}\n```\n\n"
            f"Review the diff ONLY. Use summaries for context. Output JSON."
        )

        messages = [
            {"role": "system", "content": _REVIEW_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

        log.info(f"[Reviewer] Sending to {self.model}...")
        start = time.time()

        try:
            resp = self.client.chat(
                messages=messages,
                temperature=Config.REVIEW_TEMPERATURE,
                max_tokens=Config.MAX_REVIEW_TOKENS,
                response_format={"type": "json_object"},
            )
            duration = time.time() - start
            log.info(
                f"[Reviewer] Done in {duration:.1f}s | "
                f"tokens: {resp.usage.get('total_tokens', 'N/A')}"
            )

            result = json.loads(resp.content)
            result["_meta"] = {
                "model": self.model,
                "duration_sec": round(duration, 2),
                "tokens": resp.usage,
            }
            return result

        except json.JSONDecodeError:
            log.error("[Reviewer] JSON parse failed")
            return {
                "verdict": "WARN",
                "summary": "LLM returned non-JSON output. Manual review required.",
                "issues": [],
                "_meta": {
                    "model": self.model,
                    "error": "json_parse_failed",
                },
            }
        except Exception as e:
            log.error(f"[Reviewer] Failed: {e}")
            return {
                "verdict": "WARN",
                "summary": f"Review pipeline error: {e}",
                "issues": [],
                "_meta": {
                    "model": self.model,
                    "error": str(e),
                },
            }

    def _format_summaries(self, summaries: List[Dict[str, Any]]) -> str:
        lines = []
        for s in summaries:
            fp = s.get("file_path", "unknown")
            lines.append(f"**{fp}**")
            lines.append(f"- Purpose: {s.get('purpose', 'N/A')}")
            funcs = s.get("key_functions", [])
            if funcs:
                lines.append(f"- Functions: {', '.join(str(f) for f in funcs[:5])}")
            risks = s.get("risk_flags", [])
            if risks:
                lines.append(f"- Risks: {', '.join(str(r) for r in risks)}")
            lines.append("")
        return "\n".join(lines)
