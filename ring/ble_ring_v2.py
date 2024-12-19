import time
import math
import asyncio
import struct
from bleak import BleakScanner, BleakClient
import queue
from types import FunctionType
from threading import Thread
from typing import Tuple

from .utils.imu_data import IMUData


class NotifyProtocol:
    READ_CHARACTERISTIC = "BAE80011-4F05-4503-8E65-3AF1F7329D1F"
    WRITE_CHARACTERISTIC = "BAE80010-4F05-4503-8E65-3AF1F7329D1F"

    GET_SOFTWARE_VERSION = bytearray([0x00, 0x00, 0x11, 0x00])
    GET_HARDWARE_VERSION = bytearray([0x00, 0x00, 0x11, 0x01])
    GET_BATTERY_LEVEL = bytearray([0x00, 0x00, 0x12, 0x00])
    GET_BATTERY_STATUS = bytearray([0x00, 0x00, 0x12, 0x01])
    OPEN_6AXIS_IMU = bytearray([0x00, 0x00, 0x40, 0x06])
    CLOSE_6AXIS_IMU = bytearray([0x00, 0x00, 0x40, 0x00])
    GET_TOUCH = bytearray([0x00, 0x00, 0x61, 0x00])
    OPEN_AUDIO = bytearray([0x00, 0x00, 0x71, 0x00, 0x01])
    CLOSE_AUDIO = bytearray([0x00, 0x00, 0x71, 0x00, 0x00])
    GET_NFC = bytearray([0x00, 0x00, 0x82, 0x00])


class BLERing:

    def __init__(self, address:str, index:int, gyro_bias:Tuple[float]=None, imu_callback=None,
        battery_callback=None, touch_callback=None, imu_freq=200.0):
        self.address = address
        self.index = index
        if gyro_bias is None:
            self.gyro_bias = (0, 0, 0)
        else: self.gyro_bias = gyro_bias
        self.imu_mode = False
        self.raw_imu_data = bytearray()
        self.touch_callback = touch_callback
        self.battery_callback = battery_callback
        self.imu_callback = imu_callback
        self.acc_fsr = "0"
        self.gyro_fsr = "0"
        self.imu_freq = imu_freq
        self.client = None
        self.connected = False
        self.action_queue = queue.Queue()
        self.isack = False

        # touch related
        self.taped = False
        self.touch_history = []
        self.is_holding = False
        self.touch_type = -1
        self.tap_thread = Thread(target=self.tap_func)
        self.tap_thread.daemon = True
        self.tap_thread.start()

        # data
        self.all_imu_data = []


    @property
    def name(self):
        return "Ring" + str(self.index)


    def on_disconnect(self, clients):
        # print('Disconnected')
        pass


    def tap_func(self):
        counter = -1
        while True:
            if self.taped:
                if counter != -1:
                    if self.touch_callback is not None:
                        self.touch_callback(self.index, 1) # double-tap
                    counter = -1
                else:
                    counter = 0
                self.taped = False
            elif counter >= 0:
                counter += 1
                if counter == 100:  # tap interval
                    if self.touch_type == 0: pass
                    elif self.touch_type == 1: pass
                    elif self.touch_type == 2: pass
                    counter = -1
            time.sleep(0.01)


    def get_touch_state(self, x, y, z):
        # 0: 000 -> 0
        # 1: 001 -> 1
        # 2: 010 -> 3
        # 3: 011 -> 2
        # 4: 100 -> 5
        # 5: 101 -> -1
        # 6: 110 -> 4
        # 7: 111 -> -2
        # print(x, y, z)
        return [0, 1, 3, 2, 5, -1, 4, -2][x * 4 + y * 2 + z]
    
    
    def _detect_touch_events(self, data: bytearray) -> None:
        new_touch = [
            self.get_touch_state(
                1 if data[1] & 0x02 else 0,
                1 if data[1] & 0x08 else 0,
                1 if data[1] & 0x20 else 0,
            ),
            time.time(),
        ]
        self.touch_history.append(new_touch)
        if new_touch[0] == 0:
            if not self.is_holding and len(self.touch_history) > 1:
                self.taped = True
                if self.touch_history[-2][0] > self.touch_history[0][0]: # down
                    self.touch_type = 1
                elif self.touch_history[-2][0] < self.touch_history[0][0]: # up
                    self.touch_type = 2
                elif self.touch_history[-2][-1] - self.touch_history[0][-1] < 0.1: # tap
                    self.touch_type = 0
                else:
                    self.touch_type = 0
            elif self.is_holding:
                if self.touch_callback is not None:
                    self.touch_callback(self.index, 5) # release
            self.is_holding = False
            self.touch_history.clear()
        else:
            if self.touch_history[-1][-1] - self.touch_history[0][-1] > 1 and not self.is_holding:
                if self.touch_callback is not None:
                    self.touch_callback(self.index, 2) # long-touch
                self.is_holding = True


    def notify_callback(self, sender, data: bytearray):
        bias = self.gyro_bias
        if data[2] == 0x40 and data[3] == 0x06:
            if len(data) > 20:
                imu_data = []
                for i in range(4 + len(data) % 2, len(data), 12):
                    acc_x, acc_y, acc_z = struct.unpack("hhh", data[i:i+6])
                    acc_x, acc_y, acc_z = acc_x/1e3*9.8, acc_y/1e3*9.8, acc_z/1e3*9.8
                    gyr_x, gyr_y, gyr_z = struct.unpack("hhh", data[i+6:i+12])
                    gyr_x, gyr_y, gyr_z = gyr_x/180*math.pi, gyr_y/180*math.pi, gyr_z/180*math.pi,
                    # change axis
                    timestamp = time.perf_counter()
                    imu = IMUData(-1 * acc_y, acc_z, -1 * acc_x,
                        -1 * gyr_y - bias[0], gyr_z - bias[1], -1 * gyr_x - bias[2], timestamp)
                    imu_data.append(imu)
                imu_data[4].gyr_z = (imu_data[3].gyr_z + imu_data[5].gyr_z) / 2
                self.all_imu_data.extend(imu_data)
                
                # IMU callback function
                if self.imu_callback is not None:
                    for i, imu in enumerate(imu_data):
                        self.imu_callback(self.index, imu)
                        
        elif data[2] == 0x61 and data[3] == 0x0:
            pass

        elif data[2] == 0x61 and data[3] == 0x1:
            self._detect_touch_events(data[5:])

        elif data[2] == 0x12 and data[3] == 0x0:
            battery_level = data[4]
            print(f"Ring {self.index} battery level: {battery_level}")


    async def connect(self, callback: FunctionType = None):
        self.client = BleakClient(
            self.address, disconnected_callback=self.on_disconnect
        )
        try:
            print(f"Try to connect to {self.address}")
            await self.client.connect()
        except Exception as e:
            print(e)
            print(f"Failed to connect to {self.address}")
            return

        print("Start notify")
        await self.client.start_notify(
            NotifyProtocol.READ_CHARACTERISTIC, self.notify_callback
        )
        time.sleep(0.5)
        await self.client.write_gatt_char(
            NotifyProtocol.WRITE_CHARACTERISTIC, NotifyProtocol.OPEN_6AXIS_IMU
        )
        await self.client.write_gatt_char(
            NotifyProtocol.WRITE_CHARACTERISTIC, NotifyProtocol.GET_BATTERY_LEVEL
        )

        if self.client.is_connected:
            print("Connected")
            self.connected = True
            if callback is not None:
            #   callback(self.index)
                callback()

        while True:
            if not self.client.is_connected:
                self.connected = False
                print("connetion closed")
                break
            await asyncio.sleep(2.0)


    async def disconnect(self):
        await self.client.stop_notify(NotifyProtocol.READ_CHARACTERISTIC)
        await self.client.disconnect()
        print(f"Disconnected from {self.address}")
        
    
    def get_imu_data(self): return self.all_imu_data

    def clear_imu_data(self): self.all_imu_data.clear()


def imu_callback(name, data):
    print(f"[{name}]: {data}")


async def scan_rings(loop=False):
    if not loop:
        ring_macs = []
        devices = await BleakScanner.discover()
        for d in devices:
            if d.name is not None and "BCL" in d.name:
                print("Found ring:", d.name, d.address)
                ring_macs.append(d.address)
    else:
        ring_macs = []
        while True:
            devices = await BleakScanner.discover()
            for d in devices:
                if d.name is not None and "BCL" in d.name:
                    print("Found ring:", d.name, d.address)
                    ring_macs.append(d.address)
            if len(ring_macs) > 0:
                break
    return ring_macs


async def connect():
    ring_macs = await scan_rings()
    print(ring_macs)

    coroutines = []
    ring_addr = f'7ED722A8-D972-4C34-0CF9-B9059A4BCAF2'
    ring = BLERing(ring_addr, index=0, imu_callback=imu_callback)
    coroutines.append(ring.connect())

    await asyncio.gather(*coroutines)


if __name__ == "__main__":
    asyncio.run(connect())