import asyncio
import pathlib
import time
import traceback
import typing
from sre_compile import isstring

from lsst.ts import salobj, utils
from lsst.ts.idl.enums import ATSpectrograph

from . import __version__
from .config_schema import CONFIG_SCHEMA
from .mock_controller import MockSpectrographController
from .model import Model

__all__ = ["CSC", "run_atspectrograph_csc"]

HEALTH_LOOP_DIED = 1
LS_ERROR = 2
FW_ERROR = 3
GW_ERROR = 4
CONNECTION_ERROR = 5


class CSC(salobj.ConfigurableCsc):
    """
    Configurable Commandable SAL Component (CSC) for the Auxiliary Telescope
    Spectrograph.

    Parameters
    ----------
    initial_state : `salobj.State` or `int` (optional)
        The initial state of the CSC. This is provided for unit testing,
        as real CSCs should start up in `lsst.ts.salobj.StateSTANDBY`,
        the default.
    simulation_mode : `int` (optional)
        Simulation mode.

    Notes
    -----
    **Simulation Modes**

    Supported simulation modes:

    * 0: regular operation
    * 1: simulation mode: start a mock TCP/IP controller and talk to it

    **Error Codes**

    * 1: Health monitoring loop died.
    * 2: Error in the linear stage.
    * 3: Error in the filter wheel.
    * 4: Error in the grating wheel.
    * 5: Error connecting to the hardware controller.
    """

    valid_simulation_modes = (0, 1)
    version = __version__

    def __init__(
        self,
        config_dir: typing.Union[str, pathlib.Path, None] = None,
        initial_state: salobj.State = salobj.State.STANDBY,
        simulation_mode: int = 0,
    ) -> None:
        # flag to monitor if camera is exposing or not, if True, motion
        # commands will be rejected.
        self.is_exposing = False

        self.want_connection = False
        self._health_loop = utils.make_done_future()

        self.timeout = 5.0

        super().__init__(
            "ATSpectrograph",
            index=0,
            config_schema=CONFIG_SCHEMA,
            config_dir=config_dir,
            initial_state=initial_state,
            simulation_mode=simulation_mode,
        )

        self.model = Model(self.log)

        self.mock_ctrl: typing.Optional[MockSpectrographController] = (
            MockSpectrographController(port=self.model.port)
            if simulation_mode == 1
            else None
        )

        self._report_position_options = dict(
            reportedFilterPosition=self.report_filter_position,
            reportedDisperserPosition=self.report_disperser_position,
            reportedLinearStagePosition=self.report_linear_stage_position,
        )
        # Add a remote for the ATCamera to monitor if it is exposing or not.
        # If it is, reject commands that would cause motion.
        self.atcam_remote = salobj.Remote(
            self.domain,
            "ATCamera",
            readonly=True,
            include=["startIntegration", "startReadout"],
        )

    async def start(self) -> None:
        await super().start()

        await self.atcam_remote.start_task

        # Add a callback function to monitor exposures
        self.atcam_remote.evt_startIntegration.callback = (
            self.monitor_start_integration_callback
        )
        self.atcam_remote.evt_startReadout.callback = (
            self.monitor_start_readout_callback
        )

    async def handle_summary_state(self) -> None:
        """Called after every state transition.

        If running in simulation mode, check if mock_ctrl has been initialized
        and, if not, wait for mock controller to start.

        Parameters
        ----------
        data : ATSpectrograph_command_start
            Command data

        """

        if self.mock_ctrl is not None and not self.mock_ctrl.initialized:
            await self.mock_ctrl.start()

        if not self.disabled_or_enabled and self.model.connected:
            self.log.warning(f"CSC in {self.summary_state!r} disconnecting...")
            try:
                self.model.disconnect()
            except Exception:
                self.log.exception("Error disconnecting from controller. Ignoring.")
                self.want_connection = True

        await super().handle_summary_state()

    async def end_start(self, data: salobj.type_hints.BaseMsgType) -> None:
        """end do_start; called after state changes.

        This method call setup on the model, passing the selected setting.

        Parameters
        ----------
        data : ATSpectrograph_command_start
            Command data
        """
        self.want_connection = True

        await super().end_start(data)

    async def begin_enable(self, data: salobj.type_hints.BaseMsgType) -> None:
        """Begin do_enable; called before state changes.

        Send CMD_INPROGRESS acknowledgement with estimated timeout.

        Parameters
        ----------
        id_data : `CommandIdData`
            Command ID and data
        """

        await self.cmd_enable.ack_in_progress(
            data, timeout=self.model.connection_timeout + self.timeout
        )

        await super().begin_enable(data)

    async def end_enable(self, data: salobj.type_hints.BaseMsgType) -> None:
        """End do_enable; called after state changes.

        This method will connect to the controller.

        Parameters
        ----------
        data : ATSpectrograph_command_enable
            Command data
        """

        # start connection with the controller
        if not self.model.connected:
            try:
                await self.model.connect()
            except Exception as e:
                await self.fault(
                    code=CONNECTION_ERROR,
                    report="Cannot connect to controller.",
                    traceback=traceback.format_exc(),
                )

                try:
                    await self.model.disconnect()
                except Exception:
                    self.log.exception(
                        "Ignoring exception while trying to disconnect from controller."
                    )

                raise e

            self.want_connection = False

        try:
            # Check/Report linear stage position. Home if position is out of
            # range.
            state = await self.model.query_gs_status(self.want_connection)
            self.log.debug(f"query_gs_status: {state}")

            await self.evt_lsState.set_write(state=state[0], force_output=True)
            if state[1] < 0.0:
                self.log.warning("Linear stage out of range. Homing.")
                await self.home_element(
                    query="query_gs_status",
                    home="init_gs",
                    report="reportedLinearStagePosition",
                    inposition="linearStageInPosition",
                    report_state="lsState",
                )
            else:
                await self.evt_reportedLinearStagePosition.set_write(position=state[1])
        except Exception as e:
            await self.fault(
                code=CONNECTION_ERROR,
                report="Cannot get information from model for linear stage.",
                traceback=traceback.format_exc(),
            )
            raise e

        try:
            # Check/Report Filter Wheel position.
            state = await self.model.query_fw_status(self.want_connection)
            self.log.debug(f"query_fw_status: {state}")

            await self.evt_fwState.set_write(state=state[0], force_output=True)
            await self.evt_reportedFilterPosition.set_write(
                slot=int(state[1]),
                name=self.filter_info["filter_name"][int(state[1])],
                band=self.filter_info["band"][int(state[1])],
                centralWavelength=self.filter_info["central_wavelength_filter"][
                    int(state[1])
                ],
                focusOffset=self.filter_info["offset_focus_filter"][int(state[1])],
                pointingOffsets=[
                    self.filter_info["offset_pointing_filter"]["x"][int(state[1])],
                    self.filter_info["offset_pointing_filter"]["y"][int(state[1])],
                ],
            )
            self.log.debug("sent evt_reportedFilterPosition in end_enable")
        except Exception as e:
            await self.fault(
                code=CONNECTION_ERROR,
                report="Cannot get information from model for filter wheel.",
                traceback=traceback.format_exc(),
            )
            raise e

        try:
            # Check/Report Grating/Disperser Wheel position.
            state = await self.model.query_gw_status(self.want_connection)
            self.log.debug(f"query_gw_status: {state}")
            await self.evt_gwState.set_write(state=state[0], force_output=True)
            await self.evt_reportedDisperserPosition.set_write(
                slot=int(state[1]),
                name=self.grating_info["grating_name"][int(state[1])],
                band=self.grating_info["band"][int(state[1])],
                pointingOffsets=[
                    self.grating_info["offset_pointing_grating"]["x"][int(state[1])],
                    self.grating_info["offset_pointing_grating"]["y"][int(state[1])],
                ],
            )
            self.log.debug("sent evt_reportedDisperserPosition in end_enable")

        except Exception as e:
            await self.fault(
                code=CONNECTION_ERROR,
                report="Cannot get information from model for "
                "grating/disperser wheel.",
                traceback=traceback.format_exc(),
            )
            raise e

        self._health_loop = asyncio.ensure_future(self.health_monitor_loop())

        await super().end_enable(data)

    async def end_disable(self, data: salobj.type_hints.BaseMsgType) -> None:
        # TODO: Russell Owen suggest using putting this in
        # `handle_summary_state` instead and calling it whenever the state
        # leaves "enabled".
        try:
            await asyncio.wait_for(self._health_loop, timeout=self.timeout)
        except asyncio.TimeoutError:
            self.log.exception(
                "Wait for health loop to complete timed out. Cancelling."
            )

            self._health_loop.cancel()

            try:
                await self._health_loop
            except asyncio.CancelledError:
                self.log.debug("Heath monitor loop canceled.")

        try:
            await self.model.disconnect()
        except Exception:
            self.log.exception("Error disconnecting from controller.")

        await super().end_disable(data)

    async def health_monitor_loop(self) -> None:
        """A coroutine to monitor the state of the hardware."""

        while self.summary_state == salobj.State.ENABLED:
            try:
                ls_state = await self.model.query_gs_status(self.want_connection)
                fw_state = await self.model.query_fw_status(self.want_connection)
                gw_state = await self.model.query_gw_status(self.want_connection)

                if self.want_connection:
                    self.want_connection = False

                # Make sure none of the sub-components are in fault. Go to
                # fault state if so.
                if ls_state[2] != ATSpectrograph.Error.NONE:
                    self.log.error(f"Linear stage in error: {ls_state}")
                    await self.fault(
                        code=LS_ERROR, report=f"Linear stage in error: {ls_state}"
                    )
                    break
                elif fw_state[2] != ATSpectrograph.Error.NONE:
                    self.log.error(f"Filter wheel in error: {fw_state}")
                    await self.fault(
                        code=FW_ERROR, report=f"Filter wheel  in error: {fw_state}"
                    )
                    break
                elif gw_state[2] != ATSpectrograph.Error.NONE:
                    await self.log.error(f"Grating wheel in error: {gw_state}")
                    self.fault(
                        code=GW_ERROR, report=f"Grating wheel in error: {gw_state}"
                    )
                    break

                await asyncio.sleep(salobj.base_csc.HEARTBEAT_INTERVAL)
            except Exception:
                await self.fault(
                    code=HEALTH_LOOP_DIED,
                    report="Health loop died for some unspecified reason.",
                    traceback=traceback.format_exc(),
                )

    async def do_changeDisperser(self, data: salobj.type_hints.BaseMsgType) -> None:
        """Change the disperser element.

        Parameters
        ----------
        data : ATSpectrograph_command_changeDisperser
            Command ID and data

        """
        self.assert_enabled("changeDisperser")
        self.assert_move_allowed("changeDisperser")

        gratings_name = self.grating_info["grating_name"]

        if len(data.name) > 0:
            if data.name not in self.grating_info["grating_name"]:
                raise RuntimeError(
                    f"Invalid disperser name={data.name}, must be one of {gratings_name}."
                )
            disperser_name = data.name
            disperser_id = self.grating_info["grating_name"].index(disperser_name)
        elif 0 <= data.disperser < self.n_grating:
            disperser_id = data.disperser
            disperser_name = self.grating_info["grating_name"][disperser_id]
        else:
            raise RuntimeError(
                f"Invalid input. disperser={data.disperser}, must be between "
                f"0-{self.n_grating-1}. name={data.name}, must be one of {gratings_name}."
            )

        await self.move_element(
            query="query_gw_status",
            move="move_gw",
            position=disperser_id,
            report="reportedDisperserPosition",
            inposition="disperserInPosition",
            report_state="gwState",
            position_name=disperser_name,
        )

    async def do_changeFilter(self, data: salobj.type_hints.BaseMsgType) -> None:
        """Change filter.

        Parameters
        ----------
        data : ATSpectrograph_command_changeFilter
            Command data

        """
        self.assert_enabled("changeFilter")
        self.assert_move_allowed("changeFilter")

        filters_name = self.filter_info["filter_name"]

        if len(data.name) > 0:
            if data.name not in self.filter_info["filter_name"]:
                raise RuntimeError(
                    f"Invalid filter name={data.name}, must be one of {filters_name}."
                )
            filter_name = data.name
            filter_id = self.filter_info["filter_name"].index(filter_name)
        elif 0 <= data.filter < self.n_filter:
            filter_id = data.filter
            filter_name = self.filter_info["filter_name"][filter_id]
        else:
            raise RuntimeError(
                f"Invalid input. filter={data.filter}, must be between"
                f"0-{self.n_filter-1}. name={data.name}, must be one of {filters_name}."
            )

        await self.move_element(
            query="query_fw_status",
            move="move_fw",
            position=filter_id,
            report="reportedFilterPosition",
            inposition="filterInPosition",
            report_state="fwState",
            position_name=filter_name,
        )

    async def do_homeLinearStage(self, data: salobj.type_hints.BaseMsgType) -> None:
        """Home linear stage.

        Parameters
        ----------
        data : ATSpectrograph_command_homeLinearStage
            Command data

        """
        self.assert_enabled("homeLinearStage")
        self.assert_move_allowed("homeLinearStage")

        await self.home_element(
            query="query_gs_status",
            home="init_gs",
            report="reportedLinearStagePosition",
            inposition="linearStageInPosition",
            report_state="lsState",
        )

    async def do_moveLinearStage(self, data: salobj.type_hints.BaseMsgType) -> None:
        """Move linear stage.

        Parameters
        ----------
        data : ATSpectrograph_command_moveLinearStage
            Command data

        """
        self.assert_enabled("moveLinearStage")
        self.assert_move_allowed("moveLinearStage")

        await self.move_element(
            query="query_gs_status",
            move="move_gs",
            position=data.distanceFromHome,
            report="reportedLinearStagePosition",
            inposition="linearStageInPosition",
            report_state="lsState",
        )

    async def do_stopAllAxes(self, data: salobj.type_hints.BaseMsgType) -> None:
        """Stop all axes.

        Parameters
        ----------
        data : ATSpectrograph_command_stopAllAxes
            Command data

        """
        self.assert_enabled("stopAllAxes")

        await self.model.stop_all_motion(self.want_connection)

        # TODO: Report new state and position since it will hold all previous
        # information, however low priority since this has never actually been
        # used.

    async def move_element(
        self,
        query: str,
        move: str,
        position: typing.Union[int, float],
        report: str,
        inposition: str,
        report_state: str,
        position_name: typing.Optional[str] = None,
    ) -> None:
        """A utility function to wrap the steps for moving the filter wheel,
        grating wheel and linear stage.

        Parameters
        ----------
        query : str
            Name of the method that queries the status of the element. Must be
            one of the three options:
                - query_gw_status
                - query_fw_status
                - query_ls_status

        move : str
            Name of the method that moves the element. Must be one of the
            three options:
                - move_gw
                - move_fw
                - move_ls

        position : int or float
            Position to move the wheel or the linear stage. Limits are
            checked by the controller and an exception is raised if out of
            range.

        report : str
            Name of the method responsible for reporting the position. Must
            be one of the three options:
                - reportedDisperserPosition
                - reportedFilterPosition
                - reportedLinearStagePosition

        inposition : str
            Name of the method responsible for reporting that element is in
            position. Must be one of the three options:
                - disperserInPosition
                - filterInPosition
                - linearStageInPosition

        report_state : str
            Name of the method responsible for reporting the state of the
            element. Must be one of the three options:
                - gwState
                - fwState
                - lsState
        position_name : str
            Name of the specified position.
        """

        # Verify this was called with an appropriate event
        if report not in [
            "reportedLinearStagePosition",
            "reportedFilterPosition",
            "reportedDisperserPosition",
        ]:
            raise RuntimeError(
                "Expected report = reportedLinearStagePosition, reportedFilterPosition or "
                f"reportedDisperserPosition, but got {report}"
            )

        state = await getattr(self.model, query)(self.want_connection)
        await getattr(self, f"evt_{report_state}").set_write(state=state[0])

        # Send command to the controller. Limit is checked by model.
        if state[0] == ATSpectrograph.Status.STATIONARY:
            await getattr(self, f"evt_{report_state}").set_write(state=state[0])
            try:
                await getattr(self.model, move)(position)
            except Exception as e:
                await getattr(self, f"evt_{report_state}").set_write(
                    state=ATSpectrograph.Status.NOTINPOSITION
                )
                raise e
            if position_name is None:
                # this will be for the linear stage only since it's the only
                # topic with a position attribute
                await getattr(self, f"evt_{report}").set_write(position=state[1])
            await getattr(self, f"evt_{inposition}").set_write(inPosition=False)
        else:
            raise RuntimeError(
                f"Cannot change position. Current state is {ATSpectrograph.Status(state)!r}, "
                f"expected {ATSpectrograph.Status.STATIONARY!r}."
            )

        # Need to wait for command to complete
        start_time = time.time()
        while True:
            state = await getattr(self.model, query)(self.want_connection)

            await getattr(self, f"evt_{report_state}").set_write(state=state[0])

            if (
                state[0] == ATSpectrograph.Status.STATIONARY
                and state[1] - position <= self.model.tolerance
            ):
                await self._report_position_options[report](
                    position=state[1],
                    position_name=str(position_name if isstring(position_name) else ""),
                )

                await getattr(self, f"evt_{inposition}").set_write(inPosition=True)
                break
            elif time.time() - start_time > self.model.move_timeout:
                raise TimeoutError(
                    "Change position timed out trying to move to "
                    f"position {position}."
                )

            await asyncio.sleep(0.5)

    async def home_element(
        self, query: str, home: str, report: str, inposition: str, report_state: str
    ) -> None:
        """Utility method to home subcomponents.


        Parameters
        ----------
        query : str
            Name of the method that queries the status of the element. Must be
            one of the three options:
                - query_gw_status
                - query_fw_status
                - query_ls_status

        home : str
            Name of the method that initializes element. Must be one of the
            three options:
                - init_gw
                - init_fw
                - init_ls

        report : str
            Name of the method responsible for reporting the position. Must be
            one of the three options:
                - reportedDisperserPosition
                - reportedFilterPosition
                - reportedLinearStagePosition

        inposition : str
            Name of the method responsible for reporting that element is in
            position. Must be one of the three options:
                - disperserInPosition
                - filterInPosition
                - linearStageInPosition

        report_state : str
            Name of the method responsible for reporting the state of the
            element. Must be one of the three options:
                - gwState
                - fwState
                - lsState

        """

        current_state = await getattr(self.model, query)(self.want_connection)
        stationary_state = ATSpectrograph.Status.STATIONARY
        homing_state = ATSpectrograph.Status.HOMING
        not_in_position = ATSpectrograph.Status.NOTINPOSITION

        if current_state[0] != stationary_state:
            raise RuntimeError(
                f"Element {inposition.split('In')[0]} in {current_state}. "
                f"Must be in {stationary_state}. Cannot home."
            )
        else:
            await getattr(self, f"evt_{report_state}").set_write(state=homing_state)

        try:
            await getattr(self.model, home)()
            p_state = await getattr(self.model, query)(self.want_connection)
        except Exception as e:
            await getattr(self, f"evt_{report_state}").set_write(
                state=not_in_position, force_output=True
            )
            raise e

        await getattr(self, f"evt_{inposition}").set_write(
            inPosition=False, force_output=True
        )

        # Need to wait for command to complete
        start_time = time.time()
        while True:
            state = await getattr(self.model, query)(self.want_connection)

            if p_state[0] != state[0]:
                await getattr(self, f"evt_{report_state}").set_write(state=state[0])
                p_state = state

            if state[0] == ATSpectrograph.Status.STATIONARY:
                await getattr(self, f"evt_{report}").set_write(
                    position=state[1], force_output=True
                )
                await getattr(self, f"evt_{inposition}").set_write(
                    inPosition=True, force_output=True
                )
                break
            elif time.time() - start_time > self.model.move_timeout:
                raise TimeoutError("Homing element failed...")

            await asyncio.sleep(0.1)

    def assert_move_allowed(self, action: str) -> None:
        """Assert that moving the spectrograph elements is allowed."""
        if self.is_exposing:
            raise salobj.base.ExpectedError(
                f"Camera is exposing, {action} is not allowed."
            )

    def monitor_start_integration_callback(
        self, data: salobj.type_hints.BaseMsgType
    ) -> None:
        """Set `is_exposing` flag to True."""
        self.is_exposing = True

    def monitor_start_readout_callback(
        self, data: salobj.type_hints.BaseMsgType
    ) -> None:
        """Set `is_exposing` flag to False."""
        self.is_exposing = False

    @staticmethod
    def get_config_pkg() -> str:
        return "ts_config_latiss"

    async def configure(self, config: typing.Any) -> None:
        """Configure the CSC.

        Parameters
        ----------
        config : object
            The configuration as described by the schema at ``schema_path``,
            as a struct-like object.

        Notes
        -----
        Called when running the ``start`` command, just before changing
        summary state from `State.STANDBY` to `State.DISABLED`.
        """

        if self.simulation_mode == 0:
            self.model.host = config.host
            self.model.port = config.port
        else:
            assert isinstance(self.mock_ctrl, MockSpectrographController)
            if config.host != self.mock_ctrl.host or config.port != self.mock_ctrl.port:
                self.log.warning(
                    f"Running in simulation mode ({self.simulation_mode}). "
                    f"Overriding host/port from configuration file {config.host}:{config.port} "
                    f"to {self.mock_ctrl.host}:{self.mock_ctrl.port}"
                )
            self.model.host = self.mock_ctrl.host
            self.model.port = self.mock_ctrl.port

        self.model.connection_timeout = config.connection_timeout
        self.model.read_timeout = config.response_timeout
        self.model.move_timeout = config.move_timeout

        self.model.min_pos = config.min_pos
        self.model.max_pos = config.max_pos

        if self.model.min_pos >= self.model.max_pos:
            raise RuntimeError(
                f"Minimum linear stage position ({self.model.min_pos}) "
                f"must be smaller than maximum ({self.model.max_pos})."
            )

        self.model.tolerance = config.tolerance

        self.filter_info = config.filters
        self.grating_info = config.gratings

        self.n_filter = self.check_fg_config(self.filter_info)
        self.n_grating = self.check_fg_config(self.grating_info)

        # settingsApplied needs to publish the comma separated string
        filters_str = {
            "filter_name": "",
            "central_wavelength_filter": "",
            "offset_focus_filter": "",
            "offset_pointing_filter": "",
        }

        for i in range(self.n_filter):
            filters_str["filter_name"] += self.filter_info["filter_name"][i]
            filters_str["central_wavelength_filter"] += str(
                self.filter_info["central_wavelength_filter"][i]
            )
            filters_str["offset_focus_filter"] += str(
                self.filter_info["offset_focus_filter"][i]
            )
            filters_str["offset_pointing_filter"] += (
                "["
                + str((self.filter_info["offset_pointing_filter"])["x"][i])
                + ","
                + str((self.filter_info["offset_pointing_filter"])["y"][i])
                + "]"
            )
            # need to add comma, except for the last value
            if i < self.n_filter - 1:
                # loop over keys to add a comma for each
                for key in filters_str:
                    filters_str[key] += ","

        gratings_str = {
            "grating_name": "",
            "offset_focus_grating": "",
            "offset_pointing_grating": "",
        }
        for i in range(self.n_grating):
            gratings_str["grating_name"] += self.grating_info["grating_name"][i]
            gratings_str["offset_focus_grating"] += str(
                self.grating_info["offset_focus_grating"][i]
            )
            gratings_str["offset_pointing_grating"] += (
                "["
                + str((self.grating_info["offset_pointing_grating"])["x"][i])
                + ","
                + str((self.grating_info["offset_pointing_grating"])["y"][i])
                + "]"
            )

            # need to add comma, except for the last value
            if i < self.n_grating - 1:
                # loop over keys to add a comma for each, do not add a space
                # after the comma!
                for key in gratings_str:
                    gratings_str[key] += ","

        await self.evt_settingsAppliedValues.set_write(
            host=self.model.host,
            port=self.model.port,
            linearStageMinPos=self.model.min_pos,
            linearStageMaxPos=self.model.max_pos,
            linearStageSpeed=0.0,
            filterNames=filters_str["filter_name"],
            filterCentralWavelengths=filters_str["central_wavelength_filter"],
            filterFocusOffsets=filters_str["offset_focus_filter"],
            filterPointingOffsets=filters_str["offset_pointing_filter"],
            gratingNames=gratings_str["grating_name"],
            gratingFocusOffsets=gratings_str["offset_focus_grating"],
            gratingPointingOffsets=gratings_str["offset_pointing_grating"],
            instrumentPort=config.instrument_port,
            connectionTimeout=self.model.connection_timeout,
            responseTimeout=self.model.read_timeout,
            moveTimeout=self.model.move_timeout,
        )
        # Set the field but don't write.
        self.evt_configurationApplied.set(otherInfo="settingsAppliedValues")

    async def close(self) -> None:
        if self.mock_ctrl is not None:
            await self.mock_ctrl.stop(timeout=self.timeout)

        await super().close()

    @staticmethod
    def check_fg_config(config: typing.Dict[str, typing.Any]) -> int:
        """Check Filter/Grating configuration integrity.

        Parameters
        ----------
        config: `dict`
            Dictionary with arrays for each key.

        Returns
        -------
        size: `int`
            Size of the arrays in the dictionary.

        Raises
        ------
        RuntimeError
            If arrays have different sizes.
        """
        offset_pointing_name = (
            "offset_pointing_filter"
            if "offset_pointing_filter" in config
            else "offset_pointing_grating"
        )

        n_info = [len(config[info]) for info in config if info != offset_pointing_name]
        n_info.append(len(config[offset_pointing_name]["x"]))
        n_info.append(len(config[offset_pointing_name]["y"]))

        if not (all([n_size == n_info[0] for n_size in n_info])):
            size_report = dict(
                [
                    (info_entry, len(config[info_entry]))
                    for info_entry in config
                    if info_entry != offset_pointing_name
                ]
            )
            size_report[f"{offset_pointing_name}[x]"] = len(
                config[offset_pointing_name]["x"]
            )
            size_report[f"{offset_pointing_name}[y]"] = len(
                config[offset_pointing_name]["y"]
            )
            raise RuntimeError(
                "Invalid input data. Need same number of values for "
                f"all attributes. Got {size_report}."
            )

        return n_info[0]

    async def report_filter_position(self, position: int, position_name: str) -> None:
        """Report the filter wheel position.

        Parameters
        ----------
        position : `int`
            Position of the element.
        position_name : `str`
            Name of the position.
        """
        await self.evt_reportedFilterPosition.set_write(
            slot=int(position),
            name=position_name,
            band=self.filter_info["band"][int(position)],
            centralWavelength=self.filter_info["central_wavelength_filter"][
                int(position)
            ],
            focusOffset=self.filter_info["offset_focus_filter"][int(position)],
            pointingOffsets=[
                self.filter_info["offset_pointing_filter"]["x"][int(position)],
                self.filter_info["offset_pointing_filter"]["y"][int(position)],
            ],
            force_output=True,
        )

    async def report_disperser_position(
        self, position: int, position_name: str
    ) -> None:
        """Report the disperser wheel position.

        Parameters
        ----------
        position : `int`
            Position of the element.
        position_name : `str`
            Name of the position.
        """
        await self.evt_reportedDisperserPosition.set_write(
            slot=int(position),
            name=position_name,
            band=self.grating_info["band"][int(position)],
            focusOffset=self.grating_info["offset_focus_grating"][int(position)],
            pointingOffsets=[
                self.grating_info["offset_pointing_grating"]["x"][int(position)],
                self.grating_info["offset_pointing_grating"]["y"][int(position)],
            ],
            force_output=True,
        )

    async def report_linear_stage_position(
        self, position: int, position_name: str
    ) -> None:
        """Report the linear stage position.

        Parameters
        ----------
        position : `int`
            Position of the element.
        position_name : `str`
            Name of the position.
        """
        await self.evt_reportedLinearStagePosition.set_write(
            position=position, force_output=True
        )


def run_atspectrograph_csc() -> None:
    """Run the ATSpectrograph CSC from the command line."""
    asyncio.run(CSC.amain(index=None))
