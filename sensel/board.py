import sys
import time
import _thread
import numpy as np
from collections import deque
from typing import List

from . import sensel
from .frame_data import FrameData, ContactData


class Board:
    """Manage the Sensel Morph board."""

    MAX_X = 230.0
    MAX_Y = 130.0
    FPS = 50  # unused

    def __init__(self, print_fps=False):
        self.print_fps = print_fps
        self.fps_cnt = 0
        self.fps_start_time = time.perf_counter()
        self._open_sensel()
        self.setScanDetail(2)
        self.setMaxFrameRate(2000)
        print("detail: ", self.getScanDetail())
        print("max frame rate: ", self.getMaxFrameRate())
        self._init_frame()

    def _open_sensel(self):
        handle = None
        (error, device_list) = sensel.getDeviceList()
        if device_list.num_devices != 0:
            (error, handle) = sensel.openDeviceByID(device_list.devices[0].idx)
        self.handle = handle

    def _init_frame(self):
        (error, self.info) = sensel.getSensorInfo(self.handle)
        error = sensel.setFrameContent(self.handle, 0x0F)
        error = sensel.setContactsMask(self.handle, 0x0F)
        error, frame = sensel.allocateFrameData(self.handle)
        error = sensel.startScanning(self.handle)
        self._frame = frame
        self.frames = deque(maxlen=100)
        self.last_timestamp = time.perf_counter()
        self.updated = False
        try:
            _thread.start_new_thread(self._run, ())
        except:
            print("Thread Error")

    def _close_sensel(self):
        self.is_running = False
        error = sensel.freeFrameData(self.handle, self._frame)
        error = sensel.stopScanning(self.handle)
        error = sensel.close(self.handle)

    def _sync(self):
        while (time.perf_counter() - self.last_timestamp) * Board.FPS < 1:
            pass

    def _run(self):
        self.is_running = True
        while self.is_running:
            error = sensel.readSensor(self.handle)
            (error, num_frames) = sensel.getNumAvailableFrames(self.handle)
            for i in range(num_frames):
                # self._sync() # important!!! NEVER USE SYNC
                timestamp_s = time.perf_counter()
                error = sensel.getFrame(self.handle, self._frame)
                timestamp_e = time.perf_counter()
                timestamp = (timestamp_s + timestamp_e) / 2
                self.last_timestamp = timestamp
            R = self.info.num_rows
            C = self.info.num_cols
            force_map = np.zeros((R, C), dtype=np.float32)
            for r in range(R):
                force_map[r, :] = self._frame.force_array[r * C : (r + 1) * C]
            force_map *= 0.05
            frame = FrameData(force_map, timestamp)

            for i in range(self._frame.n_contacts):
                c = self._frame.contacts[i]
                x = c.x_pos / Board.MAX_X
                y = c.y_pos / Board.MAX_Y
                contact = ContactData(
                    c.id,
                    c.state,
                    x,
                    y,
                    c.area,
                    c.total_force,
                    c.major_axis,
                    c.minor_axis,
                    c.delta_x,
                    c.delta_y,
                    c.delta_force,
                    c.delta_area,
                )
                frame.append_contact(contact)

            self.frames.append(frame)
            if len(self.frames) == self.frames.maxlen:
                print("Warning: Frame data not read in time")
            self.updated = True

            if self.print_fps:
                self._printFPS()
            self.fps_cnt += 1

        self._close_sensel()

    def _printFPS(self, time_interval=1):
        if time.perf_counter() - self.fps_start_time > time_interval:
            print(
                f"Sensel FPS: {self.fps_cnt / (time.perf_counter() - self.fps_start_time)}"
            )
            self.fps_start_time = time.perf_counter()
            self.fps_cnt = 0

    def close(self):
        self.is_running = False

    def get_frame(self) -> FrameData:
        while self.updated == False:
            time.sleep(0.001)
        self.updated = False
        return self.frames.popleft()

    def get_frames(self) -> List[FrameData]:
        frames = []
        while self.updated == False:
            time.sleep(0.001)
        self.updated = False
        while len(self.frames) > 0:
            frames.append(self.frames.popleft())
        return frames

    def setScanDetail(self, detail):
        error = sensel.setScanDetail(self.handle, detail)

    def setMaxFrameRate(self, rate):
        error = sensel.setMaxFrameRate(self.handle, rate)

    def getScanDetail(self):
        return sensel.getScanDetail(self.handle)

    def getMaxFrameRate(self):
        return sensel.getMaxFrameRate(self.handle)


if __name__ == "__main__":
    board = Board(print_fps=True)

    start_time = time.time()
    cnt = 0
    while time.time() - start_time < 10:
        time.sleep(0.1)
        # print(time.time() - start_time)
    print("Done")
    board.close()
    print("Stopped")
