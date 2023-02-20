# pylint: disable=redefined-outer-name
# pylint: disable=unused-argument
# pylint: disable=unused-import
# pylint: disable=unused-variable
# pylint: disable=wildcard-import

from asyncio import AbstractEventLoop

import pytest

from simcore_service_deployment_agent import cmd_utils, exceptions


async def test_valid_cmd(event_loop: AbstractEventLoop):
    await cmd_utils.run_cmd_line(["whoami"])


async def test_invalid_cmd(event_loop: AbstractEventLoop):
    with pytest.raises(exceptions.CmdLineError):
        await cmd_utils.run_cmd_line(["whoamiasd"])
