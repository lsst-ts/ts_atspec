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
                 simulation_mode=0):
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
                         simulation_mode=simulation_mode)

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

    async def end_start(self, data):
        """end do_start; called after state changes.

        This method call setup on the model, passing the selected setting.

        Parameters
        ----------
        data : ATSpectrograph_command_start
            Command data
        """
        self.want_connection = True

        await super().end_start(data)

    async def end_enable(self, data):
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
                self.fault(code=CONNECTION_ERROR,
                           report="Cannot connect to controller.",
                           traceback=traceback.format_exc())
                raise e

            self.want_connection = False

        try:
            # Check/Report linear stage position. Home if position is out of
            # range.
            state = await self.model.query_gs_status(self.want_connection)
            self.log.debug(f"query_gs_status: {state}")
            if state[1] < 0.:
                self.log.warning("Linear stage out of range. Homing.")
                await self.home_element(query="query_gs_status",
                                        home="init_gs",
                                        report="reportedLinearStagePosition",
                                        inposition="linearStageInPosition",
                                        report_state="lsState")
            else:
                self.evt_reportedLinearStagePosition.set_put(position=state[1])
        except Exception as e:
            self.fault(code=CONNECTION_ERROR,
                       report=f"Cannot get information from model for "
                              f"linear stage.",
                       traceback=traceback.format_exc())
            raise e

        try:

            # Check/Report Filter Wheel position.
            state = await self.model.query_fw_status(self.want_connection)
            self.log.debug(f"query_fw_status: {state}")
            # TODO: this area needs documentation on why this state works as such.
            filter_name = str(list(self.model.filter_to_enum_mapping_dict.keys())[int(state[1])])
            #print(f'filter_name is {filter_name}')


            self.evt_reportedFilterPosition.set_put(position=int(state[1])+1,
                                                    name=filter_name,
                                                    centralWavelength=self.filter_info['filter_central_wavelength'][int(state[1])],
                                                    focusOffset=self.filter_info["filter_focus_offset"][int(state[1])])

        except Exception as e:
            self.fault(code=CONNECTION_ERROR,
                       report=f"Cannot get information from model for "
                              f"filter wheel.",
                       traceback=traceback.format_exc())
            raise e

        try:
            # Check/Report Grating/Disperser Wheel position.
            state = await self.model.query_gw_status(self.want_connection)
            self.log.debug(f"query_gw_status: {state}")
            grating_name = str(list(self.model.gratings_to_enum_mapping_dict.keys())[int(state[1])])
            self.evt_reportedDisperserPosition.set_put(position=int(state[1])+1,
                                                       name=grating_name)
        except Exception as e:
            self.fault(code=CONNECTION_ERROR,
                       report=f"Cannot get information from model for "
                              f"grating/Disperser wheel.",
                       traceback=traceback.format_exc())
            raise e

        self._health_loop = asyncio.ensure_future(self.health_monitor_loop())

        await super().end_enable(data)

    async def end_disable(self, data):

        # TODO: Russell Owen suggest using putting this in
        # `handle_summary_state` instead and calling it whenever the state
        # leaves "enabled".
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
            self.fault(code=CONNECTION_ERROR,
                       report="Cannot disconnect from controller.",
                       traceback=traceback.format_exc())
            raise e

        await super().end_disable(data)

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
                    self.log.error(f"Linear stage in error: {ls_state}")
                    self.fault(code=LS_ERROR,
                               report=f"Linear stage in error: {ls_state}")
                    break
                elif fw_state[2] != ATSpectrograph.Error.NONE:
                    self.log.error(f"Filter wheel in error: {fw_state}")
                    self.fault(code=FW_ERROR,
                               report=f"Filter wheel  in error: {fw_state}")
                    break
                elif gw_state[2] != ATSpectrograph.Error.NONE:
                    self.log.error(f"Grating wheel in error: {gw_state}")
                    self.fault(code=GW_ERROR,
                               report=f"Grating wheel in error: {gw_state}")
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
            except Exception:
                self.fault(code=HEALTH_LOOP_DIED,
                           report="Health loop died for some unspecified reason.",
                           traceback=traceback.format_exc())

    async def do_changeDisperser(self, data):
        """Change the disperser element.

        Parameters
        ----------
        data : ATSpectrograph_command_changeDisperser
            Command ID and data

        """
        self.assert_enabled("changeDisperser")
        self.assert_move_allowed("changeDisperser")

        if data.disperser > 0 and len(data.name) > 0:
            raise RuntimeError(f"Either disperser id or filter name must be selected. "
                               f"Got disperser={data.disperser} and name={data.name}")
        elif data.disperser == 0 and len(data.name) == 0:
            raise RuntimeError(f"Neither filter id or name where specified.")
        elif data.disperser < 0 or data.disperser > len(self.model.gratings_to_enum_mapping_dict):
            raise RuntimeError(f"Invalid filter id. Got {data.disperser}, must "
                               f"be between 0 and {len(self.model.gratings_to_enum_mapping_dict)}")
        elif data.disperser > 0:
            disperser_id = int(ATSpectrograph.DisperserPosition(data.disperser))
            disperser_name = str(list(self.model.gratings_to_enum_mapping_dict.keys())[disperser_id-1])
        else:
            disperser_name = data.name
            disperser_id = int(self.model.gratings_to_enum_mapping_dict[data.name])

        await self.move_element(query="query_gw_status",
                                move="move_gw",
                                position=disperser_id-1,
                                report="reportedDisperserPosition",
                                inposition="disperserInPosition",
                                report_state="gwState",
                                position_name=disperser_name)

    async def do_changeFilter(self, data):
        """Change filter.

        Parameters
        ----------
        data : ATSpectrograph_command_changeFilter
            Command data

        """
        self.assert_enabled("changeFilter")
        self.assert_move_allowed("changeFilter")

        if data.filter > 0 and len(data.name) > 0:
            raise RuntimeError(f"Either filter id or filter name must be selected. "
                               f"Got filter={data.filter} and name={data.name}")
        elif data.filter == 0 and len(data.name) == 0:
            raise RuntimeError(f"Neither filter id or name where specified.")
        elif data.filter < 0 or data.filter > len(self.model.filter_to_enum_mapping_dict):
            raise RuntimeError(f"Invalid filter id. Got {data.filter}, must "
                               f"be between 0 and {len(self.model.filter_to_enum_mapping_dict)}")
        elif data.filter > 0:
            filter_id = int(ATSpectrograph.FilterPosition(data.filter))
            filter_name = str(list(self.model.filter_to_enum_mapping_dict.keys())[filter_id-1])
        else:
            filter_name = data.name
            filter_id = int(self.model.filter_to_enum_mapping_dict[data.name])

        await self.move_element(query="query_fw_status",
                                move="move_fw",
                                position=filter_id-1,
                                report="reportedFilterPosition",
                                inposition="filterInPosition",
                                report_state="fwState",
                                position_name=filter_name)



    async def do_homeLinearStage(self, data):

        """Home linear stage.

        Parameters
        ----------
        data : ATSpectrograph_command_homeLinearStage
            Command data

        """
        self.assert_enabled("homeLinearStage")
        self.assert_move_allowed("homeLinearStage")

        await self.home_element(query="query_gs_status",
                                home="init_gs",
                                report="reportedLinearStagePosition",
                                inposition="linearStageInPosition",
                                report_state="lsState")

    async def do_moveLinearStage(self, data):
        """Move linear stage.

        Parameters
        ----------
        data : ATSpectrograph_command_moveLinearStage
            Command data

        """
        self.assert_enabled("moveLinearStage")
        self.assert_move_allowed("moveLinearStage")

        await self.move_element(query="query_gs_status",
                                move="move_gs",
                                position=data.distanceFromHome,
                                report="reportedLinearStagePosition",
                                inposition="linearStageInPosition",
                                report_state="lsState")

    async def do_stopAllAxes(self, data):
        """Stop all axes.

        Parameters
        ----------
        data : ATSpectrograph_command_stopAllAxes
            Command data

        """
        self.assert_enabled("stopAllAxes")

        await self.model.stop_all_motion(self.want_connection)

        # TODO: Report new state and position

    async def implement_simulation_mode(self, simulation_mode):
        """Implement going into or out of simulation mode.

        Parameters
        ----------
        simulation_mode : int
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
                # this will be for the linear stage only since it's the only topic with a position attribute
                getattr(self, f"evt_{report}").set_put(position=p_state[1])
            else:
                #TODO: doesn't this apply to both filter and disperser? why only reporting disperser?
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
                if report == "reportedLinearStagePosition":
                    # this is for the linear stage only since it's the only topic with a position attribute
                    getattr(self, f"evt_{report}").set_put(position=state[1])
                elif report == "reportedFilterPosition":
                    getattr(self, f"evt_{report}").set_put(position=state[1]+1,
                                                           name=position_name,
                                                           centralWavelength=
                                                           self.filter_info['filter_central_wavelength'][state[1]],
                                                           focusOffset=self.filter_info["filter_focus_offset"][
                                                               int(state[1])] )
                elif report == "reportedDisperserPosition":
                    getattr(self, f"evt_{report}").set_put(position=state[1]+1,
                                                           name=position_name,
                                                           focusOffset=self.grating_info["grating_focus_offset"][
                                                               int(state[1])])
                else:
                    raise RuntimeError(f"Expected report = reportedLinearStagePosition, reportedFilterPosition or "
                                       f"reportedDisperserPosition, but got {report}")
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

        current_state = await getattr(self.model, query)(self.want_connection)
        stationary_state = ATSpectrograph.Status.STATIONARY
        homing_state = ATSpectrograph.Status.HOMING
        not_in_position = ATSpectrograph.Status.NOTINPOSITION

        if current_state[0] != stationary_state:
            raise RuntimeError(f"Element {inposition.split('In')[0]} in {current_state}. "
                               f"Must be in {stationary_state}. Cannot home.")
        else:
            getattr(self, f"evt_{report_state}").set_put(state=homing_state)

        try:
            await getattr(self.model, home)()
            p_state = await getattr(self.model, query)(self.want_connection)
        except Exception as e:
            getattr(self, f"evt_{report_state}").set_put(state=not_in_position, force_output=True)
            raise e

        getattr(self, f"evt_{inposition}").set_put(inPosition=False, force_output=True)

        # Need to wait for command to complete
        start_time = time.time()
        while True:
            state = await getattr(self.model, query)(self.want_connection)

            if p_state[0] != state[0]:
                getattr(self, f"evt_{report_state}").set_put(state=state[0])
                p_state = state

            if state[0] == ATSpectrograph.Status.STATIONARY:
                getattr(self, f"evt_{report}").set_put(position=state[1], force_output=True)
                getattr(self, f"evt_{inposition}").set_put(inPosition=True, force_output=True)
                break
            elif time.time()-start_time > self.model.move_timeout:
                raise TimeoutError(f"Homing element failed...")

            await asyncio.sleep(0.1)

    def assert_move_allowed(self, action):
        """Assert that moving the spectrograph elements is allowed."""
        if self.is_exposing:
            raise salobj.base.ExpectedError(f"Camera is exposing, {action} is not allowed.")

    def monitor_start_integration_callback(self, data):
        """Set `is_exposing` flag to True."""
        self.is_exposing = True

    def monitor_start_readout_callback(self, data):
        """Set `is_exposing` flag to False."""
        self.is_exposing = False

    @staticmethod
    def get_config_pkg():
        return 'ts_config_latiss'

    async def configure(self, config):
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

        self.model.host = config.host
        self.model.port = config.port

        self.model.min_pos = config.min_pos
        self.model.max_pos = config.max_pos

        if self.model.min_pos >= self.model.max_pos:
            raise RuntimeError(f"Minimum linear stage position ({self.model.min_pos}) "
                               f"must be smaller than maximum ({self.model.max_pos}).")

        self.model.tolerance = config.tolerance

        # Verify configurations for filter_to_enum_mapping_dict are populated correctly.
        # create dictionary mapping the name to the filter position
        if (len(config.filters['filter_name']) == len(ATSpectrograph.FilterPosition) - 1)and \
                (len(config.filters['filter_central_wavelength']) == len(ATSpectrograph.FilterPosition) - 1) and \
                (len(config.filters['filter_focus_offset']) == len(ATSpectrograph.FilterPosition) - 1):
            self.model.filter_to_enum_mapping_dict = dict()
            self.filter_info = config.filters
            self.grating_info = config.gratings

            # create relationship between filter name and enumeration since either can be used as inputs
            for i, f in enumerate(ATSpectrograph.FilterPosition):
                self.model.filter_to_enum_mapping_dict[config.filters['filter_name'][i]] = f

                # Why do this? Appears enums can go larger than 3?
                if i == len(ATSpectrograph.FilterPosition)-2:
                    break
        else:
            # This shouldn't be able to be called as the configuration file should have first been validated
            # to have the appropriate number of values
            raise RuntimeError(f"Invalid filter configuration. Need same number of values for all attributes. Expected "
                               f"{len(ATSpectrograph.FilterPosition)} entries, got "
                               f"{len(config.filters['filter_name'])} for name,"
                               f"{len(config.filters['filter_central_wavelength'])} for filter_central_wavelength,"
                               f"{len(config.filters['filter_focus_offset'])} for filter_focus_offset")


        # Verify configurations for gratings are populated correctly.
        # create dictionary mapping the name to the grating position
        if len(config.gratings['grating_name']) == len(ATSpectrograph.DisperserPosition)-1:
            self.model.gratings_to_enum_mapping_dict = dict()
            for i, g in enumerate(ATSpectrograph.DisperserPosition):
                self.model.gratings_to_enum_mapping_dict[config.gratings['grating_name'][i]] = g
                if i == len(ATSpectrograph.DisperserPosition)-2:
                    break
        else:
            # This shouldn't be able to be called as the configuration file should have first been validated
            # to have the appropriate number of values
            raise RuntimeError("Invalid grating name configuration. Expected "
                               f"{len(ATSpectrograph.DisperserPosition)} entries, got "
                               f"{len(config.gratings['grating_name'])} for name,"
                               f"{len(config.gratings['grating_focus_offset'])} for focus_offset")


        # settingsApplied needs to publish the comma separated string
        filters_str = {'filter_name': '', 'filter_central_wavelength': '', 'filter_focus_offset': ''}
        for i, f in enumerate(self.model.filter_to_enum_mapping_dict):
            filters_str['filter_name'] += str(f)
            filters_str['filter_central_wavelength'] += str(config.filters['filter_central_wavelength'][i])
            filters_str['filter_focus_offset'] += str(config.filters['filter_focus_offset'][i])
            # need to add comma, except for the last value
            if i < len(self.model.filter_to_enum_mapping_dict) - 1:
                # loop over keys to add a comma for each
                for key in filters_str:
                    filters_str[key] += ','

        gratings_str = {'grating_name': '', 'grating_focus_offset': ''}
        for i, f in enumerate(self.model.gratings_to_enum_mapping_dict):
            gratings_str['grating_name'] += str(f)
            gratings_str['grating_focus_offset'] += str(config.gratings['grating_focus_offset'][i])
            # need to add comma, except for the last value
            if i < len(self.model.gratings_to_enum_mapping_dict) - 1:
                # loop over keys to add a comma for each, do not add a space after the comma!
                for key in gratings_str:
                    gratings_str[key] += ','

        if hasattr(self, "evt_settingsAppliedValues"):
            self.evt_settingsAppliedValues.set_put(host=self.model.host,
                                                   port=self.model.port,
                                                   linearStageMinPos=self.model.min_pos,
                                                   linearStageMaxPos=self.model.max_pos,
                                                   linearStageSpeed=0.,
                                                   filterNames=filters_str['filter_name'],
                                                   filterCentralWavelengths=filters_str['filter_central_wavelength'],
                                                   filterFocusOffsets=filters_str['filter_focus_offset'],
                                                   gratingNames=gratings_str['grating_name'],
                                                   gratingFocusOffsets=gratings_str['grating_focus_offset'],
                                                   instrumentPort=config.instrument_port)

        elif hasattr(self, "evt_settingsApplied"):
            self.evt_settingsApplied.set_put(host=self.model.host,
                                             port=self.model.port,
                                             linearStageMinPos=self.model.min_pos,
                                             linearStageMaxPos=self.model.max_pos,
                                             linearStageSpeed=0.,
                                             filterNames=filters_str['filter_name'],
                                             filterCentralWavelengths=filters_str['filter_central_wavelength'],
                                             filterFocusOffsets=filters_str['filter_focus_offset'],
                                             gratingNames=gratings_str['grating_name'],
                                             gratingFocusOffsets=gratings_str['grating_focus_offset'],
                                             instrumentPort=config.instrument_port)

        else:
            self.log.warning("No settingsApplied or settingsAppliedValues event.")
            self.log.info(f"host:{self.model.host},port:{self.model.port},"
                          f"filterNames:{filters_str},gratingNames:{gratings_str},"
                          f"instrumentPort:{config.instrument_port}")

    async def close(self):
        if self.mock_ctrl is not None:
            await self.mock_ctrl.stop(timeout=self.timeout)

        await super().close()

    @classmethod
    def add_arguments(cls, parser):
        super(CSC, cls).add_arguments(parser)
        parser.add_argument("-s", "--simulate", action="store_true",
                            help="Run in simuation mode?")

    @classmethod
    def add_kwargs_from_args(cls, args, kwargs):
        super(CSC, cls).add_kwargs_from_args(args, kwargs)
        kwargs["simulation_mode"] = 1 if args.simulate else 0
