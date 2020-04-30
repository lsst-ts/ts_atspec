import sys
import unittest
import asynctest
import asyncio
import numpy as np
import pathlib
import logging

from lsst.ts import salobj

from lsst.ts.atspectrograph import atspec_csc as csc

np.random.seed(12)

BASE_TIMEOUT = 5  # standard command timeout (sec)
LONG_TIMEOUT = 20  # timeout for starting SAL components (sec)

index_gen = salobj.index_generator()

logger = logging.getLogger()
logger.level = logging.DEBUG

TEST_CONFIG_DIR = pathlib.Path(__file__).parents[1].joinpath("tests", "data", "config")


class Harness:
    def __init__(self, simulation_mode=0):
        salobj.test_utils.set_random_lsst_dds_domain()
        self.csc = csc.CSC(simulation_mode=simulation_mode)
        self.remote = salobj.Remote(self.csc.domain, "ATSpectrograph")

    async def __aenter__(self):
        await self.csc.start_task
        await self.remote.start_task
        return self

    async def __aexit__(self, *args):
        await self.csc.close()


class TestATSpecCSC(asynctest.TestCase):

    async def test_standard_state_transitions(self):
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

        async with Harness(simulation_mode=1) as harness:

            commands = ("start", "enable", "disable", "exitControl", "standby",
                        "changeDisperser", "changeFilter", "homeLinearStage",
                        "moveLinearStage", "stopAllAxes")

            # Check initial state
            current_state = await harness.remote.evt_summaryState.next(flush=False,
                                                                       timeout=BASE_TIMEOUT)

            self.assertEqual(harness.csc.summary_state, salobj.State.STANDBY)
            self.assertEqual(current_state.summaryState, salobj.State.STANDBY)

            # Check that settingVersions was published
            await harness.remote.evt_settingVersions.next(flush=False,
                                                          timeout=BASE_TIMEOUT)

            for bad_command in commands:
                if bad_command in ("start", "exitControl"):
                    continue  # valid command in STANDBY state
                with self.subTest(bad_command=bad_command):
                    cmd_attr = getattr(harness.remote, f"cmd_{bad_command}")
                    with self.assertRaises(salobj.AckError):
                        await cmd_attr.start(cmd_attr.DataType(), timeout=BASE_TIMEOUT)

            # send start; new state is DISABLED
            cmd_attr = getattr(harness.remote, f"cmd_start")
            await asyncio.sleep(BASE_TIMEOUT)  # give time for event loop to run
            harness.remote.evt_summaryState.flush()
            await cmd_attr.start(timeout=120)  # this one can take longer to execute
            state = await harness.remote.evt_summaryState.next(flush=False,
                                                               timeout=BASE_TIMEOUT)
            self.assertEqual(harness.csc.summary_state, salobj.State.DISABLED)
            self.assertEqual(state.summaryState, salobj.State.DISABLED)

            # Check that settings applied was published
            if hasattr(harness.remote, "evt_settingsAppliedValues"):
                set_applied_values = await harness.remote.evt_settingsAppliedValues.next(flush=False,
                                                                                         timeout=BASE_TIMEOUT)
                print('set_applied_values')
                print(set_applied_values)

            elif hasattr(harness.remote, "evt_settingsApplied"):
                set_applied = await harness.remote.evt_settingsApplied.next(flush=False,
                                                                            timeout=BASE_TIMEOUT)
                print('set_applied')
                print(set_applied)

            for bad_command in commands:
                if bad_command in ("enable", "standby"):
                    continue  # valid command in DISABLED state
                with self.subTest(bad_command=bad_command):
                    cmd_attr = getattr(harness.remote, f"cmd_{bad_command}")
                    with self.assertRaises(salobj.AckError):
                        await cmd_attr.start(cmd_attr.DataType(), timeout=BASE_TIMEOUT)

            # send enable; new state is ENABLED
            cmd_attr = getattr(harness.remote, f"cmd_enable")
            await asyncio.sleep(BASE_TIMEOUT)  # give time for the event loop to run
            harness.remote.evt_summaryState.flush()
            try:
                # enable may take some time to complete
                await cmd_attr.start(cmd_attr.DataType(), timeout=120.)
            finally:
                state = await harness.remote.evt_summaryState.next(flush=False,
                                                                   timeout=BASE_TIMEOUT)
            self.assertEqual(harness.csc.summary_state, salobj.State.ENABLED)
            self.assertEqual(state.summaryState, salobj.State.ENABLED)

            # check that position was published
            await harness.remote.evt_reportedLinearStagePosition.aget(timeout=BASE_TIMEOUT)
            await harness.remote.evt_reportedFilterPosition.aget(timeout=BASE_TIMEOUT)
            await harness.remote.evt_reportedDisperserPosition.aget(timeout=BASE_TIMEOUT)

            for bad_command in commands:
                if bad_command in ("disable", "changeDisperser", "changeFilter",
                                   "homeLinearStage", "moveLinearStage", "stopAllAxes"):
                    continue  # valid command in ENABLE state
                logger.debug(f"Testing {bad_command}")
                with self.subTest(bad_command=bad_command):
                    cmd_attr = getattr(harness.remote, f"cmd_{bad_command}")
                    with self.assertRaises(salobj.AckError):
                        await cmd_attr.start(cmd_attr.DataType(), timeout=BASE_TIMEOUT)

            logger.debug("Disabling CSC...")
            # send disable; new state is DISABLED
            cmd_attr = getattr(harness.remote, f"cmd_disable")
            # this CMD may take some time to complete
            await cmd_attr.start(cmd_attr.DataType(), timeout=LONG_TIMEOUT)
            self.assertEqual(harness.csc.summary_state, salobj.State.DISABLED)

    async def test_changeFilter(self):

        async with Harness(simulation_mode=1) as harness:

            await salobj.set_summary_state(harness.remote, salobj.State.ENABLED)

            set_applied = None
            if hasattr(harness.remote, "evt_settingsAppliedValues"):
                set_applied = await harness.remote.evt_settingsAppliedValues.next(
                    flush=False,
                    timeout=BASE_TIMEOUT)
                print(f'set_applied in if of test_changeFilter')
                print(set_applied)
            elif hasattr(harness.remote, "evt_settingsApplied"):
                set_applied = await harness.remote.evt_settingsApplied.next(flush=False,
                                                                            timeout=BASE_TIMEOUT)
                print(f'set_applied in elif of test_changeFilter')
                print(set_applied)
            else:
                print(f'No evt_settingsApplied or evt_settingsAppliedValues published in test_changeFilter')
                await salobj.set_summary_state(harness.remote, salobj.State.STANDBY)
                return

            for i, filter_name in enumerate(set_applied.filterNames.split(',')):

                filter_id = i+1

                with self.subTest(filter_name=filter_name):
                    print(f'testing filter = {filter_name}')
                    harness.remote.evt_reportedFilterPosition.flush()
                    harness.remote.evt_filterInPosition.flush()
                    await harness.remote.cmd_changeFilter.set_start(filter=0,
                                                                    name=filter_name,
                                                                    timeout=LONG_TIMEOUT)
                    inpos1 = await harness.remote.evt_filterInPosition.next(
                        flush=False,
                        timeout=BASE_TIMEOUT)
                    inpos2 = await harness.remote.evt_filterInPosition.next(
                        flush=False,
                        timeout=BASE_TIMEOUT)
                    # TODO: should I be flushing this, I think yes, otherwise the assertion below just uses
                    #  the old value instead of not having a value
                    fpos = harness.remote.evt_reportedFilterPosition.get()
                    self.assertFalse(inpos1.inPosition)
                    self.assertTrue(inpos2.inPosition)
                    self.assertEqual(fpos.name,
                                     filter_name)
                    self.assertEqual(fpos.position,
                                     filter_id)
                    # settingsApplied returns lists of floats, so have to set to the correct type
                    self.assertEqual(fpos.centralWavelength,
                                     float(set_applied.filterCentralWavelengths.split(',')[i]))
                    self.assertEqual(fpos.focusOffset,
                                     float(set_applied.filterFocusOffsets.split(',')[i]))

                with self.subTest(filter_id=filter_id):
                    harness.remote.evt_reportedFilterPosition.flush()
                    harness.remote.evt_filterInPosition.flush()

                    await harness.remote.cmd_changeFilter.set_start(filter=filter_id, name='',
                                                                    timeout=LONG_TIMEOUT)
                    inpos1 = await harness.remote.evt_filterInPosition.next(
                        flush=False,
                        timeout=BASE_TIMEOUT)
                    inpos2 = await harness.remote.evt_filterInPosition.next(
                        flush=False,
                        timeout=BASE_TIMEOUT)
                    fpos = harness.remote.evt_reportedFilterPosition.get()
                    self.assertFalse(inpos1.inPosition)
                    self.assertTrue(inpos2.inPosition)
                    self.assertEqual(fpos.name,
                                     filter_name)
                    self.assertEqual(fpos.position,
                                     filter_id)
                    # settingsApplied returns lists of floats, so have to set to the correct type
                    self.assertEqual(fpos.centralWavelength,
                                     float(set_applied.filterCentralWavelengths.split(',')[i]))
                    self.assertEqual(fpos.focusOffset,
                                     float(set_applied.filterFocusOffsets.split(',')[i]))

            await salobj.set_summary_state(harness.remote, salobj.State.STANDBY)

    async def test_changeDisperser(self):

        async with Harness(simulation_mode=1) as harness:

            await salobj.set_summary_state(harness.remote, salobj.State.ENABLED)

            if hasattr(harness.remote, "evt_settingsAppliedValues"):
                set_applied = await harness.remote.evt_settingsAppliedValues.next(
                    flush=False,
                    timeout=BASE_TIMEOUT)
            elif hasattr(harness.remote, "evt_settingsApplied"):
                set_applied = await harness.remote.evt_settingsApplied.next(flush=False,
                                                                            timeout=BASE_TIMEOUT)
            else:
                return

            for i, disperser_name in enumerate(set_applied.gratingNames.split(',')):

                disperser_id = i+1

                with self.subTest(disperser_name=disperser_name):
                    harness.remote.evt_reportedDisperserPosition.flush()
                    harness.remote.evt_disperserInPosition.flush()
                    await harness.remote.cmd_changeDisperser.set_start(
                        disperser=0,
                        name=disperser_name,
                        timeout=LONG_TIMEOUT)
                    inpos1 = await harness.remote.evt_disperserInPosition.next(
                        flush=False,
                        timeout=BASE_TIMEOUT)
                    inpos2 = await harness.remote.evt_disperserInPosition.next(
                        flush=False,
                        timeout=BASE_TIMEOUT)
                    dpos = harness.remote.evt_reportedDisperserPosition.get()
                    self.assertFalse(inpos1.inPosition)
                    self.assertTrue(inpos2.inPosition)
                    self.assertEqual(dpos.name,
                                     disperser_name)
                    self.assertEqual(dpos.position,
                                     disperser_id)
                    # settingsApplied returns lists of floats, so have to set to the correct type
                    # position comes back with some numerical precision issue, looks like float is
                    # converted to double somewhere, so use almost equal
                    np.testing.assert_allclose(dpos.focusOffset,
                                               float(set_applied.gratingFocusOffsets.split(',')[i]),
                                               rtol=0.0, atol=1e-6)

                with self.subTest(disperser_id=disperser_id):
                    harness.remote.evt_reportedDisperserPosition.flush()
                    harness.remote.evt_disperserInPosition.flush()
                    await harness.remote.cmd_changeDisperser.set_start(
                        disperser=disperser_id,
                        name='',
                        timeout=LONG_TIMEOUT)
                    inpos1 = await harness.remote.evt_disperserInPosition.next(
                        flush=False,
                        timeout=BASE_TIMEOUT)
                    inpos2 = await harness.remote.evt_disperserInPosition.next(
                        flush=False,
                        timeout=BASE_TIMEOUT)
                    dpos = harness.remote.evt_reportedDisperserPosition.get()
                    self.assertFalse(inpos1.inPosition)
                    self.assertTrue(inpos2.inPosition)
                    self.assertEqual(dpos.name,
                                     disperser_name)
                    self.assertEqual(dpos.position,
                                     disperser_id)

                    # settingsApplied returns lists of floats, so have to set to the correct type
                    # position comes back with some numerical precision issue, looks like float
                    # is converted to double somewhere, so use almost equal
                    np.testing.assert_allclose(dpos.focusOffset,
                                               float(set_applied.gratingFocusOffsets.split(',')[i]),
                                               rtol=0.0, atol=1e-6)

            await salobj.set_summary_state(harness.remote, salobj.State.STANDBY)

    async def test_moveLinearStage(self):

        async with Harness(simulation_mode=1) as harness:

            await salobj.set_summary_state(harness.remote, salobj.State.ENABLED)

            for ls_pos in [0., 100, 500, 900, 1000]:
                with self.subTest(ls_pos=ls_pos):
                    harness.remote.evt_reportedLinearStagePosition.flush()
                    harness.remote.evt_linearStageInPosition.flush()
                    await harness.remote.cmd_moveLinearStage.set_start(distanceFromHome=ls_pos,
                                                                       timeout=LONG_TIMEOUT)
                    inpos1 = await harness.remote.evt_linearStageInPosition.next(
                        flush=False,
                        timeout=BASE_TIMEOUT)
                    inpos2 = await harness.remote.evt_linearStageInPosition.next(
                        flush=False,
                        timeout=BASE_TIMEOUT)
                    lpos = harness.remote.evt_reportedLinearStagePosition.get()
                    self.assertFalse(inpos1.inPosition)
                    self.assertTrue(inpos2.inPosition)
                    self.assertEqual(lpos.position,
                                     ls_pos)

            await salobj.set_summary_state(harness.remote, salobj.State.STANDBY)

    async def test_homeLinearStage(self):

        async with Harness(simulation_mode=1) as harness:

            await salobj.set_summary_state(harness.remote, salobj.State.ENABLED)

            harness.remote.evt_linearStageInPosition.flush()

            await harness.remote.cmd_homeLinearStage.set_start(timeout=LONG_TIMEOUT)

            inpos1 = await harness.remote.evt_linearStageInPosition.next(flush=False,
                                                                         timeout=BASE_TIMEOUT)
            inpos2 = await harness.remote.evt_linearStageInPosition.next(flush=False,
                                                                         timeout=BASE_TIMEOUT)
            lpos = await harness.remote.evt_reportedLinearStagePosition.aget(timeout=BASE_TIMEOUT)
            self.assertFalse(inpos1.inPosition)
            self.assertTrue(inpos2.inPosition)
            self.assertEqual(lpos.position,
                             0.)

            await salobj.set_summary_state(harness.remote, salobj.State.STANDBY)


if __name__ == '__main__':

    stream_handler = logging.StreamHandler(sys.stdout)
    logger.addHandler(stream_handler)

    unittest.main()
