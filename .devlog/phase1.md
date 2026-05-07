# Phase 1: Tree-sitter AST + SQLite Knowledge Graph + Impact Radius

## Goals
- Replace regex-based parsing with Tree-sitter AST for Go
- Replace pickle cache with SQLite-backed graph storage
- Implement Blast Radius (Impact Radius) via SQLite recursive CTE
- Integrate impact analysis into review pipeline

## Files Changed

### graph_builder.py (Rewritten)
- **Before**: Regex-based func/struct extraction, pickle cache, no edges/relationships
- **After**:
  - `NodeInfo` / `EdgeInfo` dataclasses matching code-review-graph schema
  - `GraphStore`: SQLite with WAL mode, upsert nodes/edges per file
  - `GoParser`: Tree-sitter AST extraction (function, method, struct, interface, calls, imports)
  - `RegexGoParser`: Fallback when tree-sitter is unavailable
  - `KnowledgeGraph`: Orchestrates full/incremental build, hash-based change detection
  - `get_impact_radius()`: SQLite recursive CTE BFS for blast radius analysis

### review_pipeline.py (Modified)
- Added Step 2.5: Impact Radius Analysis (between static analysis and summarization)
- `impact_report` generated from graph and fed into reviewer prompt

### agents/reviewer.py (Modified)
- Prompt now includes `### Impact Analysis (Blast Radius)` section
- LLM instructed to consider downstream callers/dependents when reviewing changes

### requirements.txt (Modified)
- Added `tree-sitter-language-pack>=0.7.0` (optional, with regex fallback)

## Multi-Language Dispatch (Added)

### graph_builder.py (Rewritten — again)
- **Before**: Hardcoded `GoParser` only, regex fallback was `RegexGoParser`
- **After**:
  - `MultiLangParser`: Dispatches to language-specific `_parse_XXX` methods based on `EXT_TO_LANG` map
  - Supported languages: Go, Python, JavaScript/TypeScript/TSX, Rust, Java, C/C++, C#, Ruby, PHP, Swift, Kotlin, Scala, Dart, R
  - Each parser extracts functions/methods/classes/structs/traits and call edges using Tree-sitter queries
  - `RegexFallbackParser`: Language-aware regex fallback when tree-sitter is unavailable (not just Go)
  - `KnowledgeGraph` unchanged: still orchestrates build, hash detection, and impact radius

### linter_runner.py (Rewritten)
- **Before**: Only `golangci-lint` for Go files; hardcoded Go directory loop in `review_pipeline.py`
- **After**:
  - `run_all_linters(changed_files, project_root)`: Dispatches to per-extension linter functions
  - `run_linter(file_path, project_root)`: Single-file entry point
  - Normalized issue format: `{file, line, column, severity, message, linter}`
  - Supported linters:
    - Go: `golangci-lint`
    - Python: `py_compile` (built-in) + `flake8` + `pylint`
    - JS/TS: `eslint`
    - Rust: `cargo clippy`
    - Java: `checkstyle`
    - C/C++: `cppcheck`
  - Graceful degradation: missing external tools are skipped with a debug log

### review_pipeline.py (Modified)
- Replaced Go-only directory loop with `run_all_linters(changed_files_rel, self.project_root)`
- Static analysis now runs for any file with a registered linter

## Design Decisions
1. **Tree-sitter-language-pack** chosen over individual grammar packages for single-dependency convenience
2. **SQLite WAL mode** for concurrent reads during write
3. **Impact Radius CTE** only traverses `CALLS/CONTAINS/INHERITS/IMPLEMENTS` edges
4. **Bare-name call targets**: Cross-file calls stored as unqualified names; resolution deferred to future phase
5. **SHA-256 hashes** replace MD5 for file change detection
6. **Linter graceful degradation**: Built-in `py_compile` for Python guarantees *some* static analysis even without external tools; other languages skip silently when tools are missing
