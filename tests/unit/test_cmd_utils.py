# pylint: disable=redefined-outer-name
# pylint: disable=unused-argument
# pylint: disable=unused-import
# pylint: disable=unused-variable
# pylint: disable=wildcard-import

import pytest

from simcore_service_deployment_agent import cmd_utils, exceptions


async def test_valid_cmd(loop):
    await cmd_utils.run_cmd_line(["whoami"])


async def test_invalid_cmd(loop):
    with pytest.raises(exceptions.CmdLineError):
        await cmd_utils.run_cmd_line(["whoamiasd"])
