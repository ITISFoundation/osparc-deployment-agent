""" Utils and extensions to 'subprocess' standard library


SEE https://docs.python.org/3/library/subprocess.html
SEE https://docs.python.org/3/library/asyncio-subprocess.html
"""


import asyncio
import logging
import subprocess
from asyncio.subprocess import Process
from typing import Optional, Union

from .exceptions import CmdLineError

log = logging.getLogger(__name__)


#
# **ASYNCronous** helpers
#


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


async def exec_command(
    program_and_args: list[str], cwd: str = ".", *, strip_endline: bool = True
) -> Optional[str]:
    """Create a subprocess

    returns output.strip('\n') or None if no outputs
    raises CmdLineError
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *program_and_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
    except FileNotFoundError as e:
        raise CmdLineError(
            " ".join(program_and_args),
            "The command was invalid and the cmd call failed.",
        ) from e

    return await _wait_and_process_results(
        proc, command=program_and_args, strip_endline=strip_endline
    )


async def shell_command(
    cmd: str, cwd: str = ".", *, strip_endline: bool = True
) -> Optional[str]:
    """Run the cmd shell command

    returns output.strip('\n') or None if no outputs
    raises CmdLineError
    """
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )

    return await _wait_and_process_results(
        proc, command=cmd, strip_endline=strip_endline
    )


#
# **syncronous** helpers
#


def run_command(cmd: Union[str, list[str]], shell=True, **kwargs) -> str:
    """Thin wrapper for  subprocess.run

    If shell is True, the specified command will be executed through the shell.
    This can be useful if you are using Python primarily for the enhanced control
    flow it offers over most system shells and still want convenient access to other
    shell features such as shell pipes, filename wildcards, environment variable expansion,
    and expansion of ~ to a user's home directory. However, note that Python itself offers
    implementations of many shell-like features (in particular, glob, fnmatch, os.walk(),
    os.path.expandvars(), os.path.expanduser(), and shutil).

    returns command outputs

    raises supbrocess.CalledProcessError
    raises Timeout
    """

    result = subprocess.run(
        cmd,
        capture_output=True,
        check=True,
        shell=shell,
        encoding="utf-8",
        **kwargs,
    )
    return result.stdout.rstrip() if result.stdout else ""
