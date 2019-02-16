import asyncio
import traceback
import time

import SALPY_ATSpectrograph

from lsst.ts import salobj

from .model import Model
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

    def __init__(self):
        """
        Initialize AT Spectrograph CSC.
        """

        super().__init__(SALPY_ATSpectrograph)

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

    def end_enable(self, id_data):
        """

        Parameters
        ----------
        id_data : `CommandIdData`
            Command ID and data

        """
        self._health_loop = asyncio.ensure_future(self.health_monitor_loop())

    async def do_enable(self, id_data):
        """Transition from `State.DISABLED` to `State.ENABLED`.

        Parameters
        ----------
        id_data : `CommandIdData`
            Command ID and data
        """
        # start connection with the controller
        await self.model.connect()
        self.want_connection = False

        # initialize the elements
        await self.home_element(query="query_fw_status",
                                home="init_fw",
                                eport="reportedFilterPosition",
                                inposition="filterInPosition",
                                report_state="fwState")  # Home filter wheel

        await self.home_element(query="query_gw_status",
                                home="init_gw",
                                report="reportedDisperserPosition",
                                inposition="disperserInPosition",
                                report_state="gwState")  # Home grating wheel

        await self.home_element(query="query_ls_status",
                                home="init_ls",
                                report="reportedLinearStagePosition",
                                inposition="linearStageInPosition",
                                report_state="lsState")  # Home linear stage

        self._do_change_state(id_data, "enable", [salobj.State.DISABLED], salobj.State.ENABLED)

    async def health_monitor_loop(self):
        """A coroutine to monitor the state of the hardware."""

        while True:
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

        await self.move_element(query="query_gw_status",
                                move="move_gw",
                                position=id_data.distanceFromHome,
                                report="reportedDisperserPosition",
                                inposition="disperserInPosition",
                                report_state="gwState")

    async def do_changeFilter(self, id_data):
        """Change filter.

        Parameters
        ----------
        id_data : `CommandIdData`
            Command ID and data

        """
        self.assert_enabled("changeFilter")

        await self.move_element(query="query_fw_status",
                                move="move_fw",
                                position=id_data.distanceFromHome,
                                report="reportedFilterPosition",
                                inposition="filterInPosition",
                                report_state="fwState")

    async def do_homeLinearStage(self, id_data):
        """Home linear stage.

        Parameters
        ----------
        id_data : `CommandIdData`
            Command ID and data

        """
        self.assert_enabled("homeLinearStage")

        await self.home_gs()

    async def do_moveLinearStage(self, id_data):
        """Move linear stage.

        Parameters
        ----------
        id_data : `CommandIdData`
            Command ID and data

        """
        self.assert_enabled("moveLinearStage")

        # FIXME: Linear stage has a problem and should not be operated at the moment
        if True:
            return

        await self.move_element(query="query_ls_status",
                                move="move_ls",
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

        await self.disconnect()
        await self.stop_mock_ctrl()
        if simulation_mode == 1:
            self.mock_ctrl = MockSpectrographController(port=self.port)
            await asyncio.wait_for(self.mock_ctrl.start(), timeout=2)
        if self.want_connection:
            await self.connect()

    async def move_element(self, query, move, position, report, inposition, report_state):
        """A utility function to wrap the steps for moving the filter wheel, grating
        wheel and linear stage.

        Parameters
        ----------
        query : str
            Name of the method that queries the status of the element. Must be one of the
            three options:
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
            Position to move the wheel or the linear stage. Limits are checked by
            the controller and an exception is raised if out of range.

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
                    state[1] == position):
                getattr(self, f"evt_{report}").set_put(position=state[1])
                getattr(self, f"evt_{inposition}").set_put(inPosition=True)
                break
            elif time.time()-start_time > self.model.move_timeout:
                raise TimeoutError(f"Change position timed out trying to move to "
                                   f"position {position}.")

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
            raise RuntimeError("Element moving. Cannot home.")
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
