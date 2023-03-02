import asyncio
import logging
from typing import Optional

from .exceptions import CmdLineError

log = logging.getLogger(__name__)


async def run_cmd_line(
    cmd: list[str], cwd_: str = ".", *, strip_endline: bool = True
) -> Optional[str]:
    """

    returns output.strip('\n') or None if no outputs
    raises CmdLineError
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd_
        )
    except FileNotFoundError as e:
        raise CmdLineError(
            " ".join(cmd), "The command was invalid and the cmd call failed."
        ) from e

    stdout, stderr = await proc.communicate()
    log.debug("[{%s}] exited with %s]", cmd, proc.returncode)

    if proc.returncode > 0:
        error_data = ""
        if stderr:
            error_data = stderr.decode()
            log.debug("\n[stderr]%s", error_data)
        raise CmdLineError(cmd, error_data)

    if stdout:
        standard_output = stdout.decode()
        log.debug("\n[stdout]%s", standard_output)

        return standard_output.strip("\n") if strip_endline else standard_output
    return None


async def run_cmd_line_unsafe(cmd: str, cwd_: str = ".") -> str:
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd_,
    )

    stdout, stderr = await proc.communicate()
    log.debug("[{%s}] exited with %s]", cmd, proc.returncode)

    if proc.returncode > 0:
        error_data = ""
        if stderr:
            error_data = stderr.decode()
            log.debug("\n[stderr]%s", error_data)
        raise CmdLineError(cmd, error_data)

    if stdout:
        data = stdout.decode()
        log.debug("\n[stdout]%s", data)
        return data
    return _EMPTY_STRING
