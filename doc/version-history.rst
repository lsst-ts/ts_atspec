.. _Version_History:

===============
Version History
===============

v0.8.10
------
* Update the version of ts-conda-build to 0.4 in the conda recipe.

v0.8.9
------
* Fix conda recipe by removing setup.cfg reference.

v0.8.8
------
* Fix duplicate configurationApplied event.

v0.8.7
------
* Report band in initial reportedDisperserPosition.

v0.8.6
------
* Add otherInfo to configurationApplied event.
* Remove -u root and change ownership workaround from Jenkinsfile.

v0.8.5
------

* In CSC:
  * Improve ``handle_summary_state`` so it will disconnect from controller if CSC is not in disabled or enabled.
  * Improve ``end_disable`` so that it will log the exception and keep going if it fails to disconnect from the controller, instead of going to FAULT and rejecting the command.
  * In ``health_loop``, fix going to FAULT if one of the components is in FAULT.
* Update conda recipe to properly handle python versions.

v0.8.4
------

* Fix run_atspectrograph_csc function.

v0.8.3
------

* Fix architecture for conda build.

v0.8.2
------

* Fixes to correct packaging errors.

v0.8.1
------

* Switch to ``pyproject.toml``.

v0.8.0
------

* Upgrade CSC to salobj 7.
* Modernize Jenkinsfile for CI job.

v0.7.7
------

* Fix salobj run dependency in conda recipe.

v0.7.6
------

* Fix reporting filter, grating and linear stage state.
* Minor improvements to MockSpectrographController.
* Refactor the way position is reported to use a dictionary instead of an if statement.
* Report linear stage position while moving.

v0.7.5
------

* Fix ATSPectrograph handling of bad filter/grating name rejection.

v0.7.4
------

* Implement new salobj config_schema mechanism and ditch schema path.
* Fix issue when configuring CSC running in simulation mode.
  When running in simulation mode it still reads the configuration file but it must override the values of host and port to those used by the mock controller.
  If the info provided by the config does not match it will send a warning message.

v0.7.3
------

* Reformat code using black 20.
* Pin version of ts-conda-build to 0.3
* Remove usage of asynctest in favor of `unittest.IsolatedAsyncioTestCase`.

v0.7.2
------

* Fix use of deprecated salobj feature "implement_simulation_mode".
* Implement support for version.
* Fix documentation build script.

Requirements
------------

* xml >=7
* salobj >=6.3
* ts_idl >3
* ts_config_latiss >=0.6

v0.7.1
------

* Update setup.cfg to reformat code with black.
* Update configuration schema to add timeout attributes.
* Set timeout configuration to the model class.
* Implement backward compatibility with previous xml file for sending new configuration values.
* If connection fails during enable, disconnect before returning.
* Add `begin_enable` method to send INPROGRESS acknowledgment.

Requirements
------------

* xml >=7
* salobj >6
* ts_idl >2
* ts_config_latiss >=0.6

v0.7.0
------
* Implement compatibility with xml 7.0.0.
* Modernize unit tests to use ``salobj.BaseCscTestCase`` facility.
* Reformat code with black 19.0.
* Update code formatting options.
* User ``ts-conda-build`` as a test dependency for building conda package.
* Fix small issue in Jenkinsfile that would run ``scons`` before building docs.
* Disable concurrent builds in Jenkinsfile.
* Use ``CSC_Conda_Node`` node to build conda package.

v0.6.0
------
* Made compatible with ts_salobj 6 (and 5)

v0.5.1
------
* Add setup.py, conda/meta.yaml and Jenkinsfile.conda to handle packaging.

v0.5.0
------
* Add command-line argument to run the CSC in simulation mode.
* Stop lower-case filtering filter and grating names.
* Make CSC backwards compatible with ts_xml 4.1

v0.4.1
------
* Added Jenkinsfile for conda recipe
* Added conda recipe
* Incorporated new offset parameters with the option of them being persistent (sticky)
