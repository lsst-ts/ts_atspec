#!/usr/bin/env python

import asyncio

from lsst.ts import atspectrograph

asyncio.run(atspectrograph.CSC.amain(index=None))
