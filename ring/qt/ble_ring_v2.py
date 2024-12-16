import math
import os
import time
import socket
import struct
import asyncio
import subprocess
from threading import Thread

from ..utils.imu_data import IMUData

class BLERing():
    def __init__(
        self,
        address: str,
        index: int,
        imu_callback=None,
        battery_callback=None,
        touch_callback=None,
        imu_freq=200,
        port: int = 5566,
    ):
        self.address = address
        self.index = index
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
        self.isack = False
        self.touched = False
        self.hold = False
        self.start_touch_time = 0
        self.port = port

        # touch related
        self.taped = False
        self.touch_history = []
        self.holding_num = 0
        self.last_hold_time = 0
        self.touch_type = -1
        self.tap_thread = Thread(target=self.tap_func)
        self.tap_thread.daemon = True
        self.tap_thread.start()
        self.package_length = 125


    def tap_func(self):
        counter = -1
        while True:
            if self.taped:
                if counter != -1:
                    self.touch_callback(self.index, 1) # double-tap
                    counter = -1
                else:
                    counter = 0
                self.taped = False
            elif counter >= 0:
                counter += 1
                if counter == 50:  # tap interval
                    if self.touch_type == 0:
                        # self.touch_callback(self.index, 0) # tap
                        pass
                    elif self.touch_type == 1:
                        # self.touch_callback(self.index, 4) # touch-down
                        pass
                    elif self.touch_type == 2:
                        # self.touch_callback(self.index, 3) # touch-up
                        pass
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
        print(x, y, z)
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
            if self.holding_num == 0 and len(self.touch_history) > 1:
                self.taped = True
                if self.touch_history[-2][0] > self.touch_history[0][0]: # down
                    self.touch_type = 1
                elif self.touch_history[-2][0] < self.touch_history[0][0]: # up
                    self.touch_type = 2
                elif self.touch_history[-2][-1] - self.touch_history[0][-1] < 0.1: # tap
                    self.touch_type = 0
                else:
                    self.touch_type = 0
            elif self.holding_num > 0:
                self.touch_callback(self.index, 5) # release
            self.holding_num = 0
            self.touch_history.clear()
        else:
            if (
                self.touch_history[-1][-1] - self.touch_history[0][-1] > 1
                # and not self.is_holding
            ):
                self.last_hold_time = time.time()
                self.holding_num += 1
                if self.holding_num > 2:
                    self.touch_callback(self.index, 2) # long-touch

    def notify_callback(self, data: bytearray):
        if len(self.touch_history) > 0 and time.time() - self.touch_history[-1][-1] > 0.5:
            # error long-touch
            if self.holding_num > 2:
                self.touch_callback(self.index, 5) # release
            self.holding_num = 0
            self.touch_history.clear()
        if data[2] == 0x40 and data[3] == 0x06:
            if len(data) >= self.package_length:
                imu_datas = []
                for i in range(5, self.package_length - 1, 12):
                    acc_x, acc_y, acc_z = struct.unpack("hhh", data[i : i + 6])
                    acc_x, acc_y, acc_z = (
                        acc_x / 1000 * 9.8,
                        acc_y / 1000 * 9.8,
                        acc_z / 1000 * 9.8,
                    )
                    # if math.sqrt(acc_x * acc_x + acc_y * acc_y + acc_z * acc_z) < 1:
                    #   continue
                    gyr_x, gyr_y, gyr_z = struct.unpack("hhh", data[i + 6 : i + 12])
                    gyr_x, gyr_y, gyr_z = (
                        gyr_x / 180 * math.pi,
                        gyr_y / 180 * math.pi,
                        gyr_z / 180 * math.pi,
                    )
                    # change axis
                    # imu_data = IMUData(acc_x, acc_y, acc_z, gyr_x, gyr_y, gyr_z, int(time.time() * 1e3))
                    imu_data = IMUData(
                        -acc_y, acc_z, -acc_x, -gyr_y, gyr_z, -gyr_x, int(time.time() * 1e3)
                    )
                    imu_datas.append(imu_data)
                imu_datas[4].gyr_z = (imu_datas[3].gyr_z + imu_datas[5].gyr_z) / 2
                for i, imu_data in enumerate(imu_datas):
                    self.imu_callback(self.index, imu_data)
                    # print(round(acc_x, 2), round(acc_y, 2), round(acc_z, 2), round(gyr_x, 2), round(gyr_y, 2), round(gyr_z, 2))
                if len(data) > self.package_length:
                    self.notify_callback(data[self.package_length:])
                
        elif data[2] == 0x61 and data[3] == 0x0:
            # self.touch_callback(self.index, data[4])
            # print("Touch", data[4])
            # print(f"t610 len: {len(data)} {time.time()}")
            # print(' '.join(f'0x{byte:02x}' for byte in data))
            if len(data) > 5:
                self.notify_callback(data[5:])
            # pass

        elif data[2] == 0x61 and data[3] == 0x1 and len(data) >= 23:
            # print(f"touch len: {len(data)} {time.time()}")
            # print(' '.join(f'0x{byte:02x}' for byte in data))
            self._detect_touch_events(data[5:])
            if len(data) > 23:
                self.notify_callback(data[23:])
        else:
            pass
            # print("in else:")
            # print(' '.join(f'0x{byte:02x}' for byte in data))
            
            # status = "touch status: "
            # status += f"  {1 if data[6] & 0x02 else 0}   "
            # status += f"  {1 if data[6] & 0x08 else 0}   "
            # status += f"  {1 if data[6] & 0x20 else 0}   "
            # new_ring_status = [
            #     1 if data[6] & 0x02 else 0,
            #     1 if data[6] & 0x08 else 0,
            #     1 if data[6] & 0x20 else 0,
            # ]
            # print(status)
            # if data[7] & 0x01:
            #     pass
            #     # print("tap")
            #     # self.touch_callback(self.index, 6)
            # elif data[7] & 0x02:
            #     print("swipe_positive")
            # elif data[7] & 0x04:
            #     print("swipe_negative")
            # elif data[7] & 0x08:
            #     print("flick_positive")
            #     self.touch_callback(self.index, 34)
            # elif data[7] & 0x10:
            #     print("flick_negative")
            #     self.touch_callback(self.index, 35)
            # elif data[7] & 0x20:
            #     # if not self.hold:
            #         # print("hold")
            #         # self.touch_callback(self.index, 6)
            #         # self.hold = True
            #     pass
            # if not all(
            #     [self.touch_start_status[i] == new_ring_status[i] for i in range(3)]
            # ):
            #     if not self.touched:
            #         self.start_touch_time = time.time()
            #         self.touched = True
            #     if time.time() - self.start_touch_time > 1:
            #         if not self.hold:
            #             self.touch_callback(self.index, 6)
            #         self.hold = True
            # else:
            #     if self.touched:
            #         pass
            #     self.touched = False
            #     self.hold = False

            # self.touch_callback(self.index, data[4:])
            # print("Touch Raw", data[4:])
    
    def kill(self, name):
        try:
            subprocess.Popen("taskkill /T /F /IM " + name, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except Exception as e:
            # print(e)
            ...

    def launch(self):
        try:
            subprocess.Popen([os.path.dirname(os.path.abspath(__file__)) + "\\ble_test_tools\\BLEData.exe", str(self.port)])
        except Exception as e:
            # print(e)
            ...
    async def connect(self, callback = None):
        self.connected = False
        print('Listening', self.port)
        server = socket.socket()
        server.bind(('0.0.0.0', self.port))
        server.listen(5)
        self.kill('ble_test_tools.exe')
        self.kill('BLEData.exe')
        print("Killed")
        await asyncio.sleep(1)
        self.launch()
        print('Scanning')
        # server.settimeout(12)
        conn, addr = server.accept()
        print('Accepted')
        # callback()
        while True:
            data = conn.recv(1024)
            try:
                if (data.decode('utf-8') == 'Disconnected'):
                    self.connected = False
                    print("Ring Disconnected")
                    callback(self.index)
                    continue
                elif (data.decode('utf-8') == 'Connected'):
                    self.connected = True
                    print("Ring Connected")
                    callback(self.index)
                    continue
                elif (":" in data.decode('utf-8')):
                    print(data.decode('utf-8'))
                    self.address = data.decode('utf-8')
                    continue
            except:
                pass
            self.notify_callback(data)
            await asyncio.sleep(0.0001)
        self.kill('BLEData.exe')
            