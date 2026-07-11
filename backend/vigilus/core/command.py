"""Safe parsing for user-configured command lines.

Commands are executed directly as argv arrays. Shell control operators are
rejected rather than interpreted, so configured input cannot add shell stages
or expansions.
"""

from __future__ import annotations

import shlex

_SHELL_OPERATORS = frozenset(
    {"|", "||", "&", "&&", ";", "(", ")", "<", ">", "<<", ">>", "<&", ">&", "`", "$"}
)


def parse_command_argv(command: str, *, field_name: str = "command") -> list[str]:
    """Parse one executable plus arguments without invoking a shell.

    Quoting is supported for arguments containing whitespace. Shell pipelines,
    redirects, substitutions, variable expansion, and command chaining are
    deliberately rejected; callers must model those operations as separate
    argv-based steps.
    """
    if not isinstance(command, str) or not command.strip():
        raise ValueError(f"{field_name} must contain an executable")
    if "\n" in command or "\r" in command:
        raise ValueError(f"{field_name} must be a single command line")

    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars="|&;()<>`$")
        lexer.whitespace_split = True
        lexer.commenters = ""
        argv = list(lexer)
    except ValueError as exc:
        raise ValueError(f"{field_name} contains invalid quoting") from exc

    if not argv:
        raise ValueError(f"{field_name} must contain an executable")
    if any(token in _SHELL_OPERATORS for token in argv):
        raise ValueError(
            f"{field_name} must not use shell operators; configure one executable and its arguments"
        )
    return argv
