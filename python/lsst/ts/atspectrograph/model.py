
import os
import yaml
import asyncio

__all__ = ['Model']

_LOCAL_HOST = "127.0.0.1"


class FilterWheelStatus:
    """Class to store possible filter wheel status and error codes."""

    status = {"I": "Initializing/Homing",
              "M": "Moving",
              "S": "Stationary/Not moving",
              "X": "Not at a filter position"}

    error = {"N": "None",
             "B": "busy",
             "I": "not initialized",
             "T": "move timed out"}

    @staticmethod
    def parse_status(status):
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
        return FilterWheelStatus.status[values[0]], values[1], FilterWheelStatus.error[values[2]]


class GratingWheelStatus(FilterWheelStatus):
    """Class to store possible Grating wheel status and error codes."""
    pass


class GratingStageStatus(FilterWheelStatus):
    """Class to store possible Grating Stage status and error codes."""


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
        self.port = 9999
        self.buffer_size = 20  # Vendor specifies that communicatino is aways 20 bytes long
        self.connect_task = None
        self.reader = None
        self.writer = None

        self.socket = None

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
            raise IOError('Setting %s not a valid label. Must be one of %s.',
                          setting,
                          self.get_settings())

    async def stop_all_motion(self):
        """Send command to stop all motions.

        Returns
        -------
        ret_val : str
            Response from controller.

        """
        ret_val = await self.run_command("!XXX\r\n")

        return self.check_return(ret_val)

    async def load_program_configuration_from(self, filename):
        """

        Parameters
        ----------
        filename : str
            Name of file to load configuration from.

        Returns
        -------
        ret_val : str
            Response from controller.

        Raises
        ------
        RuntimeError

        """
        ret_val = await self.run_command(f"!LDC {filename}\r\n")

        return self.check_return(ret_val)

    async def query_fw_status(self):
        """Query status of the filter wheel.

        Returns
        -------
        ret_val : str
            Response from controller.

        """
        ret_val = await self.run_command("?FWS\r\n")

        return FilterWheelStatus.parse_status(self.check_return(ret_val))

    async def query_gw_status(self):
        """Query status of the Grating Wheel.

        Returns
        -------

        """
        ret_val = await self.run_command("?GRS\r\n")

        return GratingWheelStatus.parse_status(self.check_return(ret_val))


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
        self.log.debug("connected")

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

        if not self.connected:
            if want_connection and self.connect_task is not None and not self.connect_task.done():
                await self.connect_task
            else:
                raise RuntimeError("Not connected and not trying to connect")
        async with self.cmd_lock:
            self.writer.write(f"{cmd}\n".encode())
            await self.writer.drain()

            try:
                read_bytes = await asyncio.wait_for(self.reader.readuntil("\r\n".encode()),
                                                    timeout=self.read_timeout)
            except Exception as e:
                await self.disconnect()
                raise e

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
        if ret_val[0] == "?":
            raise RuntimeError(f"{ret_val}")
        elif ret_val[0] == "!":
            return ret_val[1:]
        else:
            raise RuntimeError(f"Unrecognized value {ret_val}}")
