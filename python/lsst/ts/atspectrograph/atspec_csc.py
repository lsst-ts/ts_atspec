import enum

import SALPY_ATSpectrograph

from lsst.ts.salobj import base_csc

from .model import Model

__all__ = ['CSC']


class CSC(base_csc.BaseCsc):
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

    def do_changeDisperser(self, id_data):
        """Change the disperser element.

        Parameters
        ----------
        id_data : `CommandIdData`
            Command ID and data

        """
        self.assert_enabled("changeDisperser")

    def do_changeFilter(self, id_data):
        """Change filter.

        Parameters
        ----------
        id_data : `CommandIdData`
            Command ID and data

        """
        self.assert_enabled("changeFilter")

    def do_homeLinearStage(self, id_data):
        """Home linear stage.

        Parameters
        ----------
        id_data : `CommandIdData`
            Command ID and data

        """
        self.assert_enabled("homeLinearStage")

    def do_moveLinearStage(self, id_data):
        """Move linear stage.

        Parameters
        ----------
        id_data : `CommandIdData`
            Command ID and data

        """
        self.assert_enabled("moveLinearStage")

    def do_stopAllAxes(self, id_data):
        """Stop all axes.

        Parameters
        ----------
        id_data : `CommandIdData`
            Command ID and data

        """
        self.assert_enabled("stopAllAxes")
