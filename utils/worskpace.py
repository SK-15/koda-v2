import pathlib
from langchain_core.runnables import RunnableConfig

def get_workspace(config: RunnableConfig) -> pathlib.Path:
    ws = config.get("configurable",{}).get("workspace")
    if not ws:
        raise ValueError("No workspace in the config['configurable']['workspace']")
    return pathlib.Path(ws).resolve()

def resolve_in_workspace(path: str, config: RunnableConfig) -> pathlib.Path:
    root = get_workspace(config)
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"Path '{path}' escape workspace")
    return resolved