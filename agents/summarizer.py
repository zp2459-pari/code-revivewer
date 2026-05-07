"""
Sub-agent: File Summarizer.
Uses a cheap/lightweight model to read full file content and produce
a compact structural summary for the parent reviewer agent.
"""

import json
from typing import Dict, Optional

from config import Config, get_sub_llm_config
from llm_client import OpenAICompatibleClient, create_client
from logger import log


_FILE_SUMMARY_SYSTEM = """You are a code analysis assistant. Read a source code file and produce a compact JSON summary.

Rules:
- Output ONLY valid JSON. No markdown, no explanation.
- If the file is not code (config, markdown, generated), set purpose to "Non-code file".
- Focus on structural understanding, not line-by-line review.

Output schema:
{
  "purpose": "1-sentence description of what this file does",
  "key_functions": ["funcName: brief responsibility"],
  "dependencies": ["imported packages or internal modules"],
  "risk_flags": ["any security, concurrency, or side-effect concerns"],
  "lines_of_code": 0
}"""


class FileSummarizer:
    """Lightweight sub-agent that summarizes files to provide context for review."""

    def __init__(self, client: Optional[OpenAICompatibleClient] = None):
        cfg = get_sub_llm_config()
        self.client = client or create_client(cfg)
        self.model = cfg["model"]

    def summarize(self, file_path: str, content: str) -> Dict:
        """Generate a structured summary for a single file."""
        if not content or not content.strip():
            return self._empty_result(file_path)

        loc = content.count("\n")
        max_chars = 40000
        truncated = len(content) > max_chars
        display = content[:max_chars] if truncated else content

        messages = [
            {"role": "system", "content": _FILE_SUMMARY_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"File: {file_path}\n"
                    f"Lines: {loc}\n\n"
                    f"```\n{display}\n```\n\n"
                    f"Provide JSON summary only."
                ),
            },
        ]

        try:
            resp = self.client.chat(
                messages=messages,
                temperature=Config.SUB_TEMPERATURE,
                max_tokens=2048,
                response_format={"type": "json_object"},
            )
            summary = json.loads(resp.content)
            summary["file_path"] = file_path
            summary["truncated"] = truncated
            summary.setdefault("lines_of_code", loc)
            log.info(
                f"  [Summarizer] {file_path} "
                f"-> {len(summary.get('key_functions', []))} funcs"
            )
            return summary
        except json.JSONDecodeError:
            log.warning(
                f"  [Summarizer] JSON parse failed for {file_path}"
            )
            return self._fallback_result(file_path, loc, truncated, resp.content if 'resp' in dir() else "")
        except Exception as e:
            log.error(f"  [Summarizer] Error on {file_path}: {e}")
            return self._empty_result(file_path, loc)

    def _empty_result(self, file_path: str = "", loc: int = 0) -> Dict:
        return {
            "file_path": file_path,
            "purpose": "N/A",
            "key_functions": [],
            "dependencies": [],
            "risk_flags": [],
            "lines_of_code": loc,
            "truncated": False,
        }

    def _fallback_result(
        self, file_path: str, loc: int, truncated: bool, raw: str
    ) -> Dict:
        return {
            "file_path": file_path,
            "purpose": "Parse error",
            "key_functions": [],
            "dependencies": [],
            "risk_flags": [],
            "lines_of_code": loc,
            "truncated": truncated,
            "raw": raw,
        }
