"""
Code Knowledge Graph with Tree-sitter AST parsing and SQLite storage.

Multi-language support via tree-sitter-language-pack:
  Go, Python, JavaScript/TypeScript/TSX, Rust, Java, C/C++,
  C#, Ruby, PHP, Swift, Kotlin, Scala, Dart, R, and more.

Inspired by code-review-graph (github.com/tirth8205/code-review-graph):
- Tree-sitter AST extraction for functions, methods, classes, structs, calls, imports
- SQLite graph storage with WAL mode
- Impact Radius via SQLite recursive CTE (Blast Radius analysis)
- Incremental updates via SHA-256 file hashing
"""

import hashlib
import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from logger import log

# ---------------------------------------------------------------------------
# Tree-sitter setup (graceful fallback to regex)
# ---------------------------------------------------------------------------

TS_AVAILABLE = False
_ts_parsers: Dict[str, Any] = {}

try:
    from tree_sitter_language_pack import get_parser

    TS_AVAILABLE = True
    log.info("Tree-sitter language pack available")
except Exception as e:
    log.warning(
        "tree-sitter-language-pack not available (%s). "
        "Install: pip install tree-sitter-language-pack. "
        "Falling back to regex parser.",
        e,
    )


def _get_ts_parser(lang: str) -> Optional[Any]:
    """Lazy-load and cache tree-sitter parsers."""
    if not TS_AVAILABLE:
        return None
    if lang not in _ts_parsers:
        try:
            _ts_parsers[lang] = get_parser(lang)
        except Exception as e:
            log.warning(f"Failed to load parser for '{lang}': {e}")
            return None
    return _ts_parsers.get(lang)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class NodeInfo:
    kind: str  # File, Function, Method, Struct, Class, Interface, Trait, Import, Type
    name: str
    file_path: str
    line_start: int
    line_end: int
    qualified_name: str = ""
    parent_name: Optional[str] = None
    params: Optional[str] = None
    return_type: Optional[str] = None
    is_test: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.qualified_name:
            if self.kind == "File":
                self.qualified_name = self.file_path
            elif self.parent_name:
                self.qualified_name = (
                    f"{self.file_path}::{self.parent_name}.{self.name}"
                )
            else:
                self.qualified_name = f"{self.file_path}::{self.name}"


@dataclass
class EdgeInfo:
    kind: str  # CALLS, IMPORTS_FROM, CONTAINS, INHERITS, IMPLEMENTS, TESTED_BY
    source: str  # qualified_name
    target: str  # qualified_name or bare name
    file_path: str
    line: int = 0
    extra: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# SQLite Schema
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    qualified_name TEXT NOT NULL UNIQUE,
    file_path TEXT NOT NULL,
    line_start INTEGER,
    line_end INTEGER,
    parent_name TEXT,
    params TEXT,
    return_type TEXT,
    is_test INTEGER DEFAULT 0,
    file_hash TEXT,
    extra TEXT DEFAULT '{}',
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    source_qualified TEXT NOT NULL,
    target_qualified TEXT NOT NULL,
    file_path TEXT NOT NULL,
    line INTEGER DEFAULT 0,
    extra TEXT DEFAULT '{}',
    updated_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_nodes_file ON nodes(file_path);
CREATE INDEX IF NOT EXISTS idx_nodes_kind ON nodes(kind);
CREATE INDEX IF NOT EXISTS idx_nodes_qualified ON nodes(qualified_name);
CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_qualified);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_qualified);
CREATE INDEX IF NOT EXISTS idx_edges_kind ON edges(kind);
CREATE INDEX IF NOT EXISTS idx_edges_target_kind ON edges(target_qualified, kind);
CREATE INDEX IF NOT EXISTS idx_edges_file ON edges(file_path);
"""


# ---------------------------------------------------------------------------
# GraphStore
# ---------------------------------------------------------------------------


class GraphStore:
    """SQLite-backed code knowledge graph."""

    def __init__(self, db_path: str = "code_graph.db"):
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def remove_file_data(self, file_path: str) -> None:
        self._conn.execute("DELETE FROM nodes WHERE file_path = ?", (file_path,))
        self._conn.execute("DELETE FROM edges WHERE file_path = ?", (file_path,))
        self._conn.commit()

    def store_file_nodes_edges(
        self,
        file_path: str,
        nodes: List[NodeInfo],
        edges: List[EdgeInfo],
        fhash: str = "",
    ) -> None:
        now = time.time()
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            self.remove_file_data(file_path)
            for node in nodes:
                extra = json.dumps(node.extra) if node.extra else "{}"
                self._conn.execute(
                    """INSERT INTO nodes
                       (kind, name, qualified_name, file_path, line_start, line_end,
                        parent_name, params, return_type, is_test, file_hash, extra, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(qualified_name) DO UPDATE SET
                         kind=excluded.kind, name=excluded.name,
                         file_path=excluded.file_path, line_start=excluded.line_start,
                         line_end=excluded.line_end, parent_name=excluded.parent_name,
                         params=excluded.params, return_type=excluded.return_type,
                         is_test=excluded.is_test, file_hash=excluded.file_hash,
                         extra=excluded.extra, updated_at=excluded.updated_at
                    """,
                    (
                        node.kind,
                        node.name,
                        node.qualified_name,
                        node.file_path,
                        node.line_start,
                        node.line_end,
                        node.parent_name,
                        node.params,
                        node.return_type,
                        int(node.is_test),
                        fhash,
                        extra,
                        now,
                    ),
                )
            for edge in edges:
                extra = json.dumps(edge.extra) if edge.extra else "{}"
                self._conn.execute(
                    """INSERT INTO edges
                       (kind, source_qualified, target_qualified, file_path, line, extra, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        edge.kind,
                        edge.source,
                        edge.target,
                        edge.file_path,
                        edge.line,
                        extra,
                        now,
                    ),
                )
            self._conn.commit()
        except BaseException:
            self._conn.rollback()
            raise

    def get_nodes_by_file(self, file_path: str) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM nodes WHERE file_path = ?", (file_path,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_files(self) -> List[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT file_path FROM nodes WHERE kind = 'File'"
        ).fetchall()
        return [r["file_path"] for r in rows]

    def get_stats(self) -> Dict[str, Any]:
        total_nodes = self._conn.execute(
            "SELECT COUNT(*) FROM nodes"
        ).fetchone()[0]
        total_edges = self._conn.execute(
            "SELECT COUNT(*) FROM edges"
        ).fetchone()[0]
        return {"total_nodes": total_nodes, "total_edges": total_edges}

    def get_impact_radius(
        self,
        changed_files: List[str],
        max_depth: int = 2,
        max_nodes: int = 200,
    ) -> Dict[str, Any]:
        """BFS from changed files via SQLite recursive CTE."""
        if not changed_files:
            return {
                "changed_nodes": [],
                "impacted_nodes": [],
                "impacted_files": [],
                "truncated": False,
                "total_impacted": 0,
                "seed_count": 0,
            }

        seeds: Set[str] = set()
        for f in changed_files:
            nodes = self.get_nodes_by_file(f)
            for n in nodes:
                seeds.add(n["qualified_name"])

        if not seeds:
            return {
                "changed_nodes": [],
                "impacted_nodes": [],
                "impacted_files": [],
                "truncated": False,
                "total_impacted": 0,
                "seed_count": 0,
            }

        self._conn.execute(
            "CREATE TEMP TABLE IF NOT EXISTS _impact_seeds (qn TEXT PRIMARY KEY)"
        )
        self._conn.execute("DELETE FROM _impact_seeds")
        for s in seeds:
            self._conn.execute(
                "INSERT OR IGNORE INTO _impact_seeds (qn) VALUES (?)", (s,)
            )

        cte_sql = """
        WITH RECURSIVE impacted(node_qn, depth) AS (
            SELECT qn, 0 FROM _impact_seeds
            UNION
            SELECT e.target_qualified, i.depth + 1
            FROM impacted i
            JOIN edges e ON e.source_qualified = i.node_qn
            WHERE i.depth < ?
              AND e.kind IN ('CALLS', 'CONTAINS', 'INHERITS', 'IMPLEMENTS')
            UNION
            SELECT e.source_qualified, i.depth + 1
            FROM impacted i
            JOIN edges e ON e.target_qualified = i.node_qn
            WHERE i.depth < ?
              AND e.kind IN ('CALLS', 'CONTAINS', 'INHERITS', 'IMPLEMENTS')
        )
        SELECT DISTINCT node_qn, MIN(depth) AS min_depth
        FROM impacted
        GROUP BY node_qn
        LIMIT ?
        """
        rows = self._conn.execute(
            cte_sql, (max_depth, max_depth, max_nodes + len(seeds))
        ).fetchall()

        impacted_qns: Set[str] = set()
        for r in rows:
            qn = r[0]
            if qn not in seeds:
                impacted_qns.add(qn)

        changed_nodes = self._batch_get_nodes(seeds)
        impacted_nodes = self._batch_get_nodes(impacted_qns)

        total_impacted = len(impacted_nodes)
        truncated = total_impacted > max_nodes
        if truncated:
            impacted_nodes = impacted_nodes[:max_nodes]

        impacted_files = list({n["file_path"] for n in impacted_nodes})

        return {
            "changed_nodes": changed_nodes,
            "impacted_nodes": impacted_nodes,
            "impacted_files": impacted_files,
            "truncated": truncated,
            "total_impacted": total_impacted,
            "seed_count": len(seeds),
        }

    def _batch_get_nodes(self, qualified_names: Set[str]) -> List[Dict[str, Any]]:
        if not qualified_names:
            return []
        qns = list(qualified_names)
        results: List[Dict[str, Any]] = []
        batch_size = 450
        for i in range(0, len(qns), batch_size):
            batch = qns[i : i + batch_size]
            placeholders = ",".join("?" for _ in batch)
            rows = self._conn.execute(
                f"SELECT * FROM nodes WHERE qualified_name IN ({placeholders})",
                batch,
            ).fetchall()
            results.extend(dict(r) for r in rows)
        return results


# ---------------------------------------------------------------------------
# AST Helpers
# ---------------------------------------------------------------------------


def _node_text(node: Any, source_bytes: bytes) -> str:
    """Extract text for a tree-sitter node."""
    return source_bytes[node.start_byte : node.end_byte].decode(
        "utf-8", errors="ignore"
    )


def _parse_receiver(param_text: str) -> str:
    """Parse receiver type from Go method declaration params."""
    text = param_text.strip().strip("()")
    parts = text.split()
    if not parts:
        return ""
    candidates = [p for p in parts if not p.startswith("*")]
    if candidates:
        return candidates[-1].lstrip("*")
    return parts[-1].lstrip("*")


# ---------------------------------------------------------------------------
# MultiLangParser (Tree-sitter)
# ---------------------------------------------------------------------------


class MultiLangParser:
    """Tree-sitter based multi-language AST parser."""

    EXT_TO_LANG: Dict[str, str] = {
        ".go": "go",
        ".py": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".rs": "rust",
        ".java": "java",
        ".c": "c",
        ".cpp": "cpp",
        ".cc": "cpp",
        ".h": "c",
        ".hpp": "cpp",
        ".cs": "csharp",
        ".rb": "ruby",
        ".php": "php",
        ".swift": "swift",
        ".kt": "kotlin",
        ".scala": "scala",
        ".dart": "dart",
        ".r": "r",
        ".mjs": "javascript",
    }

    def parse(self, file_path: str, source: str) -> Tuple[List[NodeInfo], List[EdgeInfo]]:
        ext = Path(file_path).suffix.lower()
        lang = self.EXT_TO_LANG.get(ext)

        if not lang or not TS_AVAILABLE:
            return RegexFallbackParser().parse(file_path, source)

        source_bytes = source.encode("utf-8")
        parser = _get_ts_parser(lang)
        if parser is None:
            return RegexFallbackParser().parse(file_path, source)

        try:
            tree = parser.parse(source_bytes)
            root = tree.root_node
        except Exception as e:
            log.warning(f"Tree-sitter parse failed for {file_path}: {e}")
            return RegexFallbackParser().parse(file_path, source)

        handler_name = f"_parse_{lang.replace('-', '_')}"
        handler = getattr(self, handler_name, None)
        if handler:
            return handler(root, file_path, source_bytes)

        return self._parse_generic(root, file_path, source_bytes, lang)

    # ===================== Go =====================

    def _parse_go(
        self, root: Any, file_path: str, source_bytes: bytes
    ) -> Tuple[List[NodeInfo], List[EdgeInfo]]:
        nodes: List[NodeInfo] = []
        edges: List[EdgeInfo] = []
        declared: Dict[str, str] = {}

        for child in root.children:
            if child.type == "function_declaration":
                self._go_function(child, file_path, source_bytes, nodes, edges, declared)
            elif child.type == "method_declaration":
                self._go_method(child, file_path, source_bytes, nodes, edges, declared)
            elif child.type == "type_declaration":
                self._go_type(child, file_path, source_bytes, nodes, edges, declared)
            elif child.type == "import_declaration":
                self._go_import(child, file_path, source_bytes, nodes, edges)

        nodes.append(self._file_node(file_path, source_bytes))
        return nodes, edges

    def _go_function(
        self, node, fp, src, nodes, edges, declared
    ):
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = _node_text(name_node, src)
        qname = f"{fp}::{name}"
        ls = node.start_point[0] + 1
        params = _node_text(node.child_by_field_name("parameters"), src)
        ret = _node_text(node.child_by_field_name("result"), src)
        is_test = name.startswith("Test") or name.startswith("Benchmark")
        n = NodeInfo(
            kind="Function", name=name, file_path=fp, line_start=ls,
            line_end=node.end_point[0] + 1, qualified_name=qname,
            params=params, return_type=ret, is_test=is_test,
        )
        nodes.append(n)
        declared[name] = qname
        edges.append(EdgeInfo(kind="CONTAINS", source=fp, target=qname, file_path=fp, line=ls))
        body = node.child_by_field_name("body")
        if body:
            edges.extend(self._extract_calls_go(body, src, fp, qname, declared))

    def _go_method(
        self, node, fp, src, nodes, edges, declared
    ):
        name_node = node.child_by_field_name("name")
        recv_node = node.child_by_field_name("receiver")
        if not name_node:
            return
        name = _node_text(name_node, src)
        receiver = ""
        if recv_node:
            receiver = _parse_receiver(_node_text(recv_node, src))
        qname = f"{fp}::{receiver}.{name}" if receiver else f"{fp}::{name}"
        ls = node.start_point[0] + 1
        params = _node_text(node.child_by_field_name("parameters"), src)
        ret = _node_text(node.child_by_field_name("result"), src)
        n = NodeInfo(
            kind="Method", name=name, file_path=fp, line_start=ls,
            line_end=node.end_point[0] + 1, qualified_name=qname,
            parent_name=receiver, params=params, return_type=ret,
        )
        nodes.append(n)
        declared[name] = qname
        edges.append(EdgeInfo(kind="CONTAINS", source=fp, target=qname, file_path=fp, line=ls))
        body = node.child_by_field_name("body")
        if body:
            edges.extend(self._extract_calls_go(body, src, fp, qname, declared))

    def _go_type(
        self, node, fp, src, nodes, edges, declared
    ):
        for spec in node.children:
            if spec.type != "type_spec":
                continue
            name_node = spec.child_by_field_name("name")
            type_node = spec.child_by_field_name("type")
            if not name_node or not type_node:
                continue
            name = _node_text(name_node, src)
            qname = f"{fp}::{name}"
            ls = spec.start_point[0] + 1
            t = type_node.type
            kind = "Struct" if t == "struct_type" else "Interface" if t == "interface_type" else "Type"
            n = NodeInfo(kind=kind, name=name, file_path=fp, line_start=ls, line_end=spec.end_point[0] + 1, qualified_name=qname)
            nodes.append(n)
            declared[name] = qname
            edges.append(EdgeInfo(kind="CONTAINS", source=fp, target=qname, file_path=fp, line=ls))

    def _go_import(
        self, node, fp, src, nodes, edges
    ):
        for spec in node.children:
            if spec.type != "import_spec":
                continue
            path_node = spec.child_by_field_name("path")
            if not path_node:
                continue
            path_text = _node_text(path_node, src).strip('"')
            alias_node = spec.child_by_field_name("name")
            alias = _node_text(alias_node, src) if alias_node else path_text.split("/")[-1]
            ls = spec.start_point[0] + 1
            qname = f"{fp}::{alias}"
            nodes.append(NodeInfo(kind="Import", name=alias, file_path=fp, line_start=ls, line_end=ls, qualified_name=qname))
            edges.append(EdgeInfo(kind="IMPORTS_FROM", source=fp, target=path_text, file_path=fp, line=ls))

    def _extract_calls_go(self, node, src, fp, parent_qn, declared):
        edges: List[EdgeInfo] = []
        def _walk(n):
            if n.type == "call_expression":
                func_node = n.child_by_field_name("function")
                if func_node:
                    if func_node.type == "identifier":
                        name = _node_text(func_node, src)
                        edges.append(EdgeInfo(kind="CALLS", source=parent_qn, target=declared.get(name, name), file_path=fp, line=func_node.start_point[0] + 1))
                    elif func_node.type == "selector_expression":
                        op = func_node.child_by_field_name("operand")
                        fld = func_node.child_by_field_name("field")
                        if op and fld:
                            target = declared.get(_node_text(fld, src), f"{_node_text(op, src)}.{_node_text(fld, src)}")
                            edges.append(EdgeInfo(kind="CALLS", source=parent_qn, target=target, file_path=fp, line=func_node.start_point[0] + 1))
            for c in n.children:
                _walk(c)
        _walk(node)
        return edges

    # ===================== Python =====================

    def _parse_python(
        self, root: Any, file_path: str, source_bytes: bytes
    ) -> Tuple[List[NodeInfo], List[EdgeInfo]]:
        nodes: List[NodeInfo] = []
        edges: List[EdgeInfo] = []
        declared: Dict[str, str] = {}

        def walk(node):
            actual = node
            if node.type == "decorated_definition":
                for c in node.children:
                    if c.type in ("function_definition", "class_definition"):
                        actual = c
                        break

            if actual.type == "function_definition":
                self._py_function(actual, file_path, source_bytes, nodes, edges, declared)
            elif actual.type == "class_definition":
                self._py_class(actual, file_path, source_bytes, nodes, edges, declared)
            else:
                for c in node.children:
                    walk(c)

        walk(root)
        nodes.append(self._file_node(file_path, source_bytes))
        return nodes, edges

    def _py_function(self, node, fp, src, nodes, edges, declared):
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = _node_text(name_node, src)
        qname = f"{fp}::{name}"
        ls = node.start_point[0] + 1
        params = _node_text(node.child_by_field_name("parameters"), src)
        is_test = name.startswith("test_")
        n = NodeInfo(
            kind="Function", name=name, file_path=fp, line_start=ls,
            line_end=node.end_point[0] + 1, qualified_name=qname,
            params=params, is_test=is_test,
        )
        nodes.append(n)
        declared[name] = qname
        edges.append(EdgeInfo(kind="CONTAINS", source=fp, target=qname, file_path=fp, line=ls))
        body = node.child_by_field_name("body")
        if body:
            edges.extend(self._extract_calls_py(body, src, fp, qname, declared))

    def _py_class(self, node, fp, src, nodes, edges, declared):
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = _node_text(name_node, src)
        qname = f"{fp}::{name}"
        ls = node.start_point[0] + 1
        n = NodeInfo(kind="Class", name=name, file_path=fp, line_start=ls, line_end=node.end_point[0] + 1, qualified_name=qname)
        nodes.append(n)
        declared[name] = qname
        edges.append(EdgeInfo(kind="CONTAINS", source=fp, target=qname, file_path=fp, line=ls))

        body = node.child_by_field_name("body")
        if body:
            for child in body.children:
                if child.type == "function_definition":
                    m_name_node = child.child_by_field_name("name")
                    if m_name_node:
                        m_name = _node_text(m_name_node, src)
                        m_qname = f"{fp}::{name}.{m_name}"
                        m_ls = child.start_point[0] + 1
                        m_params = _node_text(child.child_by_field_name("parameters"), src)
                        m = NodeInfo(
                            kind="Method", name=m_name, file_path=fp, line_start=m_ls,
                            line_end=child.end_point[0] + 1, qualified_name=m_qname,
                            parent_name=name, params=m_params,
                        )
                        nodes.append(m)
                        declared[m_name] = m_qname
                        edges.append(EdgeInfo(kind="CONTAINS", source=qname, target=m_qname, file_path=fp, line=m_ls))
                        m_body = child.child_by_field_name("body")
                        if m_body:
                            edges.extend(self._extract_calls_py(m_body, src, fp, m_qname, declared))

    def _extract_calls_py(self, node, src, fp, parent_qn, declared):
        edges: List[EdgeInfo] = []
        def _walk(n):
            if n.type == "call":
                func = n.child_by_field_name("function")
                if func:
                    if func.type == "identifier":
                        name = _node_text(func, src)
                        edges.append(EdgeInfo(kind="CALLS", source=parent_qn, target=declared.get(name, name), file_path=fp, line=func.start_point[0] + 1))
                    elif func.type == "attribute":
                        obj = func.child_by_field_name("object")
                        attr = func.child_by_field_name("attribute")
                        if obj and attr:
                            target = declared.get(_node_text(attr, src), f"{_node_text(obj, src)}.{_node_text(attr, src)}")
                            edges.append(EdgeInfo(kind="CALLS", source=parent_qn, target=target, file_path=fp, line=func.start_point[0] + 1))
            for c in n.children:
                _walk(c)
        _walk(node)
        return edges

    # ===================== JavaScript / TypeScript / TSX =====================

    def _parse_javascript(
        self, root: Any, file_path: str, source_bytes: bytes
    ) -> Tuple[List[NodeInfo], List[EdgeInfo]]:
        return self._parse_js_family(root, file_path, source_bytes)

    def _parse_typescript(
        self, root: Any, file_path: str, source_bytes: bytes
    ) -> Tuple[List[NodeInfo], List[EdgeInfo]]:
        return self._parse_js_family(root, file_path, source_bytes)

    def _parse_tsx(
        self, root: Any, file_path: str, source_bytes: bytes
    ) -> Tuple[List[NodeInfo], List[EdgeInfo]]:
        return self._parse_js_family(root, file_path, source_bytes)

    def _parse_js_family(self, root, fp, src):
        nodes: List[NodeInfo] = []
        edges: List[EdgeInfo] = []
        declared: Dict[str, str] = {}

        def extract_name(node):
            n = node.child_by_field_name("name")
            return _node_text(n, src) if n else None

        def walk(node):
            actual = node
            if node.type in ("export_statement", "export_default_declaration"):
                for c in node.children:
                    if c.type in ("function_declaration", "class_declaration", "function_expression"):
                        actual = c
                        break

            if actual.type == "function_declaration":
                name = extract_name(actual)
                if name:
                    qname = f"{fp}::{name}"
                    ls = actual.start_point[0] + 1
                    params = _node_text(actual.child_by_field_name("parameters"), src)
                    n = NodeInfo(kind="Function", name=name, file_path=fp, line_start=ls, line_end=actual.end_point[0] + 1, qualified_name=qname, params=params)
                    nodes.append(n)
                    declared[name] = qname
                    edges.append(EdgeInfo(kind="CONTAINS", source=fp, target=qname, file_path=fp, line=ls))
                    body = actual.child_by_field_name("body")
                    if body:
                        edges.extend(self._extract_calls_js(body, src, fp, qname, declared))

            elif actual.type == "class_declaration":
                name = extract_name(actual)
                if name:
                    qname = f"{fp}::{name}"
                    ls = actual.start_point[0] + 1
                    n = NodeInfo(kind="Class", name=name, file_path=fp, line_start=ls, line_end=actual.end_point[0] + 1, qualified_name=qname)
                    nodes.append(n)
                    declared[name] = qname
                    edges.append(EdgeInfo(kind="CONTAINS", source=fp, target=qname, file_path=fp, line=ls))
                    body = actual.child_by_field_name("body")
                    if body:
                        for c in body.children:
                            if c.type == "method_definition":
                                m_name_node = c.child_by_field_name("name")
                                if m_name_node:
                                    m_name = _node_text(m_name_node, src)
                                    m_qname = f"{fp}::{name}.{m_name}"
                                    m_ls = c.start_point[0] + 1
                                    m_params = _node_text(c.child_by_field_name("parameters"), src)
                                    m = NodeInfo(kind="Method", name=m_name, file_path=fp, line_start=m_ls, line_end=c.end_point[0] + 1, qualified_name=m_qname, parent_name=name, params=m_params)
                                    nodes.append(m)
                                    declared[m_name] = m_qname
                                    edges.append(EdgeInfo(kind="CONTAINS", source=qname, target=m_qname, file_path=fp, line=m_ls))
                                    m_body = c.child_by_field_name("body")
                                    if m_body:
                                        edges.extend(self._extract_calls_js(m_body, src, fp, m_qname, declared))

            else:
                for c in node.children:
                    walk(c)

        walk(root)
        nodes.append(self._file_node(fp, src))
        return nodes, edges

    def _extract_calls_js(self, node, src, fp, parent_qn, declared):
        edges: List[EdgeInfo] = []
        def _walk(n):
            if n.type == "call_expression":
                func = n.child_by_field_name("function")
                if func:
                    if func.type == "identifier":
                        name = _node_text(func, src)
                        edges.append(EdgeInfo(kind="CALLS", source=parent_qn, target=declared.get(name, name), file_path=fp, line=func.start_point[0] + 1))
                    elif func.type == "member_expression":
                        obj = func.child_by_field_name("object")
                        prop = func.child_by_field_name("property")
                        if obj and prop:
                            target = declared.get(_node_text(prop, src), f"{_node_text(obj, src)}.{_node_text(prop, src)}")
                            edges.append(EdgeInfo(kind="CALLS", source=parent_qn, target=target, file_path=fp, line=func.start_point[0] + 1))
            for c in n.children:
                _walk(c)
        _walk(node)
        return edges

    # ===================== Rust =====================

    def _parse_rust(
        self, root: Any, file_path: str, source_bytes: bytes
    ) -> Tuple[List[NodeInfo], List[EdgeInfo]]:
        nodes: List[NodeInfo] = []
        edges: List[EdgeInfo] = []
        declared: Dict[str, str] = {}

        def walk(node):
            if node.type == "function_item":
                self._rust_function(node, file_path, source_bytes, nodes, edges, declared)
            elif node.type == "struct_item":
                self._rust_struct(node, file_path, source_bytes, nodes, edges, declared)
            elif node.type == "trait_item":
                self._rust_trait(node, file_path, source_bytes, nodes, edges, declared)
            elif node.type == "impl_item":
                self._rust_impl(node, file_path, source_bytes, nodes, edges, declared)
            else:
                for c in node.children:
                    walk(c)

        walk(root)
        nodes.append(self._file_node(file_path, source_bytes))
        return nodes, edges

    def _rust_function(self, node, fp, src, nodes, edges, declared):
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = _node_text(name_node, src)
        qname = f"{fp}::{name}"
        ls = node.start_point[0] + 1
        params = _node_text(node.child_by_field_name("parameters"), src)
        n = NodeInfo(kind="Function", name=name, file_path=fp, line_start=ls, line_end=node.end_point[0] + 1, qualified_name=qname, params=params)
        nodes.append(n)
        declared[name] = qname
        edges.append(EdgeInfo(kind="CONTAINS", source=fp, target=qname, file_path=fp, line=ls))
        body = node.child_by_field_name("body")
        if body:
            edges.extend(self._extract_calls_rust(body, src, fp, qname, declared))

    def _rust_struct(self, node, fp, src, nodes, edges, declared):
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = _node_text(name_node, src)
        qname = f"{fp}::{name}"
        ls = node.start_point[0] + 1
        n = NodeInfo(kind="Struct", name=name, file_path=fp, line_start=ls, line_end=node.end_point[0] + 1, qualified_name=qname)
        nodes.append(n)
        declared[name] = qname
        edges.append(EdgeInfo(kind="CONTAINS", source=fp, target=qname, file_path=fp, line=ls))

    def _rust_trait(self, node, fp, src, nodes, edges, declared):
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = _node_text(name_node, src)
        qname = f"{fp}::{name}"
        ls = node.start_point[0] + 1
        n = NodeInfo(kind="Trait", name=name, file_path=fp, line_start=ls, line_end=node.end_point[0] + 1, qualified_name=qname)
        nodes.append(n)
        declared[name] = qname
        edges.append(EdgeInfo(kind="CONTAINS", source=fp, target=qname, file_path=fp, line=ls))

    def _rust_impl(self, node, fp, src, nodes, edges, declared):
        type_node = node.child_by_field_name("type")
        if not type_node:
            return
        impl_type = _node_text(type_node, src)
        qname = f"{fp}::{impl_type}"
        if impl_type not in declared:
            ls = node.start_point[0] + 1
            n = NodeInfo(kind="Struct", name=impl_type, file_path=fp, line_start=ls, line_end=node.end_point[0] + 1, qualified_name=qname)
            nodes.append(n)
            declared[impl_type] = qname
            edges.append(EdgeInfo(kind="CONTAINS", source=fp, target=qname, file_path=fp, line=ls))

        body = node.child_by_field_name("body")
        if body:
            for c in body.children:
                if c.type == "function_item":
                    m_name_node = c.child_by_field_name("name")
                    if m_name_node:
                        m_name = _node_text(m_name_node, src)
                        m_qname = f"{fp}::{impl_type}.{m_name}"
                        m_ls = c.start_point[0] + 1
                        m_params = _node_text(c.child_by_field_name("parameters"), src)
                        m = NodeInfo(kind="Method", name=m_name, file_path=fp, line_start=m_ls, line_end=c.end_point[0] + 1, qualified_name=m_qname, parent_name=impl_type, params=m_params)
                        nodes.append(m)
                        declared[m_name] = m_qname
                        edges.append(EdgeInfo(kind="CONTAINS", source=qname, target=m_qname, file_path=fp, line=m_ls))
                        m_body = c.child_by_field_name("body")
                        if m_body:
                            edges.extend(self._extract_calls_rust(m_body, src, fp, m_qname, declared))

    def _extract_calls_rust(self, node, src, fp, parent_qn, declared):
        edges: List[EdgeInfo] = []
        def _walk(n):
            if n.type == "call_expression":
                func = n.child_by_field_name("function")
                if func:
                    if func.type == "identifier":
                        name = _node_text(func, src)
                        edges.append(EdgeInfo(kind="CALLS", source=parent_qn, target=declared.get(name, name), file_path=fp, line=func.start_point[0] + 1))
                    elif func.type == "field_expression":
                        field = func.child_by_field_name("field")
                        if field:
                            ft = _node_text(field, src)
                            edges.append(EdgeInfo(kind="CALLS", source=parent_qn, target=declared.get(ft, ft), file_path=fp, line=func.start_point[0] + 1))
            for c in n.children:
                _walk(c)
        _walk(node)
        return edges

    # ===================== Java =====================

    def _parse_java(
        self, root: Any, file_path: str, source_bytes: bytes
    ) -> Tuple[List[NodeInfo], List[EdgeInfo]]:
        nodes: List[NodeInfo] = []
        edges: List[EdgeInfo] = []
        declared: Dict[str, str] = {}
        current_class: List[Optional[str]] = [None]

        def walk(node):
            if node.type == "class_declaration":
                self._java_class(node, file_path, source_bytes, nodes, edges, declared, current_class, walk)
            elif node.type == "interface_declaration":
                self._java_interface(node, file_path, source_bytes, nodes, edges, declared, current_class, walk)
            elif node.type in ("method_declaration", "constructor_declaration"):
                self._java_method(node, file_path, source_bytes, nodes, edges, declared, current_class)
            else:
                for c in node.children:
                    walk(c)

        walk(root)
        nodes.append(self._file_node(file_path, source_bytes))
        return nodes, edges

    def _java_class(self, node, fp, src, nodes, edges, declared, current_class, recurse):
        name_node = node.child_by_field_name("name")
        if not name_node:
            for c in node.children:
                recurse(c)
            return
        name = _node_text(name_node, src)
        qname = f"{fp}::{name}"
        ls = node.start_point[0] + 1
        n = NodeInfo(kind="Class", name=name, file_path=fp, line_start=ls, line_end=node.end_point[0] + 1, qualified_name=qname)
        nodes.append(n)
        declared[name] = qname
        edges.append(EdgeInfo(kind="CONTAINS", source=fp, target=qname, file_path=fp, line=ls))
        old = current_class[0]
        current_class[0] = name
        body = node.child_by_field_name("body")
        if body:
            for c in body.children:
                recurse(c)
        current_class[0] = old

    def _java_interface(self, node, fp, src, nodes, edges, declared, current_class, recurse):
        name_node = node.child_by_field_name("name")
        if not name_node:
            for c in node.children:
                recurse(c)
            return
        name = _node_text(name_node, src)
        qname = f"{fp}::{name}"
        ls = node.start_point[0] + 1
        n = NodeInfo(kind="Interface", name=name, file_path=fp, line_start=ls, line_end=node.end_point[0] + 1, qualified_name=qname)
        nodes.append(n)
        declared[name] = qname
        edges.append(EdgeInfo(kind="CONTAINS", source=fp, target=qname, file_path=fp, line=ls))
        old = current_class[0]
        current_class[0] = name
        body = node.child_by_field_name("body")
        if body:
            for c in body.children:
                recurse(c)
        current_class[0] = old

    def _java_method(self, node, fp, src, nodes, edges, declared, current_class):
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = _node_text(name_node, src)
        cls = current_class[0]
        if cls:
            qname = f"{fp}::{cls}.{name}"
            parent = cls
        else:
            qname = f"{fp}::{name}"
            parent = None
        ls = node.start_point[0] + 1
        params = _node_text(node.child_by_field_name("parameters"), src)
        kind = "Method" if cls else "Function"
        n = NodeInfo(kind=kind, name=name, file_path=fp, line_start=ls, line_end=node.end_point[0] + 1, qualified_name=qname, parent_name=parent, params=params)
        nodes.append(n)
        declared[name] = qname
        container = cls if cls else fp
        edges.append(EdgeInfo(kind="CONTAINS", source=container, target=qname, file_path=fp, line=ls))
        body = node.child_by_field_name("body")
        if body:
            edges.extend(self._extract_calls_java(body, src, fp, qname, declared))

    def _extract_calls_java(self, node, src, fp, parent_qn, declared):
        edges: List[EdgeInfo] = []
        def _walk(n):
            if n.type == "method_invocation":
                name_node = n.child_by_field_name("name")
                if name_node:
                    name = _node_text(name_node, src)
                    edges.append(EdgeInfo(kind="CALLS", source=parent_qn, target=declared.get(name, name), file_path=fp, line=name_node.start_point[0] + 1))
            elif n.type == "object_creation_expression":
                type_node = n.child_by_field_name("type")
                if type_node:
                    tt = _node_text(type_node, src)
                    edges.append(EdgeInfo(kind="CALLS", source=parent_qn, target=f"new {declared.get(tt, tt)}", file_path=fp, line=type_node.start_point[0] + 1))
            for c in n.children:
                _walk(c)
        _walk(node)
        return edges

    # ===================== Generic Fallback =====================

    def _parse_generic(
        self, root: Any, file_path: str, source_bytes: bytes, lang: str
    ) -> Tuple[List[NodeInfo], List[EdgeInfo]]:
        """Heuristic extraction for languages without specific handlers."""
        nodes: List[NodeInfo] = []
        edges: List[EdgeInfo] = []
        declared: Dict[str, str] = {}

        DEF_PATTERNS = ["function", "method", "class", "struct", "interface", "trait", "impl", "def", "fn"]

        def walk(node):
            name_node = node.child_by_field_name("name")
            if name_node and any(p in node.type for p in DEF_PATTERNS):
                name = _node_text(name_node, source_bytes)
                if name:
                    qname = f"{file_path}::{name}"
                    ls = node.start_point[0] + 1
                    kind = "Function"
                    for pat, k in [("class", "Class"), ("struct", "Struct"), ("interface", "Interface"), ("trait", "Trait")]:
                        if pat in node.type:
                            kind = k
                            break
                    n = NodeInfo(kind=kind, name=name, file_path=file_path, line_start=ls, line_end=node.end_point[0] + 1, qualified_name=qname)
                    nodes.append(n)
                    declared[name] = qname
                    edges.append(EdgeInfo(kind="CONTAINS", source=file_path, target=qname, file_path=file_path, line=ls))
                    body = node.child_by_field_name("body")
                    if body:
                        edges.extend(self._extract_calls_generic(body, source_bytes, file_path, qname, declared))
            else:
                for c in node.children:
                    walk(c)

        walk(root)
        nodes.append(self._file_node(file_path, source_bytes))
        return nodes, edges

    def _extract_calls_generic(self, node, src, fp, parent_qn, declared):
        edges: List[EdgeInfo] = []
        def _walk(n):
            if "call" in n.type or "invocation" in n.type:
                for c in n.children:
                    if c.type in ("identifier", "property_identifier", "field_identifier"):
                        name = _node_text(c, src)
                        edges.append(EdgeInfo(kind="CALLS", source=parent_qn, target=declared.get(name, name), file_path=fp, line=c.start_point[0] + 1))
                        break
            for c in n.children:
                _walk(c)
        _walk(node)
        return edges

    # ===================== Shared =====================

    def _file_node(self, file_path: str, source_bytes: bytes) -> NodeInfo:
        return NodeInfo(
            kind="File",
            name=os.path.basename(file_path),
            file_path=file_path,
            line_start=1,
            line_end=source_bytes.count(b"\n") + 1,
            qualified_name=file_path,
        )


# ---------------------------------------------------------------------------
# RegexFallbackParser
# ---------------------------------------------------------------------------


class RegexFallbackParser:
    """Regex-based fallback parser when Tree-sitter is unavailable."""

    PATTERNS: Dict[str, Dict[str, re.Pattern]] = {
        "go": {
            "function": re.compile(r"^func\s+(?:\(\s*[\w*]+\s+[\w*]+\s*\)\s+)?(\w+)\s*\(", re.MULTILINE),
            "struct": re.compile(r"^type\s+(\w+)\s+struct", re.MULTILINE),
            "interface": re.compile(r"^type\s+(\w+)\s+interface", re.MULTILINE),
        },
        "python": {
            "function": re.compile(r"^(?:\s*@[\w.]+\s*)*def\s+(\w+)\s*\(", re.MULTILINE),
            "class": re.compile(r"^(?:\s*@[\w.]+\s*)*class\s+(\w+)", re.MULTILINE),
        },
        "javascript": {
            "function": re.compile(r"(?:export\s+(?:default\s+)?)?(?:async\s+)?function\s+(\w+)\s*\(", re.MULTILINE),
            "class": re.compile(r"(?:export\s+)?class\s+(\w+)", re.MULTILINE),
        },
        "java": {
            "function": re.compile(r"(?:public|private|protected|static|\s)+[\w<>\[\]]+\s+(\w+)\s*\(", re.MULTILINE),
            "class": re.compile(r"(?:public\s+)?class\s+(\w+)", re.MULTILINE),
        },
        "rust": {
            "function": re.compile(r"^(?:\s*#\[\w+\]\s*)*fn\s+(\w+)\s*\(", re.MULTILINE),
            "struct": re.compile(r"^struct\s+(\w+)", re.MULTILINE),
            "trait": re.compile(r"^trait\s+(\w+)", re.MULTILINE),
        },
    }

    EXT_TO_LANG: Dict[str, str] = {
        ".go": "go", ".py": "python", ".js": "javascript", ".jsx": "javascript",
        ".ts": "javascript", ".tsx": "javascript", ".java": "java", ".rs": "rust",
    }

    def parse(self, file_path: str, source: str) -> Tuple[List[NodeInfo], List[EdgeInfo]]:
        ext = Path(file_path).suffix.lower()
        lang = self.EXT_TO_LANG.get(ext, "generic")
        nodes: List[NodeInfo] = []
        edges: List[EdgeInfo] = []
        lines = source.splitlines()
        loc = len(lines)

        patterns = self.PATTERNS.get(lang, {})

        for match in patterns.get("function", re.compile(r"$^")).finditer(source):
            name = match.group(1)
            line_no = source[:match.start()].count("\n") + 1
            qname = f"{file_path}::{name}"
            is_test = False
            if lang == "go":
                is_test = name.startswith("Test") or name.startswith("Benchmark")
            elif lang == "python":
                is_test = name.startswith("test_")
            elif lang == "rust":
                is_test = name == "test"
            nodes.append(NodeInfo(kind="Function", name=name, file_path=file_path, line_start=line_no, line_end=line_no, qualified_name=qname, is_test=is_test))
            edges.append(EdgeInfo(kind="CONTAINS", source=file_path, target=qname, file_path=file_path, line=line_no))

        for kind_key in ["class", "struct", "interface", "trait"]:
            kind_map = {"class": "Class", "struct": "Struct", "interface": "Interface", "trait": "Trait"}
            for match in patterns.get(kind_key, re.compile(r"$^")).finditer(source):
                name = match.group(1)
                line_no = source[:match.start()].count("\n") + 1
                qname = f"{file_path}::{name}"
                nodes.append(NodeInfo(kind=kind_map.get(kind_key, "Type"), name=name, file_path=file_path, line_start=line_no, line_end=line_no, qualified_name=qname))
                edges.append(EdgeInfo(kind="CONTAINS", source=file_path, target=qname, file_path=file_path, line=line_no))

        nodes.append(NodeInfo(kind="File", name=os.path.basename(file_path), file_path=file_path, line_start=1, line_end=loc, qualified_name=file_path))
        return nodes, edges


# ---------------------------------------------------------------------------
# KnowledgeGraph (Orchestrator)
# ---------------------------------------------------------------------------


class KnowledgeGraph:
    """High-level orchestrator for building and querying the code graph."""

    def __init__(self, root_dir: str, db_path: str = "code_graph.db"):
        self.root_dir = root_dir
        self.store = GraphStore(db_path)
        self.parser = MultiLangParser()

    def parse_project(self, changed_files: Optional[List[str]] = None) -> Dict[str, Any]:
        if changed_files is not None:
            return self._incremental_build(changed_files)
        return self._full_build()

    def _full_build(self) -> Dict[str, Any]:
        log.info("[KG] Full graph build from: %s", self.root_dir)
        all_files: List[str] = []
        for root, _, files in os.walk(self.root_dir):
            for f in files:
                ext = Path(f).suffix.lower()
                if ext in MultiLangParser.EXT_TO_LANG:
                    all_files.append(os.path.join(root, f))

        existing = set(self.store.get_all_files())
        current = set(all_files)
        for stale in existing - current:
            self.store.remove_file_data(stale)

        total_nodes = 0
        total_edges = 0
        for i, fp in enumerate(all_files, 1):
            nodes, edges = self._process_file(fp)
            total_nodes += len(nodes)
            total_edges += len(edges)
            if i % 50 == 0 or i == len(all_files):
                log.info("[KG] Progress: %d/%d files parsed", i, len(all_files))

        stats = self.store.get_stats()
        log.info(
            "[KG] Full build complete. Files: %d | Nodes: %d | Edges: %d",
            len(all_files), stats["total_nodes"], stats["total_edges"],
        )
        return {"files_parsed": len(all_files), "total_nodes": total_nodes, "total_edges": total_edges}

    def _incremental_build(self, changed_files: List[str]) -> Dict[str, Any]:
        log.info("[KG] Incremental build for %d files", len(changed_files))
        total_nodes = 0
        total_edges = 0
        processed = 0
        for fp in changed_files:
            ext = Path(fp).suffix.lower()
            if ext not in MultiLangParser.EXT_TO_LANG:
                continue
            abs_path = fp if os.path.isabs(fp) else os.path.join(self.root_dir, fp)
            if not os.path.exists(abs_path):
                self.store.remove_file_data(abs_path)
                continue
            nodes, edges = self._process_file(abs_path)
            total_nodes += len(nodes)
            total_edges += len(edges)
            processed += 1

        stats = self.store.get_stats()
        log.info(
            "[KG] Incremental build complete. Processed: %d | Nodes: %d | Edges: %d",
            processed, stats["total_nodes"], stats["total_edges"],
        )
        return {"files_parsed": processed, "total_nodes": total_nodes, "total_edges": total_edges}

    def _process_file(self, file_path: str) -> Tuple[List[NodeInfo], List[EdgeInfo]]:
        try:
            with open(file_path, "rb") as f:
                raw = f.read()
            fhash = hashlib.sha256(raw).hexdigest()

            existing = self.store.get_nodes_by_file(file_path)
            if existing and existing[0].get("file_hash") == fhash:
                return [], []

            source = raw.decode("utf-8", errors="ignore")
            nodes, edges = self.parser.parse(file_path, source)
            self.store.store_file_nodes_edges(file_path, nodes, edges, fhash)
            return nodes, edges
        except Exception as e:
            log.error("[KG] Failed to parse %s: %s", file_path, e)
            return [], []

    def get_impact_report(self, changed_files: List[str]) -> str:
        result = self.store.get_impact_radius(changed_files)
        if not result["impacted_nodes"]:
            return "No dependency impact detected beyond changed files."
        lines = [
            f"## Impact Analysis (Blast Radius)",
            f"- Changed nodes: {result['seed_count']}",
            f"- Impacted nodes: {result['total_impacted']}{' (truncated)' if result['truncated'] else ''}",
            f"- Impacted files: {len(result['impacted_files'])}",
            "",
            "**Impacted files:**",
        ]
        for f in result["impacted_files"][:15]:
            lines.append(f"- `{f}`")
        if len(result["impacted_files"]) > 15:
            lines.append(f"- ... and {len(result['impacted_files']) - 15} more")
        return "\n".join(lines)

    def get_impact_data(self, changed_files: List[str]) -> Dict[str, Any]:
        return self.store.get_impact_radius(changed_files)

    def close(self) -> None:
        self.store.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
