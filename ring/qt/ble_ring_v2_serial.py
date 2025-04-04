import math
import os
import sys
import time
import socket
import struct
from threading import Thread
import subprocess

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
        self.last_tap_time = 0
        self.package_length = 133

        # timestamp related
        self.ring_timestamps = []
        self.end_calib_timestamps = []
        self.start_calib_timestamps = []


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
                        self.touch_callback(self.index, 0) # tap
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
    
    def _detect_touch_events(self, data) -> None:
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

    
    def _detect_double_tap_with_tap(self):
        double_tapped = False
        if time.perf_counter() - self.last_tap_time < 0.5:
            double_tapped = True
            if self.touch_callback is not None:
                self.touch_callback(self.index, 1) # double-tap
        self.last_tap_time = time.perf_counter()
        if double_tapped:
            self.last_tap_time = 0
    def notify_callback(self, data):
        if len(data) < 4:
            return
        
        # if len(data) > 133:
        #     print('Data length:', len(data))
        
        if data[2] == 0x62 and data[3] == 0x1:
            print('呼吸灯结果')
        if data[2] == 0x62 and data[3] == 0x2:
            print('自定义灯结果')
        if data[2] == 0x62 and data[3] == 0x3:
            print('自定义灯pwm空闲结果')

        if data[2] == 0x10 and data[3] == 0x0:
            print(data[4])
        if data[2] == 0x11 and data[3] == 0x0:
            print('Software version:', data[4:])
        if data[2] == 0x11 and data[3] == 0x1:
            print('Hardware version:', data[4:])
        
        if data[2] == 0x40 and data[3] == 0x06:
            acc_scale = 32768/16 * (2 ** ((data[4] >> 2) & 3)) / 9.8
            gyr_scale = 32768/2000 * (2 ** (data[4] & 3)) / (math.pi / 180)
            if len(data) >= self.package_length:
                imu_datas = []
                head_length = 4 + len(data) % 2

                imu_start_time = 0
                imu_end_time = 0
                if (len(data) - head_length) % 12 != 0:
                    # end with 2 timestamps
                    imu_start_time = struct.unpack("i", data[-8:-4])[0]
                    imu_end_time = struct.unpack("i", data[-4:])[0]
                    imu_packet_num = (len(data) - head_length - 8) // 12
                else:
                    imu_packet_num = (len(data) - head_length) // 12
                
                for i in range(head_length, self.package_length, 12):
                    if self.package_length - i < 12:
                        break
                    acc_x, acc_y, acc_z = struct.unpack("hhh", data[i:i+6])
                    acc_x, acc_y, acc_z = acc_x / acc_scale, acc_y / acc_scale, acc_z / acc_scale
                    gyr_x, gyr_y, gyr_z = struct.unpack("hhh", data[i+6:i+12])
                    gyr_x, gyr_y, gyr_z = gyr_x / gyr_scale, gyr_y / gyr_scale, gyr_z / gyr_scale,
                    if imu_start_time != 0 and imu_end_time != 0:
                        timestamp = imu_start_time + ((imu_end_time - imu_start_time) / (imu_packet_num - 1)) * ((i - head_length) // 12)
                    else:
                        timestamp = time.perf_counter()
                    imu = IMUData(-1 * acc_y, acc_z, -1 * acc_x, -1 * gyr_y, gyr_z, -1 * gyr_x, timestamp)
                    imu_datas.append(imu)

                if self.imu_callback is not None:
                    for i, imu_data in enumerate(imu_datas):
                        self.imu_callback(self.index, imu_data)
                        # print(round(acc_x, 2), round(acc_y, 2), round(acc_z, 2), round(gyr_x, 2), round(gyr_y, 2), round(gyr_z, 2))
                
                if len(data) > self.package_length:
                    self.notify_callback(data[self.package_length:])
                
        elif data[2] == 0x61 and data[3] == 0x0:
            pass

        elif data[2] == 0x61 and data[3] == 0x1:
            self._detect_touch_events(data[5:])

        elif data[2] == 0x61 and data[3] == 0x2:
            if data[4] in [0, 3, 4]:
                self._detect_double_tap_with_tap()
            elif data[4] == 1:
                if self.touch_callback is not None:
                    self.touch_callback(self.index, 2)
            else:
                if self.touch_callback is not None:
                    self.touch_callback(self.index, data[4])
            if len(data) > 5:
                self.notify_callback(data[5:])

        elif data[2] == 0x12 and data[3] == 0x0:
            battery_level = data[4]
            print(f"Ring {self.index} battery level: {battery_level}")
            if len(data) > 5:
                self.notify_callback(data[5:])
                
        elif data[2] == 0x99 and data[3] == 0x0:
            # timestamp
            self.end_calib_timestamps.append(time.perf_counter())
            self.ring_timestamps.append(struct.unpack("i", data[4:])[0] / 16384)

    
    def kill(self, name):
        try:
            subprocess.Popen("taskkill /T /F /IM " + name, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except Exception as e:
            # print(e)
            ...

    def launch(self):
        try:
            try:
                bundle_dir = sys._MEIPASS
                bundle_dir = os.path.join(bundle_dir, "core", "ring", "qt")
            except Exception as e:
                bundle_dir = os.path.abspath(os.path.dirname(__file__))
            subprocess.Popen([os.path.join(bundle_dir, "ble_test_tools", "BLEData.exe"), str(self.port)])
        except Exception as e:
            print(e)
            ...
    def connect(self, callback = None):
        self.connected = False
        print('Listening', self.port)
        server = socket.socket()
        server.bind(('0.0.0.0', self.port))
        server.listen(5)
        self.kill('ble_test_tools.exe')
        self.kill('BLEData.exe')
        print("Killed")
        time.sleep(1)
        self.launch()
        print('Scanning')
        # server.settimeout(12)
        conn, addr = server.accept()
        print('Accepted')
        # callback()
        while True:
            data = conn.recv(1024)
            # print(data)
            try:
                decoded_data = data.decode('utf-8')
                if (decoded_data == 'Disconnected'):
                    self.connected = False
                    print("Ring Disconnected")
                    callback()
                    continue
                elif (decoded_data == 'Connected'):
                    self.connected = True
                    print("Ring Connected")
                    callback()
                    continue
                elif (":" in decoded_data):
                    print(decoded_data)
                    self.address = decoded_data
                    continue
            except Exception as e:
                # print(e)
                pass
            self.notify_callback(data)
            # time.sleep(0.0001)
        self.kill('BLEData.exe')
            