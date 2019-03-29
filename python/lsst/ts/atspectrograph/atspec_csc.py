import asyncio
import traceback
import time
import logging

import SALPY_ATSpectrograph
import SALPY_ATCamera

from lsst.ts import salobj

from .model import Model, FilterWheelPosition, GratingWheelPosition
from .mock_controller import MockSpectrographController

__all__ = ['CSC']

HEALTH_LOOP_DIED = -300
LS_ERROR = -301
FW_ERROR = -302
GW_ERROR = -303


class CSC(salobj.BaseCsc):
    """
    Commandable SAL Component (CSC) for the Auxiliary Telescope Spectrograph.
    """

    def __init__(self, add_streamhandler=True):
        """
        Initialize AT Spectrograph CSC.
        """

        super().__init__(SALPY_ATSpectrograph)

        # Add a remote for the ATCamera to monitor if it is exposing or not.
        # If it is, reject commands that would cause motion.
        self.atcam_remote = salobj.Remote(SALPY_ATCamera, include=["startIntegration",
                                                                   "startReadout"])

        self.atcam_remote.evt_startIntegration.callback = self.monitor_start_integration_callback
        self.atcam_remote.evt_startReadout.callback = self.monitor_start_readout_callback

        # flag to monitor if camera is exposing or not, if True, motion
        # commands will be rejected.
        self.is_exposing = False

        # Add a callback function to monitor exposures
        if add_streamhandler:
            ch = logging.StreamHandler()
            self.log.addHandler(ch)

        self.model = Model(self.log)

        # Publish setting versions
        self.evt_settingVersions.set_put(recommendedSettingsVersion=self.model.recommended_settings,
                                         recommendedSettingsLabels=self.model.settings_labels)

        self.want_connection = False
        self._health_loop = None

    def begin_start(self, id_data):
        """Begin do_start; called before state changes.

        This method call setup on the model, passing the selected setting.

        Parameters
        ----------
        id_data : `CommandIdData`
            Command ID and data
        """
        self.model.setup(id_data.data.settingsToApply)
        self.want_connection = True

    async def do_enable(self, id_data):
        """Transition from `State.DISABLED` to `State.ENABLED`.

        Parameters
        ----------
        id_data : `CommandIdData`
            Command ID and data
        """
        self._do_change_state(id_data, "enable", [salobj.State.DISABLED], salobj.State.ENABLED)

        # start connection with the controller
        if not self.model.connected:
            await self.model.connect()
            self.want_connection = False

        for query, report_state in [("query_fw_status", "fwState"),
                                    ("query_gw_status", "gwState"),
                                    ("query_gs_status", "lsState")]:
            state = await getattr(self.model, query)(self.want_connection)
            self.log.debug(f"{query}: {state}")
            getattr(self, f"evt_{report_state}").set_put(state=state[0])

        self._health_loop = asyncio.ensure_future(self.health_monitor_loop())

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
                if ls_state[2] != SALPY_ATSpectrograph.ATSpectrograph_shared_Error_None:
                    self.fault()
                    self.log.error(f"Linear stage in error: {ls_state}")
                    self.evt_errorCode.set_put(errorCode=LS_ERROR,
                                               errorReport=f"Linear stage in error: {ls_state}")
                    break
                elif fw_state[2] != SALPY_ATSpectrograph.ATSpectrograph_shared_Error_None:
                    self.fault()
                    self.log.error(f"Filter wheel in error: {fw_state}")
                    self.evt_errorCode.set_put(errorCode=FW_ERROR,
                                               errorReport=f"Filter wheel  in error: {fw_state}")
                    break
                elif gw_state[2] != SALPY_ATSpectrograph.ATSpectrograph_shared_Error_None:
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

        if id_data.data.disperser > 0 and len(id_data.data.name) > 0:
            raise RuntimeError(f"Either disperser id or filter name must be selected. "
                               f"Got disperser={id_data.data.disperser} and name={id_data.data.name}")
        elif id_data.data.disperser > 0:
            disperser_id = int(GratingWheelPosition(id_data.data.disperser))
            disperser_name = str(list(self.model.gratings.keys())[disperser_id])
        else:
            disperser_name = id_data.data.name
            disperser_id = int(self.model.gratings[id_data.data.name])

        await self.move_element(query="query_gw_status",
                                move="move_gw",
                                position=disperser_id,
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

        if id_data.data.filter > 0 and len(id_data.data.name) > 0:
            raise RuntimeError(f"Either filter id or filter name must be selected. "
                               f"Got filter={id_data.data.filter} and name={id_data.data.name}")
        elif id_data.data.filter > 0:
            filter_id = int(FilterWheelPosition(id_data.data.filter))
            filter_name = str(list(self.model.filters.keys())[filter_id])
        else:
            filter_name = id_data.data.name
            filter_id = int(self.model.filters[id_data.data.name])

        await self.move_element(query="query_fw_status",
                                move="move_fw",
                                position=filter_id,
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

        await self.move_element(query="query_ls_status",
                                move="move_ls",
                                position=id_data.data.distanceFromHome,
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

        await self.disconnect()
        await self.stop_mock_ctrl()
        if simulation_mode == 1:
            self.mock_ctrl = MockSpectrographController(port=self.port)
            await asyncio.wait_for(self.mock_ctrl.start(), timeout=2)
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

        moving_state = SALPY_ATSpectrograph.ATSpectrograph_shared_Status_Moving
        not_in_position = SALPY_ATSpectrograph.ATSpectrograph_shared_Status_NotInPosition

        # Send command to the controller. Limit is checked by model.
        if p_state[0] == SALPY_ATSpectrograph.ATSpectrograph_shared_Status_Stationary:
            getattr(self, f"evt_{report_state}").set_put(state=moving_state)
            try:
                await getattr(self.model, move)(position)
            except Exception as e:
                getattr(self, f"evt_{report_state}").set_put(state=not_in_position)
                raise e
            getattr(self, f"evt_{report}").set_put(position=p_state[1])
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

            if (state[0] == SALPY_ATSpectrograph.ATSpectrograph_shared_Status_Stationary and
                    state[1]-position <= self.model.tolerance):
                if position_name is None:
                    getattr(self, f"evt_{report}").set_put(position=state[1])
                else:
                    getattr(self, f"evt_{report}").set_put(position=state[1],
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
        stationary_state = SALPY_ATSpectrograph.ATSpectrograph_shared_Status_Stationary
        homing_state = SALPY_ATSpectrograph.ATSpectrograph_shared_Status_Homing
        not_in_position = SALPY_ATSpectrograph.ATSpectrograph_shared_Status_NotInPosition

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

            if state[0] == SALPY_ATSpectrograph.ATSpectrograph_shared_Status_Stationary:
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
