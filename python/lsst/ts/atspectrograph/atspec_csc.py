import asyncio
import traceback
import time
import pathlib

from lsst.ts import salobj

from lsst.ts.idl.enums import ATSpectrograph

from .model import Model
from .mock_controller import MockSpectrographController

__all__ = ['CSC']

HEALTH_LOOP_DIED = -300
LS_ERROR = -301
FW_ERROR = -302
GW_ERROR = -303
CONNECTION_ERROR = -304


class CSC(salobj.ConfigurableCsc):
    """
    Configurable Commandable SAL Component (CSC) for the Auxiliary Telescope
    Spectrograph.
    """

    def __init__(self, config_dir=None, initial_state=salobj.State.STANDBY,
                 initial_simulation_mode=0):
        """
        Initialize AT Spectrograph CSC.
        """

        self.mock_ctrl = None

        schema_path = pathlib.Path(__file__).resolve().parents[4].joinpath("schema",
                                                                           "ATSpectrograph.yaml")

        super().__init__("ATSpectrograph", index=0,
                         schema_path=schema_path,
                         config_dir=config_dir,
                         initial_state=initial_state,
                         initial_simulation_mode=initial_simulation_mode)

        # Add a remote for the ATCamera to monitor if it is exposing or not.
        # If it is, reject commands that would cause motion.
        self.atcam_remote = salobj.Remote(self.domain,
                                          "ATCamera",
                                          include=["startIntegration",
                                                   "startReadout"])

        # Add a callback function to monitor exposures
        self.atcam_remote.evt_startIntegration.callback = self.monitor_start_integration_callback
        self.atcam_remote.evt_startReadout.callback = self.monitor_start_readout_callback

        # flag to monitor if camera is exposing or not, if True, motion
        # commands will be rejected.
        self.is_exposing = False

        self.model = Model(self.log)

        self.want_connection = False
        self._health_loop = None

        self.timeout = 5.

    async def end_start(self, id_data):
        """end do_start; called after state changes.

        This method call setup on the model, passing the selected setting.

        Parameters
        ----------
        id_data : `CommandIdData`
            Command ID and data
        """
        self.want_connection = True

        await super().end_start(id_data)

    async def end_enable(self, id_data):
        """End do_enable; called after state changes.

        This method will connect to the controller.

        Parameters
        ----------
        id_data : `CommandIdData`
            Command ID and data
        """

        # start connection with the controller
        if not self.model.connected:
            try:
                await self.model.connect()
            except Exception as e:
                self.evt_errorCode.set_put(errorCode=CONNECTION_ERROR,
                                           errorReport="Cannot connect to controller.",
                                           traceback=traceback.format_exc())
                self.fault()
                raise e

            self.want_connection = False

        for query, report_state in [("query_fw_status", "fwState"),
                                    ("query_gw_status", "gwState"),
                                    ("query_gs_status", "lsState")]:
            try:
                state = await getattr(self.model, query)(self.want_connection)
                self.log.debug(f"{query}: {state}")
                getattr(self, f"evt_{report_state}").set_put(state=state[0])
            except Exception as e:
                self.evt_errorCode.set_put(errorCode=CONNECTION_ERROR,
                                           errorReport=f"Cannot get information from model for "
                                                       f"{report_state}.",
                                           traceback=traceback.format_exc())
                self.fault()
                raise e

        self._health_loop = asyncio.ensure_future(self.health_monitor_loop())

        await super().end_enable(id_data)

    async def end_disable(self, id_data):

        try:
            await asyncio.wait_for(self._health_loop, timeout=self.timeout)
        except asyncio.TimeoutError as e:
            self.log.exception(e)
            self.log.error('Wait for health loop to complete timed out. Cancelling.')

            self._health_loop.cancel()

            try:
                await self._health_loop
            except asyncio.CancelledError:
                self.log.debug('Heath monitor loop canceled.')

        try:
            await self.model.disconnect()
        except Exception as e:
            self.evt_errorCode.set_put(errorCode=CONNECTION_ERROR,
                                       errorReport="Cannot disconnect from controller.",
                                       traceback=traceback.format_exc())
            self.fault()
            raise e

        await super().end_disable(id_data)

    async def health_monitor_loop(self):
        """A coroutine to monitor the state of the hardware."""

        while self.summary_state == salobj.State.ENABLED:
            try:
                ls_state = await self.model.query_gs_status(self.want_connection)
                fw_state = await self.model.query_fw_status(self.want_connection)
                gw_state = await self.model.query_gw_status(self.want_connection)

                if self.want_connection:
                    self.want_connection = False

                # Make sure none of the sub-components are in fault. Go to fault state if so.
                if ls_state[2] != ATSpectrograph.Error.NONE:
                    self.fault()
                    self.log.error(f"Linear stage in error: {ls_state}")
                    self.evt_errorCode.set_put(errorCode=LS_ERROR,
                                               errorReport=f"Linear stage in error: {ls_state}")
                    break
                elif fw_state[2] != ATSpectrograph.Error.NONE:
                    self.fault()
                    self.log.error(f"Filter wheel in error: {fw_state}")
                    self.evt_errorCode.set_put(errorCode=FW_ERROR,
                                               errorReport=f"Filter wheel  in error: {fw_state}")
                    break
                elif gw_state[2] != ATSpectrograph.Error.NONE:
                    self.fault()
                    self.log.error(f"Grating wheel in error: {gw_state}")
                    self.evt_errorCode.set_put(errorCode=GW_ERROR,
                                               errorReport=f"Grating wheel in error: {gw_state}")
                    break

                # # Publish state of each component
                # if ls_pstate is None or ls_pstate[0] != ls_state[0]:
                #     self.evt_lsState.set_put(lsState=ls_state[0])
                #
                # if fw_pstate is None or fw_pstate[0] != fw_pstate[0]:
                #     self.evt_fwState.set_put(fwState=fw_state[0])
                #
                # if gw_pstate is None or gw_pstate[0] != gw_state[0]:
                #     self.evt_gwState.set_put(gwState=gw_state[0])
                #
                # # Publish position of each component
                # # FIXME: Need to validate that *_state[1] can be converted to float and int
                # if ls_pstate is None or ls_pstate[1] != ls_state[1]:
                #     self.evt_reportedLinearStagePosition.set_put(reportedLinearStagePosition=float(ls_state[1]))
                #
                # if fw_pstate is None or fw_pstate[1] != fw_state[1]:
                #     self.evt_reportedFilterPosition.set_put(reportedFilterPosition=int(fw_state[1]))
                #
                # if gw_pstate is None or gw_pstate[1] != gw_state[1]:
                #     self.evt_reportedDisperserPosition.set_put(reportedDisperserPosition=int(gw_state[1]))
                #
                # ls_pstate = ls_state
                # fw_pstate = fw_state
                # gw_pstate = gw_state

                await asyncio.sleep(salobj.base_csc.HEARTBEAT_INTERVAL)
            except Exception as e:
                self.fault()
                self.log.exception(e)
                self.evt_errorCode.set_put(errorCode=HEALTH_LOOP_DIED,
                                           errorReport="Health loop died for some unspecified reason.",
                                           traceback=traceback.format_exc())

    async def do_changeDisperser(self, id_data):
        """Change the disperser element.

        Parameters
        ----------
        id_data : `CommandIdData`
            Command ID and data

        """
        self.assert_enabled("changeDisperser")
        self.assert_move_allowed("changeDisperser")

        if id_data.disperser > 0 and len(id_data.name) > 0:
            raise RuntimeError(f"Either disperser id or filter name must be selected. "
                               f"Got disperser={id_data.disperser} and name={id_data.name}")
        elif id_data.disperser == 0 and len(id_data.name) == 0:
            raise RuntimeError(f"Neither filter id or name where specified.")
        elif id_data.disperser < 0 or id_data.disperser > len(self.model.gratings):
            raise RuntimeError(f"Invalid filter id. Got {id_data.disperser}, must "
                               f"be between 0 and {len(self.model.gratings)}")
        elif id_data.disperser > 0:
            disperser_id = int(ATSpectrograph.DisperserPosition(id_data.disperser))
            disperser_name = str(list(self.model.gratings.keys())[disperser_id-1])
        else:
            disperser_name = id_data.name
            disperser_id = int(self.model.gratings[id_data.name])

        await self.move_element(query="query_gw_status",
                                move="move_gw",
                                position=disperser_id-1,
                                report="reportedDisperserPosition",
                                inposition="disperserInPosition",
                                report_state="gwState",
                                position_name=disperser_name)

    async def do_changeFilter(self, id_data):
        """Change filter.

        Parameters
        ----------
        id_data : `CommandIdData`
            Command ID and data

        """
        self.assert_enabled("changeFilter")
        self.assert_move_allowed("changeFilter")

        if id_data.filter > 0 and len(id_data.name) > 0:
            raise RuntimeError(f"Either filter id or filter name must be selected. "
                               f"Got filter={id_data.filter} and name={id_data.name}")
        elif id_data.filter == 0 and len(id_data.name) == 0:
            raise RuntimeError(f"Neither filter id or name where specified.")
        elif id_data.filter < 0 or id_data.filter > len(self.model.filters):
            raise RuntimeError(f"Invalid filter id. Got {id_data.filter}, must "
                               f"be between 0 and {len(self.model.filters)}")
        elif id_data.filter > 0:
            filter_id = int(ATSpectrograph.FilterPosition(id_data.filter))
            filter_name = str(list(self.model.filters.keys())[filter_id-1])
        else:
            filter_name = id_data.name
            filter_id = int(self.model.filters[id_data.name])

        await self.move_element(query="query_fw_status",
                                move="move_fw",
                                position=filter_id-1,
                                report="reportedFilterPosition",
                                inposition="filterInPosition",
                                report_state="fwState",
                                position_name=filter_name)

    async def do_homeLinearStage(self, id_data):
        """Home linear stage.

        Parameters
        ----------
        id_data : `CommandIdData`
            Command ID and data

        """
        self.assert_enabled("homeLinearStage")
        self.assert_move_allowed("homeLinearStage")

        await self.home_gs()

    async def do_moveLinearStage(self, id_data):
        """Move linear stage.

        Parameters
        ----------
        id_data : `CommandIdData`
            Command ID and data

        """
        self.assert_enabled("moveLinearStage")
        self.assert_move_allowed("moveLinearStage")

        await self.move_element(query="query_gs_status",
                                move="move_gs",
                                position=id_data.distanceFromHome,
                                report="reportedLinearStagePosition",
                                inposition="linearStageInPosition",
                                report_state="lsState")

    async def do_stopAllAxes(self, id_data):
        """Stop all axes.

        Parameters
        ----------
        id_data : `CommandIdData`
            Command ID and data

        """
        self.assert_enabled("stopAllAxes")

        await self.model.stop_all_motion(self.want_connection)

        # TODO: Report new state and position

    async def implement_simulation_mode(self, simulation_mode):
        """Implement going into or out of simulation mode.

        Parameters
        ----------
        simulation_mode : `int`
            Requested simulation mode; 0 for normal operation.

        Raises
        ------
        ExpectedError
            If ``simulation_mode`` is not a supported value.

        Notes
        -----
        Subclasses should override this method to implement simulation
        mode. The implementation should:

        * Check the value of ``simulation_mode`` and raise
          `ExpectedError` if not supported.
        * If ``simulation_mode`` is 0 then go out of simulation mode.
        * If ``simulation_mode`` is nonzero then enter the requested
          simulation mode.

        Do not check the current summary state, nor set the
        ``simulation_mode`` property nor report the new mode.
        All of that is handled `do_setSimulationMode`.
        """
        if simulation_mode not in (0, 1):
            raise salobj.ExpectedError(
                f"Simulation_mode={simulation_mode} must be 0 or 1")

        if self.simulation_mode == simulation_mode:
            return

        self.model.simulation_mode = simulation_mode

        if simulation_mode == 1:
            self.mock_ctrl = MockSpectrographController(port=self.model.port)
            await asyncio.wait_for(self.mock_ctrl.start(), timeout=2)
        elif simulation_mode == 0 and self.mock_ctrl is not None:
            await self.mock_ctrl.stop(timeout=2.)
            self.mock_ctrl = None

        if self.want_connection:
            await self.connect()

    async def move_element(self, query, move, position, report, inposition,
                           report_state, position_name=None):
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

        p_state = await getattr(self.model, query)(self.want_connection)

        moving_state = ATSpectrograph.Status.MOVING
        not_in_position = ATSpectrograph.Status.NOTINPOSITION

        # Send command to the controller. Limit is checked by model.
        if p_state[0] == ATSpectrograph.Status.STATIONARY:
            getattr(self, f"evt_{report_state}").set_put(state=moving_state)
            try:
                await getattr(self.model, move)(position)
            except Exception as e:
                getattr(self, f"evt_{report_state}").set_put(state=not_in_position)
                raise e
            if position_name is None:
                getattr(self, f"evt_{report}").set_put(position=p_state[1])
            else:
                getattr(self, f"evt_{report}").set_put(
                    position=ATSpectrograph.DisperserPosition.INBETWEEN.value,
                    name=f'{ATSpectrograph.DisperserPosition.INBETWEEN!r}')
            getattr(self, f"evt_{inposition}").set_put(inPosition=False)
        else:
            getattr(self, f"evt_{report_state}").set_put(state=p_state[0])
            raise RuntimeError(f"Cannot change position. Current state is {p_state}")

        # Need to wait for command to complete
        start_time = time.time()
        while True:
            state = await getattr(self.model, query)(self.want_connection)

            if p_state[0] != state[0]:
                getattr(self, f"evt_{report_state}").set_put(state=state[0])
                p_state = state

            if (state[0] == ATSpectrograph.Status.STATIONARY and
                    state[1]-position <= self.model.tolerance):
                if position_name is None:
                    getattr(self, f"evt_{report}").set_put(position=state[1])
                else:
                    getattr(self, f"evt_{report}").set_put(position=state[1]+1,
                                                           name=position_name)
                getattr(self, f"evt_{inposition}").set_put(inPosition=True)
                break
            elif time.time()-start_time > self.model.move_timeout:
                raise TimeoutError(f"Change position timed out trying to move to "
                                   f"position {position}.")

            await asyncio.sleep(0.5)

    async def home_element(self, query, home, report, inposition, report_state):
        """Utility method to home subcomponents.


        Parameters
        ----------
        query : str
            Name of the method that queries the status of the element. Must be one of the
            three options:
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
            Name of the method responsible for reporting the position. Must be one of the
            three options:
                - reportedDisperserPosition
                - reportedFilterPosition
                - reportedLinearStagePosition

        inposition : str
            Name of the method responsible for reporting that element is in position. Must be
            one of the three options:
                - disperserInPosition
                - filterInPosition
                - linearStageInPosition

        report_state : str
            Name of the method responsible for reporting the state of the element. Must be
            one of the three options:
                - gwState
                - fwState
                - lsState

        """

        current_state = getattr(self, f"evt_{report_state}").data.state
        stationary_state = ATSpectrograph.Status.STATIONARY
        homing_state = ATSpectrograph.Status.HOMING
        not_in_position = ATSpectrograph.Status.NOTINPOSITION

        if current_state != stationary_state:
            raise RuntimeError(f"Element {inposition.split('In')[0]}. Cannot home.")
        else:
            getattr(self, f"evt_{report_state}").set_put(state=homing_state)

        try:
            await getattr(self.model, home)()
            p_state = await getattr(self.model, query)(self.want_connection)
        except Exception as e:
            getattr(self, f"evt_{report_state}").set_put(state=not_in_position)
            raise e

        # Need to wait for command to complete
        start_time = time.time()
        while True:
            state = await getattr(self.model, query)(self.want_connection)

            if p_state[0] != state[0]:
                getattr(self, f"evt_{report_state}").set_put(state=state[0])
                p_state = state

            if state[0] == ATSpectrograph.Status.STATIONARY:
                getattr(self, f"evt_{report}").set_put(position=state[1])
                getattr(self, f"evt_{inposition}").set_put(inPosition=True)
                break
            elif time.time()-start_time > self.model.move_timeout:
                raise TimeoutError(f"Homing element failed...")

    def assert_move_allowed(self, action):
        """Assert that moving the spectrograph elements is allowed."""
        if self.is_exposing:
            raise salobj.base.ExpectedError(f"Camera is exposing, {action} is not allowed.")

    def monitor_start_integration_callback(self):
        """Set `is_exposing` flag to True."""
        self.is_exposing = True

    def monitor_start_readout_callback(self):
        """Set `is_exposing` flag to False."""
        self.is_exposing = False

    @staticmethod
    def get_config_pkg():
        return 'ts_config_latiss'

    async def configure(self, config):
        """Configure the CSC.

        Parameters
        ----------
        config : `object`
            The configuration as described by the schema at ``schema_path``,
            as a struct-like object.

        Notes
        -----
        Called when running the ``start`` command, just before changing
        summary state from `State.STANDBY` to `State.DISABLED`.
        """

        self.model.host = config.host
        self.model.port = config.port

        self.model.min_pos = config.min_pos
        self.model.max_pos = config.max_pos

        if self.model.min_pos >= self.model.max_pos:
            raise RuntimeError(f"Minimum linear stage position ({self.model.min_pos}) "
                               f"must be smaller than maximum ({self.model.max_pos}).")

        self.model.tolerance = config.tolerance

        if len(config.filters) == len(ATSpectrograph.FilterPosition)-1:
            self.model.filters = dict()
            for i, f in enumerate(ATSpectrograph.FilterPosition):
                print(self.model.filters)
                self.model.filters[config.filters[i]] = f
                if i == len(ATSpectrograph.FilterPosition)-2:
                    break
        else:
            raise RuntimeError(f"Invalid filter name configuration. Expected "
                               f"{len(ATSpectrograph.FilterPosition)} entries, got "
                               f"{len(config.filters)}")

        if len(config.gratings) == len(ATSpectrograph.DisperserPosition)-1:
            self.model.gratings = dict()
            for i, g in enumerate(ATSpectrograph.DisperserPosition):
                self.model.gratings[config.gratings[i]] = g
                if i == len(ATSpectrograph.DisperserPosition)-2:
                    break
        else:
            raise RuntimeError("Invalid grating name configuration. Expected "
                               f"{len(ATSpectrograph.DisperserPosition)} entries, got "
                               f"{len(config.gratings)}")

        print(self.model.filters)
        print(self.model.gratings)
        filters_str = ''
        for i, f in enumerate(self.model.filters):
            filters_str += str(f).lower()
            if i < len(self.model.filters) - 1:
                filters_str += ','

        gratings_str = ''
        for i, f in enumerate(self.model.gratings):
            gratings_str += str(f).lower()
            if i < len(self.model.gratings) - 1:
                gratings_str += ','

        self.evt_settingsApplied.set_put(host=self.model.host,
                                         port=self.model.port,
                                         linearStageMinPos=self.model.min_pos,
                                         linearStageMaxPos=self.model.max_pos,
                                         linearStageSpeed=0.,
                                         filterNames=filters_str,
                                         gratingNames=gratings_str,
                                         instrumentPort=config.instrument_port)

    async def close(self):
        if self.mock_ctrl is not None:
            await self.mock_ctrl.stop(timeout=self.timeout)

        await super().close()
