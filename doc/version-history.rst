.. _Version_History:

===============
Version History
===============

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
