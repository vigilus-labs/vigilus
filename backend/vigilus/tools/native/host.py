"""Host native tool handlers – shell execution, filesystem operations."""

import asyncio
import os
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def _confine(path: str, operator: Any) -> tuple[str | None, str | None]:
    """Resolve *path* against the operator's working_dir boundary.

    Operators with a working_dir are confined to it: relative paths resolve
    inside it, and absolute paths must stay within it (symlinks resolved).
    Operators without a working_dir are unrestricted.

    Returns (resolved_path, error). Exactly one is None.
    """
    if not operator or not getattr(operator, "working_dir", None):
        return path, None

    base = os.path.realpath(operator.working_dir)
    candidate = path if os.path.isabs(path) else os.path.join(base, path)
    target = os.path.realpath(candidate)

    try:
        inside = os.path.commonpath([base, target]) == base
    except ValueError:  # different drives (Windows) or mixed abs/rel
        inside = False

    if not inside:
        return None, (
            f"Access denied: '{path}' is outside this operator's working "
            f"directory ({operator.working_dir})."
        )
    return target, None


async def shell_exec(arguments: dict[str, Any], operator: Any = None, **kwargs) -> dict[str, Any]:
    """Execute a shell command locally. Confined to the operator's working_dir if set."""
    command = arguments.get("command")
    if not command:
        return {"error": "command is required", "exit_code": 1}

    requested_dir = arguments.get("working_dir")
    if operator is not None and getattr(operator, "working_dir", None):
        working_dir, err = _confine(requested_dir or ".", operator)
        if err:
            return {"error": err, "exit_code": 1}
        os.makedirs(working_dir, exist_ok=True)
    else:
        working_dir = requested_dir or os.getcwd()
    timeout = arguments.get("timeout", 30)

    logger.info("shell_exec", command=command, cwd=working_dir)
    try:
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=working_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        return {
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": stderr.decode("utf-8", errors="replace"),
            "exit_code": process.returncode,
        }
    except TimeoutError:
        return {"error": f"Command timed out after {timeout}s", "exit_code": -1}
    except Exception as e:
        return {"error": str(e), "exit_code": -1}


async def fs_read(arguments: dict[str, Any], operator: Any = None, **kwargs) -> dict[str, Any]:
    """Read file contents from local filesystem."""
    path = arguments.get("path")
    if not path:
        return {"error": "path is required"}

    path, err = _confine(path, operator)
    if err:
        return {"error": err}

    max_lines = arguments.get("max_lines", 500)
    logger.info("fs_read", path=path)

    try:
        with open(path, errors="replace") as f:
            if max_lines:
                lines = []
                for i, line in enumerate(f):
                    if i >= max_lines:
                        lines.append(f"\n... (truncated at {max_lines} lines)")
                        break
                    lines.append(line)
                content = "".join(lines)
            else:
                content = f.read()
        return {"content": content, "path": path, "size": len(content)}
    except FileNotFoundError:
        return {"error": f"File not found: {path}"}
    except PermissionError:
        return {"error": f"Permission denied: {path}"}
    except Exception as e:
        return {"error": str(e)}


async def fs_write(arguments: dict[str, Any], operator: Any = None, **kwargs) -> dict[str, Any]:
    """Write content to a file on local filesystem."""
    path = arguments.get("path")
    content = arguments.get("content", "")
    mode = arguments.get("mode", "overwrite")

    if not path:
        return {"error": "path is required"}

    path, err = _confine(path, operator)
    if err:
        return {"error": err}

    logger.info("fs_write", path=path, mode=mode)
    try:
        file_mode = "a" if mode == "append" else "w"
        with open(path, file_mode) as f:
            f.write(content)
        return {"status": "success", "message": f"Wrote to {path}", "bytes": len(content)}
    except PermissionError:
        return {"error": f"Permission denied: {path}"}
    except Exception as e:
        return {"error": str(e)}


async def fs_list(arguments: dict[str, Any], operator: Any = None, **kwargs) -> dict[str, Any]:
    """List files and directories at a given path."""
    path = arguments.get("path", ".")
    recursive = arguments.get("recursive", False)

    path, err = _confine(path, operator)
    if err:
        return {"error": err}

    logger.info("fs_list", path=path, recursive=recursive)
    try:
        if recursive:
            entries = []
            for root, dirs, files in os.walk(path):
                rel = os.path.relpath(root, path)
                for d in dirs:
                    entries.append({"name": os.path.join(rel, d) if rel != "." else d, "type": "dir"})
                for f in files:
                    full = os.path.join(root, f)
                    stat = os.stat(full)
                    entries.append({
                        "name": os.path.join(rel, f) if rel != "." else f,
                        "type": "file",
                        "size": stat.st_size,
                    })
            return {"entries": entries, "count": len(entries)}
        else:
            raw = os.listdir(path)
            entries = []
            for name in sorted(raw):
                full = os.path.join(path, name)
                if os.path.isdir(full):
                    entries.append({"name": name, "type": "dir"})
                else:
                    stat = os.stat(full)
                    entries.append({"name": name, "type": "file", "size": stat.st_size})
            return {"entries": entries, "count": len(entries)}
    except FileNotFoundError:
        return {"error": f"Path not found: {path}"}
    except PermissionError:
        return {"error": f"Permission denied: {path}"}
    except Exception as e:
        return {"error": str(e)}
