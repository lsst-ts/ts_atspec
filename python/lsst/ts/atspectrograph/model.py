
import os
import yaml

__all__ = ['Model']


class Model:
    """

    """
    def __init__(self, log):

        self.simulation_mode = False

        self.log = log

        self.config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config/config.yaml')

        with open(self.config_path, 'r') as stream:
            self.config = yaml.load(stream)

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
