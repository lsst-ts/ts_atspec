#
# This file is part of ts_atspec.
#
# Developed for the Rubin Observatory Telescope and Site System.
# This product includes software developed by the LSST Project
# (https://www.lsst.org).
# See the COPYRIGHT file at the top-level directory of this distribution
# for details of code ownership.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
__all__ = ["MockSpectrographController"]

import asyncio
import logging
import typing

import numpy as np


class MockSpectrographController:
    """Mock Spectrograph Controller that talks over TCP/IP.

    Parameters
    ----------
    port : int
        TCP/IP port
    """

    def __init__(self, port: int) -> None:
        self.port = port

        self.log = logging.getLogger("MockATSpectrographController")

        self._server: typing.Optional[asyncio.base_events.Server] = None

        self.wait_time = 1.0
        self.wait_time_move = 5.0

        self.states = ["I", "M", "S", "X"]
        self.error = ["N", "B", "I", "T"]

        self._fw_state = 2
        self._fw_pos = 0
        self._fw_err = 0

        self.fw_limit = (0, 3)

        self._gw_state = 2
        self._gw_pos = 0
        self._gw_err = 0

        self.gw_limit = (0, 3)

        self._ls_state = 2
        self._ls_pos = 0.0
        self._ls_err = 0

        self.ls_limit = (0, 1000)
        self.ls_step = 1
        self.ls_step_time = 0.2

        self._cmds: typing.Dict[
            str,
            typing.Optional[
                typing.Callable[[str], typing.Coroutine[typing.Any, typing.Any, bytes]]
            ],
        ] = {
            "!XXX": None,
            "!LDC": None,
            "?FWS": self.fws,
            "?GRS": self.grs,
            "?LSS": self.lss,
            "?LSL": None,
            "?GRP": None,
            "?FWP": None,
            "!FWI": self.fwi,
            "!GRI": self.gwi,
            "!LSI": self.lsi,
            "!FWM": self.fwm,
            "!GRM": self.grm,
            "!LSM": self.lsm,
        }

    @property
    def initialized(self) -> bool:
        return self._server is not None

    @property
    def host(self) -> str:
        return "127.0.0.1"

    async def start(self) -> None:
        """Start the TCP/IP server, set start_task Done
        and start the command loop.
        """
        if self.initialized:
            self.log.debug("Server initialized.")
            return

        self._server = await asyncio.start_server(
            self.cmd_loop, host=self.host, port=self.port
        )

    async def stop(self, timeout: float = 5.0) -> None:
        """Stop the TCP/IP server.

        Parameters
        ----------
        timeout : float
            Timeout to wait server to stop (in seconds).
        """
        if self._server is None:
            return

        server = self._server
        self._server = None
        server.close()
        await asyncio.wait_for(server.wait_closed(), timeout=timeout)

    async def cmd_loop(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Command loop.

        Parameters
        ----------
        reader : asyncio.StreamReader
            Stream reader.

        writer : asyncio.StreamWriter
            Stream writer.
        """
        self.log.info("cmd_loop begins")

        # Write welcome message
        writer.write("\r\nSpectrograph\r\n>".encode())
        await writer.drain()

        while True:
            # Write string specifing that server is ready
            line = (await reader.readline()).decode()
            if not line:
                # connection lost; close the writer and exit the loop
                writer.close()
                return
            line = line.strip()
            self.log.debug(f"read command: {line!r}")
            if line:
                try:
                    if line[:4] in self._cmds:
                        cmd = self._cmds[line[:4]]
                        assert callable(cmd)
                        reply = await cmd(line[4:])
                        self.log.debug(f"reply: {reply!r}")
                        writer.write(reply)
                        await writer.drain()
                    else:
                        writer.write(" ?Unknown\r\n".encode())
                        await writer.drain()
                except Exception:
                    writer.write(" ?Unknown\r\n".encode())
                    await writer.drain()
                    self.log.exception(f"command {line} failed")
                writer.write(">".encode())
                await writer.drain()

    async def fws(self, val: str) -> bytes:
        """return filter wheel status

        Parameters
        ----------
        val : str
            Ignored
        """
        await asyncio.sleep(self.wait_time)
        return f" {self.states[self._fw_state]} {self._fw_pos} {self.error[self._fw_err]}\r\n".encode()

    async def grs(self, val: str) -> bytes:
        """return grating wheel status

        Parameters
        ----------
        val : str
            Ignored
        """
        await asyncio.sleep(self.wait_time)
        return f" {self.states[self._gw_state]} {self._gw_pos} {self.error[self._gw_err]}\r\n".encode()

    async def lss(self, val: str) -> bytes:
        """return linear stage status

        Parameters
        ----------
        val : str
            Ignored
        """
        await asyncio.sleep(self.wait_time)
        return f" {self.states[self._ls_state]} {self._ls_pos} {self.error[self._ls_err]}\r\n".encode()

    async def fwi(self, val: str) -> bytes:
        """home filter wheel

        Parameters
        ----------
        val : str
            Ignored
        """
        self._fw_state = 0
        self._fw_pos = 0
        self._fw_err = 0
        self.log.debug("fw homing started...")
        await asyncio.sleep(self.wait_time)
        self.log.debug("fw homing completed...")
        self._fw_state = 2

        return " ".encode()

    async def gwi(self, val: str) -> bytes:
        """home filter wheel.

        Parameters
        ----------
        val : str
            Ignored
        """
        self._gw_state = 0
        self._gw_pos = 0
        self._gw_err = 0
        self.log.debug("gw homing started...")
        await asyncio.sleep(self.wait_time)
        self.log.debug("gw homing completed...")
        self._gw_state = 2

        return " ".encode()

    async def lsi(self, val: str) -> bytes:
        """home linear stage.

        Parameters
        ----------
        val : str
            Ignored
        """
        self._ls_state = 0
        self._ls_pos = 0
        self._ls_err = 0
        self.log.debug("ls homing started...")
        await asyncio.sleep(self.wait_time)
        self.log.debug("ls homing completed...")
        self._ls_state = 2

        return " ".encode()

    async def fwm(self, val: str) -> bytes:
        """Move filter wheel.

        Parameters
        ----------
        val : str
            Filter wheel position.
        """
        self.log.debug(f"Received {val!r}")
        try:
            new_pos = int(val)
            if self.fw_limit[0] <= new_pos <= self.fw_limit[1]:
                asyncio.create_task(self._execute_fw_move(new_pos))
                return " ".encode()
            else:
                return b"Invalid Argument"
        except Exception:
            return b"?Unknown"

    async def _execute_fw_move(self, new_position: int) -> None:
        """Execute move filter wheel.

        Parameters
        ----------
        new_position : int
            New filter wheel position.
        """
        if self._fw_pos != new_position:
            self.log.info(f"Moving filter wheel: {self._fw_pos} -> {new_position}.")
            self._fw_state = 1
            self._fw_pos = 3
            await asyncio.sleep(self.wait_time_move)
            self._fw_state = 2
            self._fw_pos = new_position
        else:
            self.log.info(f"Filter wheel already in position {new_position}.")

    async def grm(self, val: str) -> bytes:
        """Move grating wheel.

        Parameters
        ----------
        val : str
            Grating position.
        """
        try:
            new_pos = int(val)
            if self.gw_limit[0] <= new_pos <= self.gw_limit[1]:
                asyncio.create_task(self._execute_gw_move(new_pos))
                return " ".encode()
            else:
                return b"Invalid Argument"
        except Exception:
            return b"?Unknown"

    async def _execute_gw_move(self, new_position: int) -> None:
        """Execute move grating wheel.

        Parameters
        ----------
        new_position : int
            New grating wheel position.
        """
        if self._gw_pos != new_position:
            self.log.info(f"Moving grating wheel: {self._gw_pos} -> {new_position}.")
            self._gw_state = 1
            self._gw_pos = 3
            await asyncio.sleep(self.wait_time_move)
            self._gw_state = 2
            self._gw_pos = new_position
        else:
            self.log.info(f"Grating wheel already in position {new_position}")

    async def lsm(self, val: str) -> bytes:
        """Move linear stage.

        Parameters:
        -----------
        val : str
            Linear stage position.
        """

        try:
            new_pos = float(val)
            if self.ls_limit[0] <= new_pos <= self.ls_limit[1]:
                asyncio.create_task(self._execute_ls_move(new_pos))
                return " ".encode()
            else:
                return b"Invalid Argument"
        except Exception:
            return b"?Unknown"

    async def _execute_ls_move(self, new_position: float) -> None:
        """Execute move linear stage.

        Parameters
        ----------
        new_position : float
            New linear stage positon.
        """
        if self._ls_pos != new_position:
            self.log.info(f"Moving linear stage: {self._ls_pos} -> {new_position}.")
            self._ls_state = 1
            step = self.ls_step * (1.0 if new_position > self._ls_pos else -1.0)
            for current_position in np.arange(self._ls_pos, new_position, step):
                self.log.debug(f"linear stage position: {current_position}")
                self._ls_pos = current_position
                await asyncio.sleep(self.ls_step_time)
            self._ls_pos = new_position
            self._ls_state = 2
        else:
            self.log.info(f"Linear stage already in position {new_position}")
