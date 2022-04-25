# This file is part of ts_ATDome.
#
# Developed for Vera C. Rubin Observatory Telescope and Site Systems.
# This product includes software developed by the LSST Project
# (https://www.lsst.org).
# See the COPYRIGHT file at the top-level directory of this distribution
# for details of code ownership.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License

__all__ = ["CONFIG_SCHEMA"]

import yaml

CONFIG_SCHEMA = yaml.safe_load(
    """
$schema: http://json-schema.org/draft-07/schema#
$id: https://github.com/lsst-ts/ts_atspec/blob/master/schema/ATSpectrograph.yaml
title: ATSpectrograph v4
description: Schema for ATSpectrograph configuration files
type: object
additionalProperties: false
properties:
  instrument_port:
    description: The port on which the instrument is mounted on the telescope.
    type: number
  host:
    description: IP of the controller
    type: string
  port:
    decription: Port for the controller
    type: integer
  connection_timeout:
    description: >-
        How long to wait for a response from the low level controller when
        establishing the connection (in seconds).
    type: number
  response_timeout:
    description: >-
      How long to wait for a response from low level controller when a command
      or request is sent (in seconds).
    type: number
  move_timeout:
    description: >-
      How long to wait for a movement (from wheels and/or linear stage) to
      complete (in seconds).
    type: number
  min_pos:
    decription: Minimum position for the linear stage (in mm).
    type: number
  max_pos:
    decription: Maximum position for the linear stage (in mm).
    type: number
  tolerance:
    decription: Tolerance for positioning the linear stage.
    type: number
  filters:
    type: object
    additionalProperties: false
    properties:
      filter_name:
        decription: Currently installed filter names.
        type: array
        uniqueItems: true
        items:
          type: string
        minItems: 4
        maxItems: 4
      band:
        decription: >-
          Descriptive bandpass associated with the filter in the beam
          (e.g. u,g,r,i,z,y).
        type: array
        items:
          type: string
        minItems: 4
        maxItems: 4
      central_wavelength_filter:
        description: >-
          Wavelength for which optical system will be opimized in units of nm.
          Approximations are sufficient as the focus dependence on wavelength
          is weak.
        type: array
        items:
          type: number
        minItems: 4
        maxItems: 4
      offset_focus_filter:
        description: >-
          Focus offset to be applied on the secondary mirror in units of um,
          relative to no glass being installed. Positive values push the
          secondary down and increase the back focal distance, therefore adding
          glass thickness will result in positive focus offsets.
        type: array
        uniqueItems: false
        items:
          type: number
        minItems: 4
        maxItems: 4
      offset_pointing_filter:
        description: >-
          Pointing offset to be applied to the telescope in units of
          arcseconds, relative to no glass being installed. Relative to the
          center of the detector, positive Y-values result in the star moving
          up an amplifier, positive X-values result in moving along rows, to
          higher pixel values.
        type: object
        additionalProperties: false
        properties:
          x:
            type: array
            uniqueItems: false
            items:
              type: number
            minItems: 4
            maxItems: 4
            description: X-offset in arcseconds.
          y:
            type: array
            uniqueItems: false
            items:
              type: number
            minItems: 4
            maxItems: 4
            description: Y-offset in arcseconds.
  gratings:
    type: object
    additionalProperties: false
    properties:
      grating_name:
        decription: Currently installed grating names.
        type: array
        uniqueItems: true
        items:
          type: string
        minItems: 4
        maxItems: 4
      band:
        decription: >-
          Descriptive name associated with the grating/disperser in the beam
          (e.g. R100).
        type: array
        items:
          type: string
        minItems: 4
        maxItems: 4
      offset_focus_grating:
        description: >-
          Focus offset to be applied on the secondary mirror in units of um,
          relative to no glass being installed. Positive values push the
          secondary down and increase the back focal distance, therefore adding
          glass thickness will result in positive focus offsets.
        type: array
        uniqueItems: false
        items:
          type: number
        minItems: 4
        maxItems: 4
      offset_pointing_grating:
        description: >-
          Pointing offset to be applied to the telescope in units of
          arcseconds, relative to no glass being installed. Relative to the
          center of the detector, positive Y-values result in the star moving
          up an amplifier, positive X-values result in moving along rows, to
          higher pixel values.
        type: object
        additionalProperties: false
        properties:
          x:
            type: array
            uniqueItems: false
            items:
              type: number
            minItems: 4
            maxItems: 4
            description: X-offset in arcseconds.
          y:
            type: array
            uniqueItems: false
            items:
              type: number
            minItems: 4
            maxItems: 4
            description: Y-offset in arcseconds.
"""
)
