"""
Multi-language static analysis dispatcher.
Runs appropriate linters based on file extension.
Falls back gracefully when external tools are missing.
"""

import json
import os
import py_compile
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

from logger import log

# ---------------------------------------------------------------------------
# Normalized issue shape
# ---------------------------------------------------------------------------
# {
#     "file": "relative/path",
#     "line": 42,
#     "column": 0,
#     "severity": "error" | "warning" | "info",
#     "message": "...",
#     "linter": "tool-name"
# }

# ---------------------------------------------------------------------------
# Tool availability cache
# ---------------------------------------------------------------------------
_TOOL_CACHE: Dict[str, bool] = {}


def _tool_available(name: str) -> bool:
    if name not in _TOOL_CACHE:
        _TOOL_CACHE[name] = subprocess.run(
            [name, "--version"],
            capture_output=True,
            text=True,
            shell=False,
        ).returncode in (0, 1)  # some linters exit 1 on --version
    return _TOOL_CACHE[name]


def _run_cmd(cmd: List[str], cwd: Optional[str] = None) -> Tuple[str, str, int]:
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=120)
        return proc.stdout, proc.stderr, proc.returncode
    except FileNotFoundError:
        return "", f"{cmd[0]} not found", 127
    except subprocess.TimeoutExpired:
        return "", f"{cmd[0]} timed out", 124
    except Exception as e:
        return "", str(e), 1


# ---------------------------------------------------------------------------
# Per-language linter implementations
# ---------------------------------------------------------------------------

def _lint_go(file_path: str, project_root: str) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    if not _tool_available("golangci-lint"):
        log.debug("golangci-lint not available, skipping Go static analysis")
        return issues

    work_dir = os.path.dirname(file_path) or project_root
    stdout, stderr, rc = _run_cmd(
        ["golangci-lint", "run", ".", "--out-format=json"],
        cwd=work_dir,
    )
    if not stdout:
        return issues
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        log.warning("Failed to parse golangci-lint JSON output")
        return issues

    for issue in data.get("Issues", []):
        pos = issue.get("Pos", {})
        issues.append(
            {
                "file": pos.get("Filename", file_path),
                "line": pos.get("Line", 0),
                "column": pos.get("Column", 0),
                "severity": "error" if issue.get("Severity", "") == "error" else "warning",
                "message": issue.get("Text", ""),
                "linter": issue.get("FromLinter", "golangci-lint"),
            }
        )
    return issues


def _lint_python(file_path: str, _project_root: str) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []

    # Always try built-in py_compile first (zero install, zero config)
    try:
        py_compile.compile(file_path, doraise=True)
    except py_compile.PyCompileError as e:
        issues.append(
            {
                "file": file_path,
                "line": getattr(e, "lineno", 0) or 0,
                "column": getattr(e, "offset", 0) or 0,
                "severity": "error",
                "message": str(e),
                "linter": "py_compile",
            }
        )

    # Try flake8 if available
    if _tool_available("flake8"):
        stdout, _stderr, _rc = _run_cmd(
            ["flake8", "--format=%(path)s:%(row)d:%(col)d:%(code)s:%(text)s", file_path]
        )
        for line in stdout.strip().splitlines():
            # path:line:col:code:text
            parts = line.split(":", 4)
            if len(parts) < 5:
                continue
            issues.append(
                {
                    "file": parts[0],
                    "line": int(parts[1]) if parts[1].isdigit() else 0,
                    "column": int(parts[2]) if parts[2].isdigit() else 0,
                    "severity": "error" if parts[3].startswith("E") else "warning",
                    "message": f"[{parts[3]}] {parts[4]}",
                    "linter": "flake8",
                }
            )

    # Try pylint if available
    if _tool_available("pylint"):
        stdout, _stderr, _rc = _run_cmd(
            [
                "pylint",
                "--output-format=json",
                "--disable=missing-docstring,invalid-name",
                file_path,
            ]
        )
        if stdout:
            try:
                pylint_issues = json.loads(stdout)
                for issue in pylint_issues:
                    issues.append(
                        {
                            "file": issue.get("path", file_path),
                            "line": issue.get("line", 0),
                            "column": issue.get("column", 0),
                            "severity": issue.get("type", "warning"),
                            "message": f"[{issue.get('symbol', '')}] {issue.get('message', '')}",
                            "linter": "pylint",
                        }
                    )
            except json.JSONDecodeError:
                pass

    return issues


def _lint_javascript(file_path: str, _project_root: str) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    if not _tool_available("eslint"):
        log.debug("eslint not available, skipping JS/TS static analysis")
        return issues

    stdout, _stderr, _rc = _run_cmd(
        ["eslint", "--format=json", file_path]
    )
    if not stdout:
        return issues
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return issues

    for file_result in data:
        for msg in file_result.get("messages", []):
            issues.append(
                {
                    "file": file_result.get("filePath", file_path),
                    "line": msg.get("line", 0),
                    "column": msg.get("column", 0),
                    "severity": "error" if msg.get("severity") == 2 else "warning",
                    "message": f"[{msg.get('ruleId', '')}] {msg.get('message', '')}",
                    "linter": "eslint",
                }
            )
    return issues


def _lint_rust(file_path: str, project_root: str) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    if not _tool_available("cargo"):
        log.debug("cargo not available, skipping Rust static analysis")
        return issues

    # clippy needs to run from the crate root; attempt project_root
    stdout, stderr, rc = _run_cmd(
        ["cargo", "clippy", "--message-format=json"],
        cwd=project_root,
    )
    output = stdout + stderr
    if not output:
        return issues

    for line in output.strip().splitlines():
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("reason") != "compiler-message":
            continue
        sp = msg.get("message", {}).get("spans", [{}])[0]
        # Only keep messages for the changed file
        if sp.get("file_name") and file_path.endswith(sp["file_name"]):
            issues.append(
                {
                    "file": sp.get("file_name", file_path),
                    "line": sp.get("line_start", 0),
                    "column": sp.get("column_start", 0),
                    "severity": msg["message"].get("level", "warning"),
                    "message": msg["message"].get("message", ""),
                    "linter": "clippy",
                }
            )
    return issues


def _lint_java(file_path: str, _project_root: str) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    # Try checkstyle if available
    if _tool_available("checkstyle"):
        stdout, _stderr, _rc = _run_cmd(
            ["checkstyle", "-f", "plain", file_path]
        )
        for line in stdout.strip().splitlines():
            # [SEVERITY] file_path:line: message
            if "]:" not in line:
                continue
            prefix, message = line.split(":", 1)
            severity = "warning"
            if "ERROR" in prefix:
                severity = "error"
            file_part = prefix.split("]", 1)[-1].strip()
            line_no = 0
            if ":" in file_part:
                file_part, line_str = file_part.rsplit(":", 1)
                line_no = int(line_str) if line_str.isdigit() else 0
            issues.append(
                {
                    "file": file_part,
                    "line": line_no,
                    "column": 0,
                    "severity": severity,
                    "message": message.strip(),
                    "linter": "checkstyle",
                }
            )
    return issues


def _lint_cpp(file_path: str, _project_root: str) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    if not _tool_available("cppcheck"):
        log.debug("cppcheck not available, skipping C/C++ static analysis")
        return issues

    stdout, _stderr, _rc = _run_cmd(
        [
            "cppcheck",
            "--enable=all",
            "--error-exitcode=0",
            "--template={file}:{line}:{column}:{severity}:{message}",
            file_path,
        ]
    )
    for line in stdout.strip().splitlines():
        if not line.startswith(file_path):
            continue
        parts = line.split(":", 4)
        if len(parts) < 5:
            continue
        issues.append(
            {
                "file": parts[0],
                "line": int(parts[1]) if parts[1].isdigit() else 0,
                "column": int(parts[2]) if parts[2].isdigit() else 0,
                "severity": parts[3],
                "message": parts[4],
                "linter": "cppcheck",
            }
        )
    return issues


# ---------------------------------------------------------------------------
# Dispatch map
# ---------------------------------------------------------------------------
_LINTER_MAP = {
    ".go": _lint_go,
    ".py": _lint_python,
    ".js": _lint_javascript,
    ".jsx": _lint_javascript,
    ".ts": _lint_javascript,
    ".tsx": _lint_javascript,
    ".rs": _lint_rust,
    ".java": _lint_java,
    ".c": _lint_cpp,
    ".cpp": _lint_cpp,
    ".cc": _lint_cpp,
    ".h": _lint_cpp,
    ".hpp": _lint_cpp,
}


def run_linter(file_path: str, project_root: str) -> List[Dict[str, Any]]:
    """Run the appropriate linter(s) for a single file."""
    _, ext = os.path.splitext(file_path)
    linter_fn = _LINTER_MAP.get(ext)
    if not linter_fn:
        log.debug(f"No linter configured for extension {ext}")
        return []
    try:
        return linter_fn(file_path, project_root)
    except Exception as e:
        log.warning(f"Linter error for {file_path}: {e}")
        return []


def run_all_linters(changed_files: List[str], project_root: str) -> List[Dict[str, Any]]:
    """Run linters for a list of changed files and collect all issues."""
    all_issues: List[Dict[str, Any]] = []
    for f in changed_files:
        abs_path = os.path.join(project_root, f)
        if not os.path.exists(abs_path):
            continue
        issues = run_linter(abs_path, project_root)
        # Normalize file paths to repo-relative for consistent reporting
        for issue in issues:
            issue_file = issue.get("file", "")
            if os.path.isabs(issue_file):
                rel = os.path.relpath(issue_file, project_root)
                issue["file"] = rel
            elif issue_file.startswith(abs_path):
                issue["file"] = f
        all_issues.extend(issues)
    return all_issues


def format_linter_report(issues: List[Dict[str, Any]]) -> str:
    """Format a normalized issue list into a markdown report."""
    if not issues:
        return "No static analysis errors found. (Code passed the linter)"

    report = "### Static Analysis Report (HARD TRUTH)\n"
    report += "The following issues were detected by the linter. **You MUST address them:**\n"

    for i, issue in enumerate(issues):
        if i >= 15:
            report += f"\n... and {len(issues) - 15} more issues truncated."
            break

        file_pos = f"{issue.get('file', 'unknown')}:{issue.get('line', 0)}"
        severity = issue.get("severity", "warning").upper()
        linter = issue.get("linter", "linter")
        message = issue.get("message", "Unknown error")

        report += f"{i+1}. [{linter}/{severity}] {file_pos}\n"
        report += f"   Error: {message}\n"

    return report


if __name__ == "__main__":
    test_path = "."
    if len(sys.argv) > 1:
        test_path = sys.argv[1]
    issues = run_linter(test_path, os.getcwd())
    print(format_linter_report(issues))
