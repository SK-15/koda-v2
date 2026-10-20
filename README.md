# koda-v2

A small coding agent built on [LangGraph](https://github.com/langchain-ai/langgraph).
Give it a request ("build a CLI todo app with tests"). It plans the work as dependent
tasks, runs a tool-using worker per task inside a sandboxed workspace folder, checks the
result with tests and lint, fixes what fails, and reports what it built.

> Status: work in progress. Phase 2 (planner → coder → verify → fix → summary) is being wired up.

## How it works

```
START
  │
  ▼
planning_node ── LLM structured output → plan + tasks (id, files, depends_on)
  │               tasks topologically sorted into an execution order
  ▼
run_next_task ◄─┐   runs the worker subgraph for the next task in order
  │             │
  ├── more? ────┘
  ▼
verify ── runs tests + lint in the workspace
  │
  ├── fail (tries < MAX_FIX) ──► fixer ──► verify
  ▼
summary ── files created, check status, how to run
  │
  ▼
 END
```

**Worker subgraph:** `worker → tool_node → worker …` until the model replies without a
tool call, then `finish_task`. The worker's prompt includes the overall plan, the current
workspace file tree, and its own task (with the files it owns).

**Tools** (`utils/tools.py`). Every path is resolved inside the workspace, and anything
that escapes it is rejected:

| Tool | Purpose |
|------|---------|
| `list_dir` | List a directory (non-recursive) |
| `glob_tool` | Find files by pattern, skipping deps/build/VCS dirs |
| `grep_tool` | Search file contents (ripgrep if installed, Python fallback) |
| `read_file_tool` | Read a file, optionally by line range |
| `write_file` | Create or overwrite a file |
| `edit_file` | Replace exactly one occurrence of a string |
| `bash_execute_tool` | Run a shell command in the workspace (with timeout) |
| `git_interface_tool` | Restricted git actions (status, diff, log, add, commit, …) |

## Project layout

```
agent.py            graph definitions (worker subgraph + main graph) and entry point
utils/
  llm.py            chat model setup (Gemini via langchain-google-genai)
  state.py          Pydantic plan schema, graph state types, topo_batches()
  tools.py          agent tools, file_tree()/list_files() helpers
  worskpace.py      workspace resolution and path sandboxing
projects/<id>/      agent workspaces, where generated code lands (git-ignored)
```

## Setup

Requires Python 3.10+.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install pytest ruff        # used by the verify node
```

Create a `.env` file in the project root:

```
GOOGLE_API_KEY=your-key-here
```

Optional: install [ripgrep](https://github.com/BurntSushi/ripgrep) (`rg`) for faster `grep_tool`.

## Run

```bash
python agent.py
```

The entry point in `agent.py` creates `projects/demo/`, uses it as the workspace, and
invokes the graph with a query. Edit `project_id` and the query in the `__main__` block
to change them.

Configuration goes in `config["configurable"]`:

| Key | Meaning |
|-----|---------|
| `thread_id` | Checkpoint thread, needed to resume or time-travel |
| `workspace` | Absolute path the agent is sandboxed to |
| `verify_cmds` | Optional. Overrides the default `["python -m pytest -q", "ruff check ."]` |

State is checkpointed with `InMemorySaver`. To persist across runs, switch to the
commented-out `SqliteSaver` block in `agent.py`.

## Roadmap

- [x] Phase 1: planner + parallel workers with tools, sandboxed workspace, checkpointing
- [ ] Phase 2: coder pipeline: task ids/files/deps, ordered execution, verify + fix loop, summary
- [ ] Run independent tasks in parallel batches (`topo_batches` levels + `Send`)
