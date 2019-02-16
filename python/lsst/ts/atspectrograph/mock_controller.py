__all__ = ["MockSpectrographController"]

import asyncio
# import enum
# import functools
import logging
# import time


class MockSpectrographController:
    """Mock Spectrograph Controller that talks over TCP/IP.

    Parameters
    ----------
    port : int
        TCP/IP port

    """

    def __init__(self, port):
        self.port = port

        self.log = logging.getLogger("MockATSpectrographController")

        self._server = None

        self.wait_time = 1.0

        self.states = ['I', 'M', 'S', 'X']
        self.error = ['N', 'B', 'I', 'T']

        self._fw_state = 2
        self._fw_pos = 0
        self._fw_err = 0

        self._gw_state = 2
        self._gw_pos = 0
        self._gw_err = 0

        self._ls_state = 2
        self._ls_pos = 0.
        self._ls_err = 0

        self._cmds = {"!XXX": None,
                      "!LDC": None,
                      "?FWS": self.fws,
                      "?GRS": self.grs,
                      "?LSS": self.lss,
                      "?LSL": None,
                      "?GRP": None,
                      "?FWP": None,
                      "!FWI": self.fwi,
                      "!GRI": self.gwi,
                      "!LSI": self.lsi,
                      "!FWM": None,
                      "!GRM": None,
                      "!LSM": None,
                      }

    async def start(self):
        """Start the TCP/IP server, set start_task Done
        and start the command loop.
        """
        self._server = await asyncio.start_server(self.cmd_loop, host="127.0.0.1", port=self.port)

    async def stop(self, timeout=5):
        """Stop the TCP/IP server.
        """
        if self._server is None:
            return

        server = self._server
        self._server = None
        server.close()
        await asyncio.wait_for(server.wait_closed(), timeout=timeout)

    async def cmd_loop(self, reader, writer):
        self.log.info("cmd_loop begins")

        # Write welcome message
        writer.write(f"\r\nSpectrograph\r\n>".encode())
        await writer.drain()

        while True:
            # Write string specifing that server is ready
            line = await reader.readline()
            line = line.decode()
            if not line:
                # connection lost; close the writer and exit the loop
                writer.close()
                return
            line = line.strip()
            self.log.debug(f"read command: {line!r}")
            if line:
                try:
                    if line[:4] in self._cmds:
                        reply = await self._cmds[line[:4]](line[4:])
                        self.log.debug(f"reply: {reply!r}")
                        writer.write(reply)
                        await writer.drain()
                    else:
                        writer.write(" ?Unknown\r\n".encode())
                        await writer.drain()
                except Exception:
                    writer.write(" ?Unknown\r\n".encode())
                    await writer.drain()
                    self.log.exception(f"command {line} failed")
                writer.write(">".encode())
                await writer.drain()

    async def fws(self, val):
        """return filter wheel status"""
        await asyncio.sleep(self.wait_time)
        return f" {self.states[self._fw_state]} {self._fw_pos} {self.error[self._fw_err]}\r\n".encode()

    async def grs(self, val):
        """return grating wheel status"""
        await asyncio.sleep(self.wait_time)
        return f" {self.states[self._gw_state]} {self._gw_pos} {self.error[self._gw_err]}\r\n".encode()

    async def lss(self, val):
        """return linear stage status"""
        await asyncio.sleep(self.wait_time)
        return f" {self.states[self._ls_state]} {self._ls_pos} {self.error[self._ls_err]}\r\n".encode()

    async def fwi(self, val):
        """home filter wheel"""
        self._fw_state = 0
        self._fw_pos = 0
        self._fw_err = 0
        self.log.debug("fw homing started...")
        await asyncio.sleep(self.wait_time)
        self.log.debug("fw homing completed...")
        self._fw_state = 2

        return " ".encode()

    async def gwi(self, val):
        """home filter wheel"""
        self._gw_state = 0
        self._gw_pos = 0
        self._gw_err = 0
        self.log.debug("gw homing started...")
        await asyncio.sleep(self.wait_time)
        self.log.debug("gw homing completed...")
        self._gw_state = 2

        return " ".encode()

    async def lsi(self, val):
        """home filter wheel"""
        self._ls_state = 0
        self._ls_pos = 0
        self._ls_err = 0
        self.log.debug("ls homing started...")
        await asyncio.sleep(self.wait_time)
        self.log.debug("ls homing completed...")
        self._ls_state = 2

        return " ".encode()
