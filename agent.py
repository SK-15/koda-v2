from langgraph.types import Send
from langgraph.checkpoint.memory import InMemorySaver
from langchain.messages import SystemMessage, ToolMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from utils.state import planner, State, WorkerState, Task, topo_batches
from utils.tools import model_with_tools, tools_by_name, file_tree, list_files, model
from utils.worskpace import get_workspace
from typing import Literal
import pathlib
import subprocess
from langchain_core.runnables import RunnableConfig

#---------Worker subgraph--------------------

WORKER_SYSTEM = """You are a coding agent inside a project workspace.
Write real, runnable code with write_file/edit_file. Read a file before editing it.
Stay within your task's files unless another change is strictly needed.
When finished, reply WITHOUT a tool call: a short summary of what you changed."""

def worker(state: WorkerState, config:RunnableConfig):
    t = state["task"]
    msgs = state.get("messages") or [
            SystemMessage(content=WORKER_SYSTEM),
            HumanMessage(content=f"""## Overall plan
                                    {state['plan']}

                                    ## Current file tree
                                    {file_tree(get_workspace(config))}

                                    ## Your task ({t.id}): {t.name}
                                    {t.overview}

                                    Files you own: {', '.join(t.files) or '(none listed)'}""")
                                    ]

    msg = model_with_tools.invoke(msgs)
    new = [msg] if state.get("messages") else [*msgs, msg]

    return {"messages": new, "llm_calls": state.get('llm_calls', 0) + 1}

def finish_task(state: WorkerState):
    return {"completed_sections": [state["messages"][-1].text]}

def tool_node(state: dict, config: RunnableConfig):
    """Performs the tool call"""

    result = []
    for tool_call in state["messages"][-1].tool_calls:
        tool = tools_by_name.get(tool_call["name"])
        try: 
            if tool is None:
                raise ValueError(f"Unknown tool {tool_call['name']}")
            observation = tool.invoke(tool_call["args"], config=config)
        except Exception as e:
            observation = f"Error: {type(e).__name__}: {e}"
        result.append(ToolMessage(content=observation, tool_call_id=tool_call["id"]))
    return {"messages": result}

def should_continue(state: WorkerState) -> Literal["tool_node", "finish_task"]:
    """Decide if we should continue the loop or stop based upon whether the LLM made a tool call"""

    messages = state["messages"]
    last_message = messages[-1]

    # If the LLM makes a tool call, then perform an action
    if last_message.tool_calls:
        return "tool_node"

    # Otherwise, we stop (reply to the user)'
    return "finish_task"

worker_graph = StateGraph(WorkerState)
worker_graph.add_node("worker",worker)
worker_graph.add_node("tool_node",tool_node)
worker_graph.add_node("finish_task",finish_task)

worker_graph.add_edge(START,"worker")
worker_graph.add_conditional_edges("worker", should_continue,["tool_node","finish_task"])
worker_graph.add_edge("tool_node","worker")
worker_graph.add_edge("finish_task",END)

worker_app = worker_graph.compile()

#---------Main subgraph--------------------
def planning_node(state: State):

    report_sections = planner.invoke(
        [
            SystemMessage(content="Based on the query your task if define a plan, divide that into tasks"),
            HumanMessage(content=f"Here is the user query: {state['query']}")
        ]
    )

    batches = topo_batches(report_sections.tasks) 
    order = [i for b in batches for i in b]

    return {"plan": report_sections.plan, "tasks": order }

def summary(state: State, config: RunnableConfig):
    root = get_workspace(config)
    new_files = sorted(set(list_files(root)) - set(state["files_before"]))
    status = "✅ checks pass" if state["verify_ok"] else \
             f"❌ checks still failing after {state.get('fix_attempts', 0)} fixes:\n{state['verify_output']}"
    how = model.invoke([
        SystemMessage(content="Write 3-6 lines: how to install deps and run this project. Commands only, no fluff."),
        HumanMessage(content=f"Plan:\n{state['plan']}\n\nFiles:\n{file_tree(root)}"),
    ]).text
    return {"summary": f"## Files created\n" + "\n".join(f"- {f}" for f in new_files)
                       + f"\n\n## Status\n{status}\n\n## How to run\n{how}"}

def run_next_task(state: State, config: RunnableConfig):
    by_id = {t.id: t for t in state["tasks"]}
    next_id = next(i for i in state["order"] if i not in state["done"])
    out = worker_app.invoke({"task": by_id[next_id], "plan": state["plan"]}, config)
    return {"done": [next_id], "completed_sections": out["completed_sections"]}

def more_tasks(state: State) -> Literal["run_next_task", "verify"]:
    return "run_next_task" if len(state["done"]) < len(state["order"]) else "verify"

VERIFY_CMDS = ["python -m pytest -q", "ruff check ."]
MAX_FIX = 3

def verify(state: State, config: RunnableConfig):
    root = get_workspace(config)
    cmds = config["configurable"].get("verify_cmds", VERIFY_CMDS)
    failures = []
    for cmd in cmds:
        r = subprocess.run(cmd, shell=True, cwd=root, capture_output=True, text=True, timeout=300)
        if "pytest" in cmd and r.returncode == 5:   # 5 = no tests collected
            continue
        if r.returncode != 0:
            failures.append(f"$ {cmd} (exit {r.returncode})\n{(r.stdout + r.stderr)[-4000:]}")
    return {"verify_ok": not failures, "verify_output": "\n\n".join(failures)}

def after_verify(state: State) -> Literal["fixer", "summary"]:
    if state["verify_ok"] or state.get("fix_attempts", 0) >= MAX_FIX:
        return "summary"
    return "fixer"

def fixer(state: State, config: RunnableConfig):
    n = state.get("fix_attempts", 0)
    fix = Task(id=f"fix{n+1}", name="Fix failing checks",
               overview="Verification failed. Fix the code; change tests only if the test is wrong.\n\n"
                        + state["verify_output"],
               files=[], depends_on=[])
    out = worker_app.invoke({"task": fix, "plan": state["plan"]}, config)
    return {"fix_attempts": n + 1, "completed_sections": out["completed_sections"]}

graph = StateGraph(State)

graph.add_node("planning_node", planning_node)
graph.add_node("run_next_task", run_next_task) 
graph.add_node("more_tasks", more_tasks)
graph.add_node("summary", summary)


graph.add_edge(START, "planning_node")
graph.add_conditional_edges("planning_node", more_tasks, [run_next_task,verify])
graph.add_edge("run_next_task","more_tasks") 
graph.add_conditional_edges("verify","after_verify",["fixer","summary"]) 
graph.add_edge("fixer","verify.")
graph.add_edge("summary", END)   

# import sqlite3
# from langgraph.checkpoint.sqlite import SqliteSaver

# checkpointer = SqliteSaver(sqlite3.connect("checkpoints.db", check_same_thread=False))
# app = graph.compile(checkpointer=checkpointer)



if __name__ == "__main__":
    checkpointer = InMemorySaver()
    app = graph.compile(checkpointer=checkpointer)

    project_id = "demo"
    workspace = pathlib.Path("projects") / project_id
    workspace.mkdir(parents=True, exist_ok=True)
    config = {"configurable": {
        "thread_id": f"{project_id}-session-1",
        "workspace": str(workspace.resolve()),
    }}

    result = app.invoke({"query": "find all TODOs in repo"}, config)
    print(result["final_report"])

    # Inspect the latest saved state
    snap = app.get_state(config)
    print(snap.values["plan"], snap.next)

    # Full history (newest first)
    for s in app.get_state_history(config):
        print(s.config["configurable"]["checkpoint_id"], s.next)

    # Resume after a crash: invoke with None and the same thread_id
    app.invoke(None, config)

    # Time travel: rerun from an older checkpoint
    old = list(app.get_state_history(config))[-2]
    app.invoke(None, old.config)

    # Edit state, then continue (e.g. fix the plan by hand)
    app.update_state(config, {"plan": "revised plan"})

