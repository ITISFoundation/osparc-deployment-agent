# pylint: disable=redefined-outer-name
# pylint: disable=unused-argument
# pylint: disable=unused-import
# pylint: disable=unused-variable
# pylint: disable=wildcard-import

from asyncio import AbstractEventLoop

import pytest

from simcore_service_deployment_agent import exceptions, subprocess_utils


async def test_valid_cmd(event_loop: AbstractEventLoop):
    output = await subprocess_utils.exec_command_async(["whoami"], strip_endline=False)
    assert output.endswith("\n")
    print(output)

    # default is strip_endline=True
    output_wo_endline = await subprocess_utils.exec_command_async(["whoami"])
    assert output_wo_endline == output.strip("\n")


async def test_valid_cmd_returns_None(event_loop: AbstractEventLoop):
    output = await subprocess_utils.exec_command_async(["echo"])
    assert not output


async def test_invalid_cmd(event_loop: AbstractEventLoop):
    with pytest.raises(exceptions.CmdLineError):
        await subprocess_utils.exec_command_async(["whoamiasd"])
