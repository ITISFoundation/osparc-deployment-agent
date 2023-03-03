""" Utils and extensions to 'subprocess' standard library


SEE https://docs.python.org/3/library/subprocess.html
SEE https://docs.python.org/3/library/asyncio-subprocess.html
"""


import asyncio
import logging
from asyncio.subprocess import Process
from typing import Optional, Union

from .exceptions import CmdLineError

log = logging.getLogger(__name__)


async def _wait_and_process_results(
    process: Process, *, command: Union[str, list[str]], strip_endline: bool = True
) -> Optional[str]:
    # waits
    stdout, stderr = await process.communicate()
    log.debug("[{%s}] exited with %s]", command, process.returncode)

    # process results
    if process.returncode > 0:
        error_data = ""
        if stderr:
            error_data = stderr.decode()
            log.debug("\n[stderr]%s", error_data)
        raise CmdLineError(command, error_data)

    if stdout:
        standard_output = stdout.decode()
        log.debug("\n[stdout]%s", standard_output)

        return standard_output.strip("\n") if strip_endline else standard_output
    return None


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

    return await _wait_and_process_results(
        proc, command=cmd, strip_endline=strip_endline
    )


async def run_cmd_line_unsafe(
    cmd: str, cwd_: str = ".", *, strip_endline: bool = True
) -> Optional[str]:
    """

    returns output.strip('\n') or None if no outputs
    raises CmdLineError
    """
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd_,
    )

    return await _wait_and_process_results(
        proc, command=cmd, strip_endline=strip_endline
    )
