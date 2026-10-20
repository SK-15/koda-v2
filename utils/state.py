from typing import List #Annotated
from pydantic import BaseModel, Field
from typing_extensions import TypedDict,Annotated
import operator
from utils.tools import model
# import operator


# Schema for structured output to use in planning
class Task(BaseModel):
    id: str = Field(description="Short unique id like 't1' 't2'")
    name: str = Field(
        description="Task heading",
    )
    overview: str = Field(
        description="what to build acceptance idea",
    )
    files: List[str] = Field(default_factory=list, 
                             description="Workspace-relative files this taask creates")
    depends_on: List[str] = Field(default_factory=list, 
                                  description="ids of tasks that must finish first")


class TaskList(BaseModel):
    plan: str = Field(
        description="Overall plan: Architecture, language, how peices fit"
    )
    tasks: List[Task] = Field(
        description="List of task",
    )

class State(TypedDict):
    query: str
    plan: str
    tasks: list[Task]
    order: list[str]
    done: Annotated[list[str], operator.add]
    files_before: list[str]
    # final_report: str
    completed_sections: Annotated[list, operator.add]
    verify_ok: bool
    verify_output: str
    fix_attempts: int
    summary: str

class WorkerState(TypedDict):
    task: Task
    plan: str
    messages: Annotated[list, operator.add]
    llm_calls: int
    completed_sections: Annotated[list, operator.add]

def topo_batches(tasks: list[Task]) -> list[list[str]]:
    ids = {t.id for t in tasks}
    deps = {t.id: {d for d in t.depends_on if d in ids} for t in tasks}  # drop unknown ids
    batches = []
    while deps:
        ready = sorted(i for i, d in deps.items() if not d)
        if not ready:
            raise ValueError(f"Dependency cycle among: {list(deps)}")
        batches.append(ready)
        for i in ready:
            deps.pop(i)
        for d in deps.values():
            d.difference_update(ready)
    return batches


# Augment the LLM with schema for structured output
planner = model.with_structured_output(TaskList)