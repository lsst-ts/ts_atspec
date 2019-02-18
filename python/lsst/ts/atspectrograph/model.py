
import os
import yaml
import asyncio

import SALPY_ATSpectrograph

__all__ = ['Model']

_LOCAL_HOST = "127.0.0.1"
_DEFAULT_PORT = 9999

_limit_decode = {"-": -1,
                 "0": 0,
                 "+": +1}


class FilterWheelStatus:
    """Class to store possible filter wheel status and error codes."""

    status = {"I": SALPY_ATSpectrograph.ATSpectrograph_shared_Status_Homing,
              "M": SALPY_ATSpectrograph.ATSpectrograph_shared_Status_Moving,
              "S": SALPY_ATSpectrograph.ATSpectrograph_shared_Status_Stationary,
              "X": SALPY_ATSpectrograph.ATSpectrograph_shared_Status_NotInPosition}

    error = {"N": SALPY_ATSpectrograph.ATSpectrograph_shared_Error_None,
             "B": SALPY_ATSpectrograph.ATSpectrograph_shared_Error_Busy,
             "I": SALPY_ATSpectrograph.ATSpectrograph_shared_Error_NotInitialized,
             "T": SALPY_ATSpectrograph.ATSpectrograph_shared_Error_MoveTimeout}

    def parse_status(self, status):
        """Parse status string.

        Parameters
        ----------
        status : str
            A string in the format "x # y" where x is status code, # is position and y is error code

        Returns
        -------
        status : tuple
            (status, position, error)

        """
        values = status.split(" ")
        try:
            values[2] = int(values[2])
        except ValueError as e:
            try:
                values[2] = float(values[2])
            except ValueError as e:
                values[2] = SALPY_ATSpectrograph.ATSpectrograph_shared_FilterPosition_Inbetween

        return self.status[values[1]], values[2], self.error[values[3]]


class GratingWheelStatus(FilterWheelStatus):
    """Class to store possible Grating wheel status and error codes."""
    pass


class GratingStageStatus(FilterWheelStatus):
    """Class to store possible Grating Stage status and error codes."""
    pass


class GratingWheelStepPosition(FilterWheelStatus):
    """Class to store possible Grating Wheel Step Position status and error codes."""

    def parse_status(self, status):
        """Parse status string.

        Parameters
        ----------
        status : str
            A string in the format "x # y" where x is status code, # is position and y is error code

        Returns
        -------
        status : tuple
            (status, position)

        """
        values = status.split(" ")
        return self.status[values[1]], int(values[2])


class FilterWheelStepPosition(GratingWheelStepPosition):
    """Class to store possible Filter Wheel Step Position status and error codes."""
    pass


class Model:
    """

    """
    def __init__(self, log):

        self.simulation_mode = 1

        self.log = log

        self.config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config/config.yaml')

        with open(self.config_path, 'r') as stream:
            self.config = yaml.load(stream)

        self.host = _LOCAL_HOST
        self.port = _DEFAULT_PORT
        self.connection_timeout = 10.
        self.read_timeout = 10.

        self.move_timeout = 60.

        self.connect_task = None
        self.reader = None
        self.writer = None

        self.cmd_lock = asyncio.Lock()
        self.controller_ready = False

    @property
    def recommended_settings(self):
        """Recommended settings property.

        Returns
        -------
        recommended_settings : str
            Recommended settings read from Model configuration file.
        """
        return self.config['settingVersions']['recommendedSettingsVersion']

    @property
    def settings_labels(self):
        """Recommended settings labels.

        Returns
        -------
        recommended_settings_labels : str
            Comma separated string with the valid setting labels read from Model configuration file.

        """
        valid_settings = ''

        n_set = len(self.config['settingVersions']['recommendedSettingsLabels'])
        for i, label in enumerate(self.config['settingVersions']['recommendedSettingsLabels']):
            valid_settings += label
            if i < n_set-1:
                valid_settings += ','

        return valid_settings

    def setup(self, setting):
        """Setup the model with the given setting.

        Parameters
        ----------
        setting : str
            A string with the selected setting label. Must match one on the configuration file.

        Returns
        -------

        """

        if len(setting) == 0:
            setting = self.config['settingVersions']['recommendedSettingsVersion']
            self.log.debug('Received empty setting label. Using default: %s', setting)

        if setting not in self.config['settingVersions']['recommendedSettingsLabels']:
            raise RuntimeError('Setting %s not a valid label. Must be one of %s.',
                               setting,
                               self.settings_labels)

        self.host = self.config['setting'][setting].get('host', _LOCAL_HOST)
        self.port = self.config['setting'][setting].get('port', _DEFAULT_PORT)
        # by default, not in simulation mode
        self.simulation_mode = self.config['setting'][setting].get('simulation', 0)

    async def stop_all_motion(self, want_connection=False):
        """Send command to stop all motions.

        Returns
        -------
        ret_val : str
            Response from controller.
        want_connection : bool
            Boolean to specify if a connection with the controller is to be opened in case it is
            closed.

        """
        ret_val = await self.run_command("!XXX\r\n", want_connection=want_connection)

        return self.check_return(ret_val)

    async def load_program_configuration_from(self, filename, want_connection=False):
        """

        Parameters
        ----------
        filename : str
            Name of file to load configuration from.
        want_connection : bool
            Boolean to specify if a connection with the controller is to be opened in case it is
            closed.

        Returns
        -------
        ret_val : str
            Response from controller.

        Raises
        ------
        RuntimeError

        """
        ret_val = await self.run_command(f"!LDC {filename}\r\n", want_connection=want_connection)

        return self.check_return(ret_val)

    async def query_fw_status(self, want_connection=False):
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
            Boolean to specify if a connection with the controller is to be opened in case it is
            closed.

        Raises
        ------
        RuntimeError

        """
        ret_val = await self.run_command("?FWS\r\n", want_connection=want_connection)

        return FilterWheelStatus().parse_status(self.check_return(ret_val))

    async def query_gw_status(self, want_connection=False):
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
            Boolean to specify if a connection with the controller is to be opened in case it is
            closed.

        Raises
        ------
        RuntimeError

        """
        ret_val = await self.run_command("?GRS\r\n", want_connection=want_connection)

        return GratingWheelStatus().parse_status(self.check_return(ret_val))

    async def query_gs_status(self, want_connection=False):
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
            Boolean to specify if a connection with the controller is to be opened in case it is
            closed.

        Raises
        ------
        RuntimeError

        """
        ret_val = await self.run_command("?LSS\r\n", want_connection=want_connection)

        return GratingStageStatus().parse_status(self.check_return(ret_val))

    async def query_gs_limit_switches(self, want_connection=False):
        """Query grating stage limit switches.

        Returned status:
            0 not at a limit,
            + at the positive limit,
            - at the negative limit/home

        Parameters
        ----------
        want_connection : bool
            Boolean to specify if a connection with the controller is to be opened in case it is
            closed.


        Returns
        -------
        ret_val : int
            -1, 0 or +1

        Raises
        ------
        RuntimeError
        """
        ret_val = await self.run_command("?LSL\r\n", want_connection=want_connection)

        return _limit_decode[self.check_return(ret_val).split(" ")[1]]

    async def query_gw_step_position(self, want_connection=False):
        """Query grating wheel step position.

        Parameters
        ----------
        want_connection : bool
            Boolean to specify if a connection with the controller is to be opened in case it is
            closed.

        Returns
        -------
        status : str
            I – initializing/homing,
            M – moving,
            S – stationary/not moving
        position : int
            current motor step position
        """
        ret_val = await self.run_command(f"?GRP\r\n", want_connection=want_connection)

        return GratingWheelStepPosition().parse_status(self.check_return(ret_val))

    async def query_fw_step_position(self, want_connection=False):
        """Query filter wheel step position.

        Parameters
        ----------
        want_connection : bool
            Boolean to specify if a connection with the controller is to be opened in case it is
            closed.

        Returns
        -------
        status : str
            I – initializing/homing,
            M – moving,
            S – stationary/not moving
        position : int
            current motor step position
        """
        ret_val = await self.run_command(f"?FWP\r\n", want_connection=want_connection)

        return FilterWheelStepPosition().parse_status(self.check_return(ret_val))

    async def init_fw(self, want_connection=False):
        """Initialize/home filter wheel

        Parameters
        ----------
        want_connection : bool
            Boolean to specify if a connection with the controller is to be opened in case it is
            closed.

        """
        ret_val = await self.run_command("!FWI\r\n", want_connection=want_connection)
        return self.check_return(ret_val)

    async def init_gw(self, want_connection=False):
        """initialize/home grating wheel.

        Parameters
        ----------
        want_connection : bool
            Boolean to specify if a connection with the controller is to be opened in case it is
            closed.
        """
        ret_val = await self.run_command("!GRI\r\n", want_connection=want_connection)
        return self.check_return(ret_val)

    async def init_gs(self, want_connection=False):
        """initialize grating stage to negative limit/home.

        Parameters
        ----------
        want_connection : bool
            Boolean to specify if a connection with the controller is to be opened in case it is
            closed.
        """
        ret_val = await self.run_command("!LSI\r\n", want_connection=want_connection)
        return self.check_return(ret_val)

    async def move_fw(self, pos, want_connection=False):
        """move filter wheel to position # (0-3)

        Parameters
        ----------
        pos : int
            Filter id (0-3).
        want_connection : bool
            Boolean to specify if a connection with the controller is to be opened in case it is
            closed.
        """
        if pos < 0 or pos > 3:
            raise RuntimeError(f"Out of range (0-3), got {pos}.")
        ret_val = await self.run_command(f"!FWM{pos}\r\n", want_connection=want_connection)
        return self.check_return(ret_val)

    async def move_gw(self, pos, want_connection=False):
        """move grating wheel to position # (0-3)

        Parameters
        ----------
        pos : int
            Grating id (0-3)
        want_connection : bool
            Boolean to specify if a connection with the controller is to be opened in case it is
            closed.

        """
        if pos < 0 or pos > 3:
            raise RuntimeError(f"Out of range (0-3), got {pos}.")
        ret_val = await self.run_command(f"!GRM{pos}\r\n", want_connection=want_connection)
        return self.check_return(ret_val)

    async def move_gs(self, pos, want_connection=False):
        """move grating stage to # (mm from home position)

        Parameters
        ----------
        pos : float
            Position from home (in mm).
        want_connection : bool
            Boolean to specify if a connection with the controller is to be opened in case it is
            closed.

        """
        # TODO: limit check?
        ret_val = await self.run_command(f"!LSM{pos}\r\n", want_connection=want_connection)
        return self.check_return(ret_val)

    async def connect(self):
        """Connect to the spectrograph controller's TCP/IP port.
        """
        self.log.debug(f"connecting to: {self.host}:{self.port}")
        if self.connected:
            raise RuntimeError("Already connected")
        host = _LOCAL_HOST if self.simulation_mode == 1 else self.host
        self.connect_task = asyncio.open_connection(host=host, port=self.port)
        self.reader, self.writer = await asyncio.wait_for(self.connect_task,
                                                          timeout=self.connection_timeout)

        # Read welcome message
        await asyncio.wait_for(self.reader.readuntil("\r\n".encode()),
                               timeout=self.read_timeout)

        read_bytes = await asyncio.wait_for(self.reader.readuntil("\r\n".encode()),
                                            timeout=self.read_timeout)

        self.log.debug(f"connected: {read_bytes.decode().rstrip()}")

    async def disconnect(self):
        """Disconnect from the spectrograph controller's TCP/IP port.
        """
        self.log.debug("disconnect")
        writer = self.writer
        self.reader = None
        self.writer = None
        if writer:
            try:
                writer.write_eof()
                await asyncio.wait_for(writer.drain(), timeout=2)
            finally:
                writer.close()

    async def run_command(self, cmd, want_connection=False):
        """Send a command to the TCP/IP controller and process its replies.
        Parameters
        ----------
        cmd : `str`
            The command to send, e.g. "5.0 MV", "SO" or "?".
        want_connection : bool
            Flag to specify if a connection is to be requested in case it is not connected.
        """

        self.log.debug(f"run_command: {cmd}")

        if not self.connected:
            if want_connection and self.connect_task is not None and not self.connect_task.done():
                await self.connect_task
            else:
                raise RuntimeError("Not connected and not trying to connect")
        async with self.cmd_lock:

            # Make sure controller is ready...
            try:
                read_bytes = await asyncio.wait_for(self.reader.read(1),
                                                    timeout=self.read_timeout)
                if read_bytes != b">":
                    raise RuntimeError(f"Controller not ready: Received '{read_bytes}'...")
            except Exception as e:
                await self.disconnect()
                raise e

            self.writer.write(f"{cmd}\n".encode())
            await self.writer.drain()

            if cmd.startswith("?"):
                try:
                    read_bytes = await asyncio.wait_for(self.reader.readuntil("\r\n".encode()),
                                                        timeout=self.read_timeout)
                except Exception as e:
                    await self.disconnect()
                    raise e
            else:
                read_bytes = await asyncio.wait_for(self.reader.read(1),
                                                    timeout=self.read_timeout)

            return read_bytes.decode()

    @property
    def connected(self):
        if None in (self.reader, self.writer):
            return False
        return True

    @staticmethod
    def check_return(ret_val):
        """A utility method to check the return value of a command and return

        Returns
        -------

        """
        return ret_val.rstrip()
