# Code Review Agent

AI-powered code review system with multi-model support, sub-agent architecture, and structured JSON output.

## Architecture

```
main.py              -> Entry point, config validation, report formatting
review_pipeline.py   -> Orchestrates the 5-step review flow
git_helper.py        -> Git operations (PR diff vs branch, or gerrit patch mode)
linter_runner.py     -> Static analysis (golangci-lint)
graph_builder.py     -> Code knowledge graph (incremental, regex-based)
logger.py            -> Colored console + file logging
config.py            -> Unified env-var based configuration
llm_client.py        -> OpenAI-compatible HTTP client (Kimi, DeepSeek, Claude, OpenAI)

db/
  db.py              -> MySQL persistence for team rules and review history

agents/
  summarizer.py      -> Sub-agent: cheap model reads full files, outputs JSON summaries
  reviewer.py        -> Parent-agent: strong model reviews diff with all context
```

## Key Design Decisions

1. **Static analysis first** (`ENABLE_LINTER=true`). Linter runs before any LLM call. Zero token cost for hard truth.
2. **Sub-agent + Parent-agent pattern**. Cheap/lightweight model (sub-agent) summarizes full file context. Strong model (parent-agent) focuses on diff review with summaries provided as context.
3. **OpenAI-compatible API only**. All providers (Kimi, DeepSeek, Claude, OpenAI) use the same HTTP format. No provider-specific SDKs required.
4. **Structured JSON output** via `response_format={"type": "json_object"}`. No fragile string matching for verdicts.
5. **Git modes**:
   - `GIT_MODE=pr` (default): diff against target branch
   - `GIT_MODE=patch`: review HEAD commit only (gerrit workflow)

## Running

```bash
# Kimi (default)
export LLM_API_KEY="sk-..."
export PROJECT_ROOT="/path/to/go/repo"
python main.py

# DeepSeek
export LLM_PROVIDER="deepseek"
export LLM_API_KEY="sk-..."
python main.py

# Gerrit mode (review single commit)
export GIT_MODE="patch"
python main.py

# Disable linter
export ENABLE_LINTER="false"
python main.py
```

## Output

- `review_report.json` (default) - structured JSON with verdict, issues, metadata
- Exit codes: 0=PASS, 1=BLOCKER, 2=WARN (for CI/CD gating)

## Team Rules

Edit `team_rules.json` and run. The pipeline auto-syncs it to MySQL on startup.

## Memory

Project memories are stored in `.claude/memory/` for cross-session context.
