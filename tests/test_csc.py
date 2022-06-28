import asyncio
import pathlib
import logging
import unittest
import typing
import enum

import numpy as np

from lsst.ts import salobj
from lsst.ts.idl.enums.ATSpectrograph import Status

from lsst.ts.atspectrograph.atspec_csc import CSC

BASE_TIMEOUT = 5  # standard command timeout (sec)
LONG_TIMEOUT = 20  # timeout for starting SAL components (sec)

TEST_CONFIG_DIR = pathlib.Path(__file__).parents[1].joinpath("tests", "data", "config")


class TestATSpecCSC(salobj.BaseCscTestCase, unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.log = logging.getLogger("TestATSpecCSC")

    def setUp(self) -> None:
        self.state_published: typing.Set[enum.Enum] = set()
        self.state_published_last = None

    def basic_make_csc(
        self,
        initial_state: typing.Union[salobj.sal_enums.State, int],
        config_dir: typing.Union[str, pathlib.Path, None],
        simulation_mode: int,
    ) -> salobj.base_csc.BaseCsc:
        return CSC(
            initial_state=initial_state,
            config_dir=config_dir,
            simulation_mode=simulation_mode,
        )

    async def test_bin_script(self) -> None:
        await self.check_bin_script(
            name="ATSpectrograph", index=None, exe_name="run_atspectrograph_csc"
        )

    async def test_standard_state_transitions(self) -> None:
        """Test standard CSC state transitions.

        The initial state is STANDBY.
        The standard commands and associated state transitions are:

        * enterControl: OFFLINE to STANDBY
        * start: STANDBY to DISABLED
        * enable: DISABLED to ENABLED

        * disable: ENABLED to DISABLED
        * standby: DISABLED to STANDBY
        * exitControl: STANDBY, FAULT to OFFLINE (quit)
        """
        async with self.make_csc(
            initial_state=salobj.State.STANDBY, config_dir=None, simulation_mode=1
        ):
            events_to_check = {
                self.remote.evt_reportedLinearStagePosition,
                self.remote.evt_lsState,
                self.remote.evt_reportedFilterPosition,
                self.remote.evt_fwState,
                self.remote.evt_reportedDisperserPosition,
                self.remote.evt_gwState,
            }
            for event in events_to_check:
                event.flush()

            await self.check_standard_state_transitions(
                enabled_commands=(
                    "changeDisperser",
                    "changeFilter",
                    "homeLinearStage",
                    "moveLinearStage",
                    "stopAllAxes",
                ),
            )

            for event in events_to_check:
                await self.assert_next_sample(event)

    async def test_changeFilter(self) -> None:

        async with self.make_csc(
            initial_state=salobj.State.ENABLED, config_dir=None, simulation_mode=1
        ):

            set_applied = await self.remote.evt_settingsAppliedValues.aget(
                timeout=BASE_TIMEOUT
            )

            with self.assertRaises(salobj.AckError):
                await self.remote.cmd_changeFilter.set_start(
                    filter=0, name="bad_filter_name", timeout=LONG_TIMEOUT
                )

            for i, filter_name in enumerate(set_applied.filterNames.split(",")):

                filter_id = i

                with self.subTest(filter_name=filter_name):

                    self.remote.evt_reportedFilterPosition.flush()
                    self.remote.evt_filterInPosition.flush()
                    self.remote.evt_fwState.callback = self.monitor_state_callback

                    fpos_initial = await self.remote.evt_reportedFilterPosition.aget(
                        timeout=BASE_TIMEOUT
                    )

                    await self.remote.cmd_changeFilter.set_start(
                        filter=0, name=filter_name, timeout=LONG_TIMEOUT
                    )
                    # Verify the filter wheel goes out of position, then into
                    # position
                    inpos1 = await self.remote.evt_filterInPosition.next(
                        flush=False, timeout=BASE_TIMEOUT
                    )
                    inpos2 = await self.remote.evt_filterInPosition.next(
                        flush=False, timeout=BASE_TIMEOUT
                    )
                    fpos = await self.remote.evt_reportedFilterPosition.next(
                        flush=False, timeout=BASE_TIMEOUT
                    )

                    self.assertFalse(inpos1.inPosition)
                    self.assertTrue(inpos2.inPosition)
                    self.assertEqual(fpos.name, filter_name)
                    self.assertEqual(fpos.slot, filter_id)

                    if fpos_initial.slot == filter_id:
                        self.log.debug(
                            "Filter wheel already in position. No state change expected."
                        )
                        self.assertEqual(self.state_published_last, None)
                    else:
                        self.log.debug(
                            "Filter wheel changed position. State change expected."
                        )
                        self.assertEqual(self.state_published_last, Status.STATIONARY)

                    if len(self.state_published) > 0:
                        self.log.info(
                            "Filter wheel state changed more than once. "
                            "Checking that moving was published."
                        )
                        self.assertTrue(Status.MOVING in self.state_published)

                    # settingsApplied returns lists of floats, so have to set
                    # to the correct type
                    self.assertAlmostEqual(
                        fpos.centralWavelength,
                        float(set_applied.filterCentralWavelengths.split(",")[i]),
                        places=3,
                    )
                    self.assertAlmostEqual(
                        fpos.focusOffset,
                        float(set_applied.filterFocusOffsets.split(",")[i]),
                        places=3,
                    )

                    # pointingOffsets are arrays, but in set_applied it's a
                    # string of arrays so these have to be split and converted
                    for n, offset in enumerate(fpos.pointingOffsets):
                        # line below gives "[X,Y"
                        pair = set_applied.filterPointingOffsets.split("],")[i]
                        # need to strip off the [ and/or ] which is why there
                        # is a [1:] below
                        trimmed_pair = (pair.replace("]", "")).replace("[", "")
                        self.assertAlmostEqual(
                            offset, float(trimmed_pair.split(",")[n]), places=3
                        )

                with self.subTest(filter_id=filter_id):

                    self.remote.evt_reportedFilterPosition.flush()
                    self.remote.evt_filterInPosition.flush()

                    await self.remote.cmd_changeFilter.set_start(
                        filter=filter_id, name="", timeout=LONG_TIMEOUT
                    )
                    inpos1 = await self.remote.evt_filterInPosition.next(
                        flush=False, timeout=BASE_TIMEOUT
                    )
                    inpos2 = await self.remote.evt_filterInPosition.next(
                        flush=False, timeout=BASE_TIMEOUT
                    )
                    fpos = self.remote.evt_reportedFilterPosition.get()
                    self.assertFalse(inpos1.inPosition)
                    self.assertTrue(inpos2.inPosition)
                    self.assertEqual(fpos.name, filter_name)
                    self.assertEqual(fpos.slot, filter_id)
                    # settingsApplied returns lists of floats, so have to set
                    # to the correct type
                    self.assertAlmostEqual(
                        fpos.centralWavelength,
                        float(set_applied.filterCentralWavelengths.split(",")[i]),
                        places=3,
                    )
                    self.assertAlmostEqual(
                        fpos.focusOffset,
                        float(set_applied.filterFocusOffsets.split(",")[i]),
                        places=3,
                    )
                    for n, offset in enumerate(fpos.pointingOffsets):
                        # line below gives "[X,Y"
                        pair = set_applied.filterPointingOffsets.split("],")[i]
                        # need to strip off the [ and/or ] which is why there
                        # is a [1:] below
                        trimmed_pair = (pair.replace("]", "")).replace("[", "")
                        self.assertAlmostEqual(
                            offset, float(trimmed_pair.split(",")[n]), places=3
                        )

            await salobj.set_summary_state(self.remote, salobj.State.STANDBY)

    async def test_changeDisperser(self) -> None:

        async with self.make_csc(
            initial_state=salobj.State.ENABLED, config_dir=None, simulation_mode=1
        ):

            while True:
                try:
                    summary_state = salobj.State(
                        (
                            await self.remote.evt_summaryState.next(
                                flush=False, timeout=LONG_TIMEOUT
                            )
                        ).summaryState
                    )
                except asyncio.TimeoutError:
                    raise RuntimeError("Could not get ENABLED state from CSC.")
                if summary_state == salobj.State.ENABLED:
                    break

            set_applied = await self.remote.evt_settingsAppliedValues.aget(
                timeout=BASE_TIMEOUT
            )

            with self.assertRaises(salobj.AckError):
                await self.remote.cmd_changeDisperser.set_start(
                    disperser=0, name="bad_disperser_name", timeout=LONG_TIMEOUT
                )

            for i, disperser_name in enumerate(set_applied.gratingNames.split(",")):

                disperser_id = i

                with self.subTest(disperser_name=disperser_name):
                    self.remote.evt_reportedDisperserPosition.flush()
                    self.remote.evt_disperserInPosition.flush()
                    self.remote.evt_gwState.callback = self.monitor_state_callback

                    dpos_initial = await self.remote.evt_reportedDisperserPosition.aget(
                        timeout=BASE_TIMEOUT
                    )

                    await self.remote.cmd_changeDisperser.set_start(
                        disperser=0, name=disperser_name, timeout=LONG_TIMEOUT
                    )
                    inpos1 = await self.remote.evt_disperserInPosition.next(
                        flush=False, timeout=BASE_TIMEOUT
                    )
                    inpos2 = await self.remote.evt_disperserInPosition.next(
                        flush=False, timeout=BASE_TIMEOUT
                    )
                    dpos = await self.remote.evt_reportedDisperserPosition.next(
                        flush=False, timeout=BASE_TIMEOUT
                    )
                    self.assertFalse(inpos1.inPosition)
                    self.assertTrue(inpos2.inPosition)
                    self.assertEqual(dpos.name, disperser_name)
                    self.assertEqual(dpos.slot, disperser_id)

                    if dpos_initial.slot == disperser_id:
                        self.log.debug(
                            "Disperser wheel already in position. No state change expected."
                        )
                        self.assertEqual(self.state_published_last, None)
                    else:
                        self.log.debug(
                            "Disperser wheel changed position. State change expected."
                        )
                        self.assertEqual(self.state_published_last, Status.STATIONARY)

                    if len(self.state_published) > 0:
                        self.log.info(
                            "Disperser wheel state changed more than once. "
                            "Checking that moving was published."
                        )
                        self.assertTrue(Status.MOVING in self.state_published)

                    # settingsApplied returns lists of floats, so have to set
                    # to the correct type position comes back with some
                    # numerical precision issue, looks like float is converted
                    # to double somewhere, so use almost equal
                    self.assertAlmostEqual(
                        dpos.focusOffset,
                        float(set_applied.gratingFocusOffsets.split(",")[i]),
                        places=3,
                    )

                    for n, offset in enumerate(dpos.pointingOffsets):
                        # line below gives "[X,Y"
                        pair = set_applied.gratingPointingOffsets.split("],")[i]
                        # need to strip off the [ and/or ] which is why there
                        # is a [1:] below
                        trimmed_pair = (pair.replace("]", "")).replace("[", "")
                        self.assertAlmostEqual(
                            offset, float(trimmed_pair.split(",")[n]), places=3
                        )

                with self.subTest(disperser_id=disperser_id):

                    self.remote.evt_reportedDisperserPosition.flush()
                    self.remote.evt_disperserInPosition.flush()

                    await self.remote.cmd_changeDisperser.set_start(
                        disperser=disperser_id, name="", timeout=LONG_TIMEOUT
                    )
                    inpos1 = await self.remote.evt_disperserInPosition.next(
                        flush=False, timeout=BASE_TIMEOUT
                    )
                    inpos2 = await self.remote.evt_disperserInPosition.next(
                        flush=False, timeout=BASE_TIMEOUT
                    )
                    dpos = await self.remote.evt_reportedDisperserPosition.next(
                        flush=False, timeout=BASE_TIMEOUT
                    )
                    self.assertFalse(inpos1.inPosition)
                    self.assertTrue(inpos2.inPosition)
                    self.assertEqual(dpos.name, disperser_name)
                    self.assertEqual(dpos.slot, disperser_id)

                    # settingsApplied returns lists of floats, so have to set
                    # to the correct type position comes back with some
                    # numerical precision issue, looks like float is converted
                    # to double somewhere, so use almost equal
                    self.assertAlmostEqual(
                        dpos.focusOffset,
                        float(set_applied.gratingFocusOffsets.split(",")[i]),
                        places=3,
                    )

                    for n, offset in enumerate(dpos.pointingOffsets):
                        # line below gives "[X,Y"
                        pair = set_applied.gratingPointingOffsets.split("],")[i]
                        # need to strip off the [ and/or ] which is why there
                        # is a [1:] below
                        trimmed_pair = (pair.replace("]", "")).replace("[", "")
                        self.assertAlmostEqual(
                            offset, float(trimmed_pair.split(",")[n]), places=3
                        )

            await salobj.set_summary_state(self.remote, salobj.State.STANDBY)

    async def test_moveLinearStage(self) -> None:

        async with self.make_csc(
            initial_state=salobj.State.ENABLED, config_dir=None, simulation_mode=1
        ):

            self.monitor_state_callback(
                await self.remote.evt_lsState.aget(timeout=BASE_TIMEOUT)
            )

            self.remote.evt_lsState.callback = self.monitor_state_callback

            for ls_pos in np.linspace(
                self.csc.model.min_pos, self.csc.model.max_pos, 5
            ):
                with self.subTest(ls_pos=ls_pos):

                    self.remote.evt_reportedLinearStagePosition.flush()
                    self.remote.evt_linearStageInPosition.flush()

                    lpos_initial = (
                        await self.remote.evt_reportedLinearStagePosition.aget(
                            timeout=BASE_TIMEOUT
                        )
                    )

                    await self.remote.cmd_moveLinearStage.set_start(
                        distanceFromHome=ls_pos, timeout=LONG_TIMEOUT
                    )
                    inpos1 = await self.remote.evt_linearStageInPosition.next(
                        flush=False, timeout=BASE_TIMEOUT
                    )
                    inpos2 = await self.remote.evt_linearStageInPosition.next(
                        flush=False, timeout=BASE_TIMEOUT
                    )
                    lpos = await self.remote.evt_reportedLinearStagePosition.aget(
                        timeout=BASE_TIMEOUT
                    )
                    self.assertFalse(inpos1.inPosition)
                    self.assertTrue(inpos2.inPosition)
                    self.assertAlmostEqual(lpos.position, ls_pos, places=3)

                    if lpos_initial.position != ls_pos:
                        self.log.debug(
                            "Linear stage already in position. No state change expected."
                        )
                        self.assertEqual(self.state_published_last, Status.STATIONARY)
                    else:
                        self.log.debug(
                            "Linear stage already in position. Should not have any state change."
                        )
                        self.assertEqual(self.state_published_last, None)

                    if len(self.state_published) > 1:
                        self.log.info(
                            "Linear stage state changed more than once. "
                            "Checking that moving was published."
                        )
                        self.assertTrue(Status.MOVING in self.state_published)
                    else:
                        self.assertTrue(Status.STATIONARY in self.state_published)

            await salobj.set_summary_state(self.remote, salobj.State.STANDBY)

    async def test_homeLinearStage(self) -> None:

        async with self.make_csc(
            initial_state=salobj.State.ENABLED, config_dir=None, simulation_mode=1
        ):

            self.remote.evt_linearStageInPosition.flush()

            await self.remote.cmd_homeLinearStage.set_start(timeout=LONG_TIMEOUT)

            inpos1 = await self.remote.evt_linearStageInPosition.next(
                flush=False, timeout=BASE_TIMEOUT
            )
            inpos2 = await self.remote.evt_linearStageInPosition.next(
                flush=False, timeout=BASE_TIMEOUT
            )
            lpos = await self.remote.evt_reportedLinearStagePosition.aget(
                timeout=BASE_TIMEOUT
            )
            self.assertFalse(inpos1.inPosition)
            self.assertTrue(inpos2.inPosition)
            self.assertEqual(lpos.position, 0.0)

            await salobj.set_summary_state(self.remote, salobj.State.STANDBY)

    def test_check_fg_config(self) -> None:

        config_filter = {
            "filter_name": ["a", "b", "c", "d"],
            "band": ["a", "b", "c", "d"],
            "central_wavelength_filter": [700, 701, 702, 703],
            "offset_focus_filter": [0.0, 1.0, 2.0, 3.0],
            "offset_pointing_filter": {
                "x": [0.3, 0.2, 0.1, 0.0],
                "y": [0.3, 0.2, 0.1, 0.0],
            },
        }

        self.assertEqual(CSC.check_fg_config(config_filter), 4)

        config_grating = {
            "grating_name": ["a", "b", "c", "d"],
            "band": ["a", "b", "c", "d"],
            "offset_focus_grating": [0.0, 1.0, 2.0, 3.0],
            "offset_pointing_grating": {
                "x": [0.3, 0.2, 0.1, 0.0],
                "y": [0.3, 0.2, 0.1, 0.0],
            },
        }

        self.assertEqual(CSC.check_fg_config(config_grating), 4)

        bad_config = {
            "config_filter_bad_filter_name_1": {
                "filter_name": ["a", "b", "c"],
                "band": ["a", "b", "c", "d"],
                "central_wavelength_filter": [700, 701, 702, 703],
                "offset_focus_filter": [0.0, 1.0, 2.0, 3.0],
                "offset_pointing_filter": {
                    "x": [0.3, 0.2, 0.1, 0.0],
                    "y": [0.3, 0.2, 0.1, 0.0],
                },
            },
            "config_filter_bad_filter_name_2": {
                "filter_name": ["a", "b", "c", "d", "e"],
                "band": ["a", "b", "c", "d"],
                "central_wavelength_filter": [700, 701, 702, 703],
                "offset_focus_filter": [0.0, 1.0, 2.0, 3.0],
                "offset_pointing_filter": {
                    "x": [0.3, 0.2, 0.1, 0.0],
                    "y": [0.3, 0.2, 0.1, 0.0],
                },
            },
            "config_filter_bad_band_1": {
                "filter_name": ["a", "b", "c", "d"],
                "band": ["a", "b", "c"],
                "central_wavelength_filter": [700, 701, 702, 703],
                "offset_focus_filter": [0.0, 1.0, 2.0, 3.0],
                "offset_pointing_filter": {
                    "x": [0.3, 0.2, 0.1, 0.0],
                    "y": [0.3, 0.2, 0.1, 0.0],
                },
            },
            "config_filter_bad_band_2": {
                "filter_name": ["a", "b", "c", "d", "e"],
                "band": ["a", "b", "c", "d", "e"],
                "central_wavelength_filter": [700, 701, 702, 703],
                "offset_focus_filter": [0.0, 1.0, 2.0, 3.0],
                "offset_pointing_filter": {
                    "x": [0.3, 0.2, 0.1, 0.0],
                    "y": [0.3, 0.2, 0.1, 0.0],
                },
            },
            "config_filter_bad_offset_focus_filter_1": {
                "filter_name": ["a", "b", "c", "d"],
                "band": ["a", "b", "c", "d"],
                "central_wavelength_filter": [700, 701, 702, 703],
                "offset_focus_filter": [0.0, 1.0, 2.0],
                "offset_pointing_filter": {
                    "x": [0.3, 0.2, 0.1, 0.0],
                    "y": [0.3, 0.2, 0.1, 0.0],
                },
            },
            "config_filter_bad_offset_focus_filter_2": {
                "filter_name": ["a", "b", "c", "d"],
                "band": ["a", "b", "c", "d"],
                "central_wavelength_filter": [700, 701, 702, 703],
                "offset_focus_filter": [0.0, 1.0, 2.0, 3.0, 4.0],
                "offset_pointing_filter": {
                    "x": [0.3, 0.2, 0.1, 0.0],
                    "y": [0.3, 0.2, 0.1, 0.0],
                },
            },
            "config_filter_bad_x": {
                "filter_name": ["a", "b", "c", "d"],
                "band": ["a", "b", "c", "d"],
                "central_wavelength_filter": [700, 701, 702, 703],
                "offset_focus_filter": [0.0, 1.0, 2.0, 3.0],
                "offset_pointing_filter": {
                    "x": [0.3, 0.2, 0.1],
                    "y": [0.3, 0.2, 0.1, 0.0],
                },
            },
            "config_filter_bad_y": {
                "filter_name": ["a", "b", "c", "d"],
                "band": ["a", "b", "c", "d"],
                "central_wavelength_filter": [700, 701, 702, 703],
                "offset_focus_filter": [0.0, 1.0, 2.0, 3.0],
                "offset_pointing_filter": {
                    "x": [0.3, 0.2, 0.1, 0.0],
                    "y": [0.3, 0.2, 0.1],
                },
            },
            "config_grating_bad_x": {
                "grating_name": ["a", "b", "c", "d"],
                "band": ["a", "b", "c", "d"],
                "offset_focus_grating": [0.0, 1.0, 2.0, 3.0],
                "offset_pointing_grating": {
                    "x": [0.3, 0.2, 0.1],
                    "y": [0.3, 0.2, 0.1, 0.0],
                },
            },
            "config_grating_bad_y": {
                "grating_name": ["a", "b", "c", "d"],
                "band": ["a", "b", "c", "d"],
                "offset_focus_grating": [0.0, 1.0, 2.0, 3.0],
                "offset_pointing_grating": {
                    "x": [0.3, 0.2, 0.1, 0.0],
                    "y": [0.3, 0.2, 0.1, 0.0, 0.0],
                },
            },
        }

        for config in bad_config:
            with self.subTest(config=config):
                with self.assertRaises(RuntimeError):
                    CSC.check_fg_config(bad_config[config])

    def monitor_state_callback(self, data: salobj.type_hints.BaseMsgType) -> None:
        self.state_published_last = Status(data.state)
        self.state_published.add(Status(data.state))
        self.log.debug(f"monitor_state_callback: {self.state_published_last!r}")


if __name__ == "__main__":
    unittest.main()
