import math
import os
import time
import socket
import struct
from ..utils.imu_data import IMUData
import subprocess

class BLERing():
    EDPT_QUERY_SS			          = 0
    EDPT_OP_SYS_CONF            = 3
    EDPT_OP_ACTION_CLASS        = 4
    EDPT_OP_GSENSOR_STATE       = 10
    EDPT_OP_GSENSOR_CTL	        = 11
    EDPT_OP_HR_BO_STATE	        = 14
    EDPT_OP_HR_BO_CTL           = 15
    EDPT_OP_REPORT_HR_BO_LOG    = 0x11
    EDPT_OP_SYS_DEBUG_BIT       = 0x12
    EDPT_OP_TEMPERSTURE_QUERY   = 0x13
    EDPT_OP_GSENSOR_SWITCH_MODE = 0x20
    EDPT_OP_GSENSOR_DATA        = 0x21
    EDPT_OP_RESET_SYS_CONF      = 0x22
    EDPT_OP_LED_FLASH           = 0x23
    EDPT_OP_TOUCH_ACTION        = 0x24
    
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
        self.port = port

    @property
    def name(self):
        return "Ring" + str(self.index)

    def on_disconnect(self, clients):
        print("Disconnected")

    def spp_notify_callback(self, sender, data: bytearray):
        self.raw_imu_data.extend(data)
        while len(self.raw_imu_data) > 36:

            # searching for AA55
            for i in range(len(self.raw_imu_data) - 1):
                if self.raw_imu_data[i] == 0xAA and self.raw_imu_data[i + 1] == 0x55:
                    self.raw_imu_data = self.raw_imu_data[i:]
                    break
            
            if len(self.raw_imu_data) < 36:
                break

            # start of frame
            imu_frame = self.raw_imu_data[:36]

            # check crc
            crc = self.crc16(imu_frame, offset=4)
            try:
                assert (crc & 0xFF) == imu_frame[2]
                assert ((crc >> 8) & 0xFF) == imu_frame[3]
            except:
                # error crc, restart searching
                # print(f"Error: crc is wrong!", len(self.raw_imu_data))
                self.raw_imu_data = self.raw_imu_data[1:]
                continue

            # send imu data
            imu_data = IMUData(
                struct.unpack("f", imu_frame[4:8])[0],
                struct.unpack("f", imu_frame[8:12])[0],
                struct.unpack("f", imu_frame[12:16])[0],
                struct.unpack("f", imu_frame[16:20])[0],
                struct.unpack("f", imu_frame[20:24])[0],
                struct.unpack("f", imu_frame[24:28])[0],
                #   struct.unpack("Q", imu_frame[28:36])[0]
                int(time.time() * 1e3),
            )
            
            self.imu_callback(self.index, imu_data)
            self.raw_imu_data = self.raw_imu_data[36:]

    def crc16(self, data, offset=3):
        genpoly = 0xA001
        result = 0xFFFF
        for i in range(offset, len(data)):
            result = (result & 0xFFFF) ^ (data[i] & 0xFF)
            for _ in range(8):
                if (result & 0x0001) == 1:
                    result = (result >> 1) ^ genpoly
                else:
                    result = result >> 1
        return result & 0xFFFF


    def ble_notify_callback(self, sender, data):
        crc = self.crc16(data)
        crc_l = crc & 0xFF
        crc_h = (crc >> 8) & 0xFF
        if crc_l != data[1] or crc_h != data[2]:
            print(f"Error: crc is wrong! Data: {data.hex()}")
            return

        match data[0]:
            case self.EDPT_OP_TOUCH_ACTION:
                op_type = data[3] & 0x3
                report_path = (data[3] >> 2) & 0x3
                action_code = data[4]
                if op_type < 2:
                    if report_path == 0:
                        print("HID")
                    elif report_path == 1:
                        print("BLE")
                    else:
                        print("HID & BLE")
                elif op_type == 2:
                    if self.touch_callback != None:
                        action_code_to_touch_code = {
                            11: 0,  # tap
                            12: 1,  # double tap
                            13: 6,  # triple tap
                            14: 2,  # long touch
                            15: 5,  # release
                        }
                        if action_code in action_code_to_touch_code:
                            action_code = action_code_to_touch_code[action_code]
                            self.touch_callback(self.index, action_code)


    def notify_callback(self, data: bytearray):
        if len(self.touch_history) > 0 and time.time() - self.touch_history[-1][-1] > 0.5:
            # error long-touch
            if self.holding_num > 2:
                self.touch_callback(self.index, 5) # release
            self.holding_num = 0
            self.touch_history.clear()
        if len(data) < 4:
            return
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
    
    def kill(self, name):
        try:
            subprocess.Popen("taskkill /T /F /IM " + name, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except Exception as e:
            # print(e)
            ...

    def launch(self):
        try:
            subprocess.Popen([os.path.dirname(os.path.abspath(__file__)) + "\\ble_test_tools\\BLEData_dc.exe", str(self.port)])
        except Exception as e:
            # print(e)
            ...
    def connect(self, callback = None):
        self.connected = False
        print('Listening', self.port)
        server = socket.socket()
        server.bind(('0.0.0.0', self.port))
        server.listen(5)
        self.kill('ble_test_tools.exe')
        self.kill('BLEData_dc.exe')
        # print("Killed")
        time.sleep(1)
        self.launch()
        print('Scanning')
        # server.settimeout(12)
        conn, addr = server.accept()
        print('Accepted')
        # callback()
        while True:
            data = conn.recv(1024)
            while len(data) > 0:
                if data[:2] in [b"\x59\x90", b"\xb1\xe0"]:
                    packet_length = data[2]
                    if data[:2] == b"\x59\x90":
                        self.spp_notify_callback(None, data[3:packet_length + 3])
                    elif data[:2] == b"\xb1\xe0":
                        self.ble_notify_callback(None, data[3:packet_length + 3])
                    if len(data) > packet_length + 3:
                        data = data[packet_length + 3:]
                        continue
                    else:
                        break
                else:
                    try:
                        decoded_data = data.decode('utf-8')
                        if decoded_data == "Disconnected":
                            self.connected = False
                            print("Ring Disconnected")
                            callback()
                            break
                        elif decoded_data == "Connected":
                            self.connected = True
                            print("Ring Connected")
                            callback()
                            break
                        elif ":" in decoded_data:
                            print(decoded_data)
                            self.address = decoded_data
                            break
                    except:
                        pass
                    break
