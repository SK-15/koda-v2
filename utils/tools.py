from langchain.tools import tool
from utils.llm import model
from utils.worskpace import resolve_in_workspace, get_workspace, RunnableConfig
import subprocess
import shutil
import pathlib
import os
import re
import fnmatch
from typing import Optional, List, Set

# Non-source directories and dependencies to exclude from search scope
DEFAULT_EXCLUDED_DIRS: Set[str] = {
    # Third-party dependencies and vendor packages
    "node_modules",
    "vendor",
    "vendors",
    "third_party",
    "extern",
    "external",
    "bower_components",
    "jspm_packages",
    "Pods",
    
    # Virtual environments & python distributions
    ".venv",
    "venv",
    "env",
    ".env",
    "virtualenv",
    "site-packages",
    ".tox",
    ".nox",
    ".conda",
    
    # Version control metadata
    ".git",
    ".svn",
    ".hg",
    ".bzr",
    
    # Build artifacts and compiled outputs
    "build",
    "dist",
    "target",
    "bin",
    "obj",
    "out",
    "_build",
    ".next",
    ".nuxt",
    ".turbo",
    ".output",
    ".gradle",
    
    # IDE and editor metadata
    ".idea",
    ".vscode",
    
    # Caches, coverage, and temporary files
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".coverage",
    "coverage",
    "htmlcov",
    ".cache",
    ".parcel-cache",
    ".eggs",
}

# Non-code assets, binary extensions, lockfiles, and media files to exclude
DEFAULT_EXCLUDED_PATTERNS: Set[str] = {
    # Binary and compiled bytecode
    "*.pyc", "*.pyo", "*.pyd", "*.class", "*.o", "*.obj", "*.dll", "*.exe", "*.so", "*.dylib",
    
    # Images and graphics
    "*.png", "*.jpg", "*.jpeg", "*.gif", "*.ico", "*.svg", "*.webp", "*.bmp", "*.tiff",
    
    # Media and audio/video
    "*.mp3", "*.mp4", "*.wav", "*.avi", "*.mov", "*.flac", "*.webm",
    
    # Fonts
    "*.woff", "*.woff2", "*.ttf", "*.eot", "*.otf",
    
    # Archives and compressed files
    "*.zip", "*.tar", "*.gz", "*.bz2", "*.7z", "*.rar",
    
    # Documents and database storage
    "*.pdf", "*.doc", "*.docx", "*.sqlite", "*.sqlite3", "*.db",
    
    # Lockfiles and minified assets
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Cargo.lock", "composer.lock",
    "*.min.js", "*.min.css", "*.map",
    
    # Package metadata
    "*.egg-info", "*.egg"
}


# Default target file extensions categorized by purpose
DEFAULT_TARGET_EXTENSIONS: Set[str] = {
    # Core programming & scripting languages
    ".py", ".pyw",
    ".js", ".jsx", ".mjs", ".cjs",
    ".ts", ".tsx", ".mts", ".cts",
    ".java", ".kt", ".kts", ".scala",
    ".c", ".h", ".cpp", ".hpp", ".cc", ".cxx",
    ".cs", ".fs",
    ".go",
    ".rs",
    ".rb",
    ".php",
    ".swift",
    ".sh", ".bash", ".zsh",
    ".sql",

    # Web templates & stylesheets
    ".html", ".htm", ".vue", ".svelte",
    ".css", ".scss", ".sass", ".less",

    # Configuration, data formats & schemas
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".xml", ".env.example",

    # Documentation & text
    ".md", ".mdx", ".rst", ".txt",
}

# Default target directories to search within
DEFAULT_TARGET_DIRS: List[str] = [".", "src", "lib", "app", "utils"]


def is_path_excluded(
    path: pathlib.Path,
    base_path: pathlib.Path,
    excluded_dirs: Optional[Set[str]] = None,
    excluded_patterns: Optional[Set[str]] = None,
) -> bool:
    """
    Check if a file or directory path should be excluded from search scope.
    Excludes non-source directories (node_modules, build artifacts, git metadata,
    virtual environments) and non-code asset patterns.
    """
    exclusions = excluded_dirs if excluded_dirs is not None else DEFAULT_EXCLUDED_DIRS
    patterns = excluded_patterns if excluded_patterns is not None else DEFAULT_EXCLUDED_PATTERNS

    try:
        rel_path = path.relative_to(base_path)
        parts = rel_path.parts
    except ValueError:
        parts = path.parts

    # Check directory exclusions across all path segments
    for part in parts:
        if part in exclusions:
            return True
        for pattern in exclusions:
            if ("*" in pattern or "?" in pattern) and fnmatch.fnmatch(part, pattern):
                return True

    # Check non-code asset and file exclusion patterns
    file_name = path.name
    for pattern in patterns:
        if fnmatch.fnmatch(file_name, pattern) or fnmatch.fnmatch(str(path), pattern):
            return True

    return False


@tool
def glob_tool(
    pattern: str,
    directory: str = ".",
    exclude_dirs: Optional[List[str]] = None,
    exclude_patterns: Optional[List[str]] = None,
    config: RunnableConfig = None,
) -> str:
    """
    Glob tool to search for files matching a pattern within target directories,
    excluding non-source directories (e.g., node_modules, build artifacts, git metadata, third-party dependencies)
    and non-code assets.
    
    Args:
        pattern: File pattern to match (e.g., "*.py", "**/*.json", "*").
        directory: Target directory scope to search within (default: ".").
        exclude_dirs: Optional list of additional directory names to exclude.
        exclude_patterns: Optional list of additional file patterns to exclude.
    """
    try:
        ws_root = get_workspace(config)
        base_path = resolve_in_workspace(directory, config)
    except ValueError as e:
        return f"Error: {e}"
    if not base_path.is_dir():
        return f"Error: directory '{directory}' does not exist."

    active_exclusions = set(DEFAULT_EXCLUDED_DIRS)
    if exclude_dirs:
        active_exclusions.update(exclude_dirs)

    active_patterns = set(DEFAULT_EXCLUDED_PATTERNS)
    if exclude_patterns:
        active_patterns.update(exclude_patterns)

    matches: List[pathlib.Path] = []
    
    for root, dirs, files in os.walk(base_path):
        # Prune excluded directories in-place to avoid descending into non-source trees
        dirs[:] = [
            d for d in dirs
            if not is_path_excluded(pathlib.Path(root) / d, base_path, active_exclusions, active_patterns)
        ]
        
        for file in sorted(files):
            file_path = pathlib.Path(root) / file
            if is_path_excluded(file_path, base_path, active_exclusions, active_patterns):
                continue
            
            try:
                rel_path = file_path.relative_to(base_path)
            except ValueError:
                rel_path = file_path

            if (
                file_path.match(pattern)
                or rel_path.match(pattern)
                or fnmatch.fnmatch(file, pattern)
                or fnmatch.fnmatch(str(rel_path), pattern)
            ):
                matches.append(file_path)

        for d in sorted(dirs):
            dir_path = pathlib.Path(root) / d
            try:
                rel_path = dir_path.relative_to(base_path)
            except ValueError:
                rel_path = dir_path
            if (
                dir_path.match(pattern)
                or rel_path.match(pattern)
                or fnmatch.fnmatch(d, pattern)
                or fnmatch.fnmatch(str(rel_path), pattern)
            ):
                matches.append(dir_path)

    if not matches:
        return "No files found."
        
    # Return a clean list of file paths. Limit output to protect context window.
    return "\n".join(p.relative_to(ws_root).as_posix() for p in matches[:100])


@tool
def grep_tool(
    query: str,
    directory: str = ".",
    exclude_dirs: Optional[List[str]] = None,
    exclude_patterns: Optional[List[str]] = None,
    case_sensitive: bool = False,
    config: RunnableConfig = None,
) -> str:
    """
    Grep tool to search for terms in files within target directories while excluding non-source directories
    such as node_modules, build artifacts, git metadata, dependencies, and non-code assets.
    
    Args:
        query: The search term or regular expression pattern.
        directory: Target directory scope to search within (default: ".").
        exclude_dirs: Optional list of additional directory names to exclude.
        exclude_patterns: Optional list of additional file patterns to exclude.
        case_sensitive: Whether the search should be case sensitive (default: False).
    """

    try:
        ws_root = get_workspace(config)
        base_path = resolve_in_workspace(directory, config)
    except ValueError as e:
        return f"Error: {e}"
    if not base_path.is_dir():
        return f"Error: directory '{directory}' does not exist."
    

    active_exclusions = set(DEFAULT_EXCLUDED_DIRS)
    if exclude_dirs:
        active_exclusions.update(exclude_dirs)

    active_patterns = set(DEFAULT_EXCLUDED_PATTERNS)
    if exclude_patterns:
        active_patterns.update(exclude_patterns)

    # If ripgrep is installed, use it with exclusion flags
    if shutil.which("rg"):
        try:
            rg_cmd = ["rg", "-n", "--with-filename"]
            if not case_sensitive:
                rg_cmd.append("-i")
            for exc in active_exclusions:
                rg_cmd.extend(["-g", f"!{exc}/**", "-g", f"!{exc}"])
            for pat in active_patterns:
                rg_cmd.extend(["-g", f"!{pat}"])
            rg_cmd.extend(["--", query, base_path.relative_to(ws_root).as_posix()])
            
            result = subprocess.run(
                rg_cmd,
                cwd=ws_root,
                capture_output=True,
                text=True,
                check=False
            )
            if result.returncode == 1:
                return "No matches found."
            elif result.returncode > 1:
                return f"Error executing ripgrep: {result.stderr}"

            output = result.stdout
            max_chars = 8000
            if len(output) > max_chars:
                return output[:max_chars] + f"\n...[Output truncated, {len(output) - max_chars} characters hidden]"
            return output if output.strip() else "No matches found."
        except Exception:
            pass  # Fall back to python implementation

    # Python search fallback (respecting exclusions and search scope)
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        pattern = re.compile(query, flags)
    except re.error:
        pattern = re.compile(re.escape(query), flags)

    results = []
    for root, dirs, files in os.walk(base_path):
        dirs[:] = [
            d for d in dirs
            if not is_path_excluded(pathlib.Path(root) / d, base_path, active_exclusions, active_patterns)
        ]
        for file in sorted(files):
            file_path = pathlib.Path(root) / file
            if is_path_excluded(file_path, base_path, active_exclusions, active_patterns):
                continue
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    for line_num, line in enumerate(f, start=1):
                        if pattern.search(line):
                            results.append(f"{file_path.relative_to(ws_root).as_posix()}:{line_num}:{line.rstrip()}")
            except (OSError, UnicodeDecodeError):
                continue

    if not results:
        return "No matches found."

    output = "\n".join(results)
    max_chars = 8000
    if len(output) > max_chars:
        return output[:max_chars] + f"\n...[Output truncated, {len(output) - max_chars} characters hidden]"
    return output


@tool
def read_file_tool(file_path: str, config: RunnableConfig,start_line: Optional[int] = None, end_line: Optional[int] = None) -> str:
    """
    Reads the contents of a file, optionally restricted to a specific line range.
    Providing line ranges prevents large files from overwhelming the context window.
    """
    try:
        p = resolve_in_workspace(file_path, config)
    except ValueError as e:
        return f"Error: {e}"
    try:
        with open(p, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        # If line limits are provided, slice the array (adjusting for 1-based indexing)
        if start_line is not None and end_line is not None:
            start_idx = max(0, start_line - 1)
            end_idx = min(len(lines), end_line)
            lines = lines[start_idx:end_idx]
            
        content = "".join(lines)
        
        # Safeguard against exceptionally large files if no line limits were passed
        max_chars = 10000
        if len(content) > max_chars:
            return content[:max_chars] + f"\n...[Output truncated, {len(content) - max_chars} characters hidden]"
            
        return content
    except FileNotFoundError:
        return f"Error: File '{file_path}' not found."
    except Exception as e:
        return f"Error reading file: {str(e)}"


@tool
def bash_execute_tool(command: str, config: RunnableConfig,timeout_seconds: int = 30) -> str:
    """
    Executes a bash command and returns the standard output or standard error.
    Includes a timeout to prevent the agent from hanging on interactive prompts.
    """
    try:
        root = get_workspace(config)
    except ValueError as e:
        return f"Error: {e}"
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=timeout_seconds
        )
        
        # Return stdout if successful, otherwise return stderr for debugging
        output = result.stdout if result.returncode == 0 else f"EXIT CODE {result.returncode}:\n{result.stderr}"
        
        # Truncate output to protect the LLM context window
        max_chars = 8000
        if len(output) > max_chars:
            return output[:max_chars] + f"\n...[Output truncated, {len(output) - max_chars} characters hidden]"
            
        return output if output.strip() else "Command executed successfully with no output."
        
    except subprocess.TimeoutExpired:
        return f"Error: Command timed out after {timeout_seconds} seconds. Ensure the command does not require interactive input."
    except Exception as e:
        return f"Error executing command: {str(e)}"


@tool
def git_interface_tool(action: str, config: RunnableConfig,git_args: Optional[str] = "") -> str:
    """
    Executes constrained git actions to manage version control safely.
    Restricting allowed commands prevents destructive actions like force pushing.
    """
    allowed_actions = {"status", "diff", "log", "add", "commit", "branch", "checkout", "stash"}
    
    if action not in allowed_actions:
        return f"Error: Git action '{action}' not permitted. Allowed actions: {', '.join(allowed_actions)}"
        
    # Construct the git command
    command = f"git {action} {git_args}".strip()
    
    # Delegate execution to the bash tool to reuse its timeout and truncation logic
    return bash_execute_tool.invoke({"command":command}, config=config)


@tool
def write_file(path: str, content: str, config: RunnableConfig) -> str:
    """Create or overwrite a file in the workspace."""
    try:
        p = resolve_in_workspace(path, config)
    except ValueError as e:
        return f"Error: {e}"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Wrote {len(content)} chars to {path}"

@tool
def edit_file(path: str, old: str, new: str,config: RunnableConfig) -> str:
    """Replace exactly one occurrence of old with new in a workspace file."""
    try:
        p = resolve_in_workspace(path, config)
    except ValueError as e:
        return f"Error: {e}"

    if not p.is_file(): 
        raise ValueError("Given path is not a file")

    if len(old) == 0:
        return "Error: Old of empty"

    text = p.read_text(encoding="utf-8")
    n = text.count(old)

    if  n == 0:
        return f"Error: old string not found in {path}"

    if  n > 1:
        raise f"Error: old matches {n} times, add more surrounding context"

    if  n == 1:
        text = text.replace(old,new,1)
        p.write_text(text,encoding="utf-8")
        return f"Edited {path}"

@tool
def list_dir(config: RunnableConfig, path: str = ".") -> str:
    """
    List files and directories at a path inside the workspace (non-recursive).
    Directories end with '/'. Paths are relative to the workspace root.

    Args:
        path: Directory to list, relative to the workspace root (default: ".").
    """
    try:
        root = get_workspace(config)
        p = resolve_in_workspace(path, config)
    except ValueError as e:
        return f"Error: {e}"

    if not p.exists():
        return f"Error: '{path}' does not exist."
    if not p.is_dir():
        return f"Error: '{path}' is not a directory."

    try:
        entries = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
    except OSError as e:
        return f"Error listing '{path}': {e}"

    lines = []
    for entry in entries:
        if entry.is_dir() and entry.name in DEFAULT_EXCLUDED_DIRS:
            continue
        rel = entry.relative_to(root).as_posix()
        lines.append(rel + "/" if entry.is_dir() else rel)

    if not lines:
        return "(empty)"

    max_entries = 200
    if len(lines) > max_entries:
        return "\n".join(lines[:max_entries]) + f"\n...[{len(lines) - max_entries} more entries hidden]"
    return "\n".join(lines)
    

def list_files(root: pathlib.Path) -> list[str]:
    out = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in DEFAULT_EXCLUDED_DIRS)
        rel = pathlib.Path(dirpath).relative_to(root)
        out += [(rel / f).as_posix() for f in sorted(files)]
    return out

def file_tree(root: pathlib.Path, max_entries: int = 300) -> str:
    files = list_files(root)
    if not files:
        return "(empty workspace)"
    extra = len(files) - max_entries
    return "\n".join(files[:max_entries]) + (f"\n...[{extra} more]" if extra > 0 else "")



tools = [glob_tool, grep_tool, read_file_tool, bash_execute_tool, git_interface_tool,list_dir, edit_file, write_file]
tools_by_name = {tool.name: tool for tool in tools}
model_with_tools = model.bind_tools(tools)
