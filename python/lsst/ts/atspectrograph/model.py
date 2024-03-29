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
import asyncio
import enum
import logging
import typing

from lsst.ts.idl.enums import ATSpectrograph

__all__ = ["Model"]

_LOCAL_HOST = "127.0.0.1"
_DEFAULT_PORT = 9999

_limit_decode = {"-": -1, "0": 0, "+": +1}


class WheelStatus:
    """Store possible filter wheel status and error codes."""

    status = {
        "I": ATSpectrograph.Status.HOMING,
        "M": ATSpectrograph.Status.MOVING,
        "S": ATSpectrograph.Status.STATIONARY,
        "X": ATSpectrograph.Status.NOTINPOSITION,
    }

    error = {
        "N": ATSpectrograph.Error.NONE,
        "B": ATSpectrograph.Error.BUSY,
        "I": ATSpectrograph.Error.NOTINITIALIZED,
        "T": ATSpectrograph.Error.MOVETIMEOUT,
    }


class FilterWheelStatus(WheelStatus):
    """Store possible filter wheel status and error codes."""

    def parse_status(
        self, status: str
    ) -> typing.Tuple[enum.Enum, typing.Any, enum.Enum]:
        """Parse status string.

        Parameters
        ----------
        status : str
            A string in the format "x # y" where x is status code, # is
            position and y is error code

        Returns
        -------
        status : tuple
            (status, position, error)

        """
        values = status.split(" ")
        try:
            values[2] = int(values[2])  # type: ignore
        except ValueError:
            try:
                values[2] = float(values[2])  # type: ignore
            except ValueError:
                values[2] = ATSpectrograph.FilterPosition.INBETWEEN

        return self.status[values[1]], values[2], self.error[values[3]]


class GratingWheelStatus(FilterWheelStatus):
    """Store possible Grating wheel status and error codes."""

    pass


class GratingStageStatus(FilterWheelStatus):
    """Store possible Grating Stage status and error codes."""

    pass


class GratingWheelStepPosition(WheelStatus):
    """Store possible Grating Wheel Step Position status and error codes."""

    def parse_status(self, status: str) -> typing.Tuple[enum.Enum, typing.Any]:
        """Parse status string.

        Parameters
        ----------
        status : str
            A string in the format "x # y" where x is status code, # is
            position and y is error code

        Returns
        -------
        status : tuple
            (status, position)
        """
        values = status.split(" ")
        return self.status[values[1]], int(values[2])


class FilterWheelStepPosition(GratingWheelStepPosition):
    """Store possible Filter Wheel Step Position status and error codes."""

    pass


class Model:
    """ATSpectrogropah Model Class.

    This class implements an interface with the ATSpectrograph controller.

    Parameters
    ----------
    log : `logging.Logger`
        Parent logger.
    """

    def __init__(self, log: logging.Logger) -> None:
        self.simulation_mode = 0

        self.log = (
            logging.getLogger(type(self).__name__)
            if log is None
            else log.getChild(type(self).__name__)
        )

        self.host = _LOCAL_HOST
        self.port = _DEFAULT_PORT
        self.connection_timeout = 10.0
        self.read_timeout = 10.0

        self.move_timeout = 60.0

        self.connect_task: typing.Optional[
            typing.Coroutine[
                typing.Any,
                typing.Any,
                typing.Tuple[asyncio.StreamReader, asyncio.StreamWriter],
            ]
        ] = None
        self._reader: typing.Optional[asyncio.StreamReader] = None
        self._writer: typing.Optional[asyncio.StreamWriter] = None

        self.cmd_lock = asyncio.Lock()
        self.controller_ready = False

        self.min_pos = 0
        self.max_pos = 10000
        self.tolerance = 1e-2  # movement tolerance

    async def stop_all_motion(self, want_connection: bool = False) -> str:
        """Send command to stop all motions.

        Returns
        -------
        ret_val : str
            Response from controller.
        want_connection : bool
            Boolean to specify if a connection with the controller is to be
            opened in case it is closed.
        """
        ret_val = await self.run_command("!XXX\r\n", want_connection=want_connection)

        return self.check_return(ret_val)

    async def load_program_configuration_from(
        self, filename: str, want_connection: bool = False
    ) -> str:
        """Load configuration from file.

        Parameters
        ----------
        filename : str
            Name of file to load configuration from.
        want_connection : bool
            Boolean to specify if a connection with the controller is to be
            opened in case it is closed.

        Returns
        -------
        ret_val : str
            Response from controller.

        Raises
        ------
        RuntimeError
        """
        ret_val = await self.run_command(
            f"!LDC {filename}\r\n", want_connection=want_connection
        )

        return self.check_return(ret_val)

    async def query_fw_status(
        self, want_connection: bool = False
    ) -> typing.Tuple[enum.Enum, typing.Any, enum.Enum]:
        """Query status of the filter wheel.

        status : str
            I – initializing/homing,
            M – moving,
            S – stationary/not moving
        position : int
            current filter wheel position (0-3)
        error : str
            N - none,
            B - busy,
            I - not initialized,
            T - move timed out

        Parameters
        ----------
        want_connection : bool
            Boolean to specify if a connection with the controller is to be
            opened in case it is closed.

        Returns
        -------
        tuple
            (status, position, error)
        """
        ret_val = await self.run_command("?FWS\r\n", want_connection=want_connection)

        return FilterWheelStatus().parse_status(self.check_return(ret_val))

    async def query_gw_status(
        self, want_connection: bool = False
    ) -> typing.Tuple[enum.Enum, typing.Any, enum.Enum]:
        """Query status of the Grating Wheel.

        status : str
            I – initializing/homing,
            M – moving,
            S – stationary/not moving
        position : int
            current grating position (0-3)
        error : str
            N - none,
            B - busy,
            I - not initialized,
            T - move timed out

        Parameters
        ----------
        want_connection : bool
            Boolean to specify if a connection with the controller is to be
            opened in case it is closed.

        Returns
        -------
        status : tuple
            (status, position, error)
        """
        ret_val = await self.run_command("?GRS\r\n", want_connection=want_connection)

        return GratingWheelStatus().parse_status(self.check_return(ret_val))

    async def query_gs_status(
        self, want_connection: bool = False
    ) -> typing.Tuple[enum.Enum, typing.Any, enum.Enum]:
        """Query status of the Grating Linear Stage.

        status : str
            I – initializing/homing,
            M – moving,
            S – stationary/not moving
        position : int
            current motor step position
        error : str
            N - none,
            B - busy,
            I - not initialized,
            T - move timed out

        Parameters
        ----------
        want_connection : bool
            Boolean to specify if a connection with the controller is to be
            opened in case it is closed.

        Returns
        -------
        status : tuple
            (status, position, error)
        """
        ret_val = await self.run_command("?LSS\r\n", want_connection=want_connection)

        return GratingStageStatus().parse_status(self.check_return(ret_val))

    async def query_gs_limit_switches(self, want_connection: bool = False) -> int:
        """Query grating stage limit switches.

        Returned status:
            0 not at a limit,
            + at the positive limit,
            - at the negative limit/home

        Parameters
        ----------
        want_connection : bool
            Boolean to specify if a connection with the controller is to be
            opened in case it is closed.

        Returns
        -------
        int
            -1, 0 or +1
        """
        ret_val = await self.run_command("?LSL\r\n", want_connection=want_connection)

        return _limit_decode[self.check_return(ret_val).split(" ")[1]]

    async def query_gw_step_position(
        self, want_connection: bool = False
    ) -> typing.Tuple[enum.Enum, typing.Any]:
        """Query grating wheel step position.

        Parameters
        ----------
        want_connection : bool
            Boolean to specify if a connection with the controller is to be
            opened in case it is closed.

        Returns
        -------
        status : tuple
            (status, position)
        """
        ret_val = await self.run_command("?GRP\r\n", want_connection=want_connection)

        return GratingWheelStepPosition().parse_status(self.check_return(ret_val))

    async def query_fw_step_position(
        self, want_connection: bool = False
    ) -> typing.Tuple[enum.Enum, typing.Any]:
        """Query filter wheel step position.

        Parameters
        ----------
        want_connection : bool
            Boolean to specify if a connection with the controller is to be
            opened in case it is closed.

        Returns
        -------
        status : tuple
            (status, position)
        """
        ret_val = await self.run_command("?FWP\r\n", want_connection=want_connection)

        return FilterWheelStepPosition().parse_status(self.check_return(ret_val))

    async def init_fw(self, want_connection: bool = False) -> str:
        """Initialize/home filter wheel

        Parameters
        ----------
        want_connection : bool
            Boolean to specify if a connection with the controller is to be
            opened in case it is closed.

        Returns
        -------
        str
        """
        ret_val = await self.run_command("!FWI\r\n", want_connection=want_connection)
        return self.check_return(ret_val)

    async def init_gw(self, want_connection: bool = False) -> str:
        """initialize/home grating wheel.

        Parameters
        ----------
        want_connection : bool
            Boolean to specify if a connection with the controller is to be
            pened in case it is closed.

        Returns
        -------
        str
        """
        ret_val = await self.run_command("!GRI\r\n", want_connection=want_connection)
        return self.check_return(ret_val)

    async def init_gs(self, want_connection: bool = False) -> str:
        """initialize grating stage to negative limit/home.

        Parameters
        ----------
        want_connection : bool
            Boolean to specify if a connection with the controller is to be
            opened in case it is closed.

        Returns
        -------
        str
        """
        ret_val = await self.run_command("!LSI\r\n", want_connection=want_connection)
        return self.check_return(ret_val)

    async def move_fw(self, pos: int, want_connection: bool = False) -> str:
        """move filter wheel to position # (0-3)

        Parameters
        ----------
        pos : int
            Filter id (0-3).
        want_connection : bool
            Boolean to specify if a connection with the controller is to be
            opened in case it is closed.

        Returns
        -------
        str
        """
        if pos < 0 or pos > 3:
            raise RuntimeError(f"Out of range (0-3), got {pos}.")
        ret_val = await self.run_command(
            f"!FWM{pos}\r\n", want_connection=want_connection
        )
        return self.check_return(ret_val)

    async def move_gw(self, pos: int, want_connection: bool = False) -> str:
        """move grating wheel to position # (0-3)

        Parameters
        ----------
        pos : int
            Grating id (0-3)
        want_connection : bool
            Boolean to specify if a connection with the controller is to be
            opened in case it is closed.

        Returns
        -------
        str
        """
        if pos < 0 or pos > 3:
            raise RuntimeError(f"Out of range (0-3), got {pos}.")
        ret_val = await self.run_command(
            f"!GRM{pos}\r\n", want_connection=want_connection
        )
        return self.check_return(ret_val)

    async def move_gs(self, pos: float, want_connection: bool = False) -> str:
        """move grating stage to # (mm from home position)

        Parameters
        ----------
        pos : float
            Position from home (in mm).
        want_connection : bool
            Boolean to specify if a connection with the controller is to be
            opened in case it is closed.

        Returns
        -------
        str
        """
        # TODO: limit check?
        if not (self.min_pos <= pos <= self.max_pos):
            raise RuntimeError(
                f"Requested position {pos} outside limits "
                f"({self.min_pos} / {self.max_pos})."
            )
        ret_val = await self.run_command(
            f"!LSM{pos}\r\n", want_connection=want_connection
        )
        return self.check_return(ret_val)

    async def connect(self) -> None:
        """Connect to the spectrograph controller's TCP/IP port."""
        self.log.debug(f"connecting to: {self.host}:{self.port}")
        if self.connected:
            raise RuntimeError("Already connected")
        host = _LOCAL_HOST if self.simulation_mode == 1 else self.host
        self.connect_task = asyncio.open_connection(host=host, port=self.port)
        self.reader, self.writer = await asyncio.wait_for(
            self.connect_task, timeout=self.connection_timeout
        )

        # Read welcome message
        await asyncio.wait_for(
            self.reader.readuntil("\r\n".encode()), timeout=self.read_timeout
        )

        read_bytes = await asyncio.wait_for(
            self.reader.readuntil("\r\n".encode()), timeout=self.read_timeout
        )

        if "Spectrograph" not in read_bytes.decode().rstrip():
            raise RuntimeError("No welcome message from controller.")

        self.log.debug(f"connected: {read_bytes.decode().rstrip()}")

    async def disconnect(self) -> None:
        """Disconnect from the spectrograph controller's TCP/IP port."""
        self.log.debug("disconnect")
        writer = self.writer
        self.reset_reader_writer()
        if writer:
            try:
                writer.write_eof()
                await asyncio.wait_for(writer.drain(), timeout=2)
            finally:
                writer.close()

    async def run_command(self, cmd: str, want_connection: bool = False) -> str:
        """Send a command to the TCP/IP controller and process its replies.

        Parameters
        ----------
        cmd : `str`
            The command to send, e.g. "5.0 MV", "SO" or "?".
        want_connection : bool
            Flag to specify if a connection is to be requested in case it is
            not connected.

        Returns
        -------
        read_bytes : str
            Response from controller.
        """

        self.log.debug(f"run_command: {cmd}")

        if not self.connected:
            if want_connection and self.connect_task is not None:
                await self.connect_task
            else:
                raise RuntimeError("Not connected and not trying to connect")
        async with self.cmd_lock:
            # Make sure controller is ready...
            try:
                read_bytes = await asyncio.wait_for(
                    self.reader.read(1), timeout=self.read_timeout
                )
                if read_bytes != b">":
                    raise RuntimeError(
                        f"Controller not ready: Received '{read_bytes!r}'..."
                    )
            except Exception as e:
                await self.disconnect()
                raise e

            self.writer.write(f"{cmd}\n".encode())
            await self.writer.drain()

            if cmd.startswith("?"):
                try:
                    read_bytes = await asyncio.wait_for(
                        self.reader.readuntil("\r\n".encode()),
                        timeout=self.read_timeout,
                    )
                except Exception as e:
                    await self.disconnect()
                    raise e
            else:
                read_bytes = await asyncio.wait_for(
                    self.reader.read(1), timeout=self.read_timeout
                )

            return read_bytes.decode()

    def reset_reader_writer(self) -> None:
        """Reset reader and writer."""
        self._reader = None
        self._writer = None

    @property
    def connected(self) -> bool:
        return None not in (self._reader, self._writer)

    @property
    def reader(self) -> asyncio.StreamReader:
        assert isinstance(self._reader, asyncio.StreamReader)
        return self._reader

    @reader.setter
    def reader(self, reader: asyncio.StreamReader) -> None:
        self._reader = reader

    @property
    def writer(self) -> asyncio.StreamWriter:
        assert isinstance(self._writer, asyncio.StreamWriter)
        return self._writer

    @writer.setter
    def writer(self, writer: asyncio.StreamWriter) -> None:
        self._writer = writer

    @staticmethod
    def check_return(value: str) -> str:
        """A utility method to check the return value of a command and return.

        Parameters
        ----------
        value : str
            Value to check.

        Returns
        -------
        str
        """
        return value.rstrip()
