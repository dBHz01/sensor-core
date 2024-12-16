import time
import asyncio
import struct
import socket
import os
import subprocess
import queue

from types import FunctionType

from ..utils.imu_data import IMUData


class BLERing:
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
        self.imu_mode = True
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
        self.port = port

    @property
    def name(self):
        return "Ring" + str(self.index)

    def on_disconnect(self, clients):
        print("Disconnected")

    def ble_notify_callback(self, sender, data):
        crc = self.crc16(data)
        crc_l = crc & 0xFF
        crc_h = (crc >> 8) & 0xFF
        if crc_l != data[1] or crc_h != data[2]:
            print(f"Error: crc is wrong! Data: {data.hex()}")
            return

        match data[0]:
            case self.EDPT_QUERY_SS:
                print(f"ring time: {int.from_bytes(data[17:21])}")
                if self.battery_callback is not None:
                    self.battery_callback(data[15] & 0xFF)
            case self.EDPT_OP_GSENSOR_STATE:
                union_val = (
                    ((data[6] & 0xFF) << 24)
                    | ((data[5] & 0xFF) << 16)
                    | ((data[4] & 0xFF) << 8)
                    | (data[3] & 0xFF)
                )
                chip_state = union_val & 0x1
                work_state = (union_val >> 1) & 0x1
                step_count = (union_val >> 8) & 0x00FFFFFF
                print("gsensor_state", chip_state, work_state, step_count)
            case self.EDPT_OP_GSENSOR_DATA:
                data_type = data[3]
                print(data_type)
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
                            11: 0, # tap
                            12: 1,  # double tap
                            13: 6,  # triple tap
                            14: 2,  # long touch
                            15: 5,  # release
                        }
                        if action_code in action_code_to_touch_code:
                            action_code = action_code_to_touch_code[action_code]
                            self.touch_callback(self.index, action_code)
                        # print(f'action code: {action_code}')
                # print('op_type:', op_type, 'report_path:', report_path, 'action_code', action_code)

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
                print(f"Error: crc is wrong!", len(self.raw_imu_data))
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

    def check_data(self, data, type):
        crc = self.crc16(data)
        data[0] = type
        data[1] = crc & 0xFF
        data[2] = (crc >> 8) & 0xFF
        return data

    def query_system_conf(self):
        data = bytearray(4)
        return self.check_data(data, self.EDPT_OP_SYS_CONF)

    def query_hrbo_state(self):
        data = bytearray(11)
        data[9] = 10
        return self.check_data(data, self.EDPT_OP_HR_BO_STATE)

    def query_action_by_sel_bit(self, sel_bit):
        data = bytearray(6)
        sel_bit <<= 1
        data[3] = sel_bit & 0xFF
        data[4] = (sel_bit >> 8) & 0xFF
        data[5] = (sel_bit >> 16) & 0xFF
        return self.check_data(data, self.EDPT_OP_ACTION_CLASS)

    def set_debug_hrbo(self, enable):
        data = bytearray(4)
        data[3] = 0x3 if enable else 0x1
        return self.check_data(data, self.EDPT_OP_SYS_DEBUG_BIT)

    def query_power_sync_ts(self):
        data = bytearray(21)
        now_sec = int(time.time())
        data[17] = (now_sec >> 0) & 0xFF
        data[18] = (now_sec >> 8) & 0xFF
        data[19] = (now_sec >> 16) & 0xFF
        data[20] = (now_sec >> 24) & 0xFF
        return self.check_data(data, self.EDPT_QUERY_SS)

    def do_op_touch_action(self, get_or_set, path_code, action_code):
        # "AET_PULL_UP", "AET_PULL_DOWN", "AET_DOUBLE_CLICK", "AET_WHEEL_UP", "AET_WHEEL_DOWN", "AET_PLAY_PAUSE",
        # "AET_ONE_KEY_START", "AET_NEXT", "AET_PRIV", "AET_VOL_DOWN", "AET_VOL_UP", "AET_HOME", "AET_BACK",
        # "AET_CUSTOM0", "AET_CUSTOM1", "AET_CUSTOM2", "AET_CUSTOM3", "AET_CUSTOM4", "AET_CUSTOM5"

        data = bytearray(5)
        data[3] = ((path_code & 0x3) << 2) | (get_or_set & 0x3)
        data[4] = action_code
        return self.check_data(data, self.EDPT_OP_TOUCH_ACTION)

    def kill(self, name):
        try:
            subprocess.Popen(
                "taskkill /T /F /IM " + name,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except Exception as e:
            # print(e)
            ...

    def launch(self):
        try:
            subprocess.Popen(
                [os.path.dirname(os.path.abspath(__file__)) + "\\ble_test_tools\\BLEData_dc.exe", str(self.port)]
            )
        except Exception as e:
            # print(e)
            ...

    async def connect(self, callback: FunctionType = None):
        self.connected = False
        print("Listening", self.port)
        server = socket.socket()
        server.bind(("0.0.0.0", self.port))
        server.listen(5)
        self.kill("ble_test_tools.exe")
        self.kill("BLEData_dc.exe")
        print("Killed")
        await asyncio.sleep(1)
        self.launch()
        print("Scanning")
        # server.settimeout(12)
        conn, addr = server.accept()
        print("Accepted")
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
                        if data.decode("utf-8") == "Disconnected":
                            self.connected = False
                            print("Ring Disconnected")
                            callback(self.index)
                        elif data.decode("utf-8") == "Connected":
                            self.connected = True
                            print("Ring Connected")
                            callback(self.index)
                        elif ":" in data.decode("utf-8"):
                            print(data.decode("utf-8"))
                            self.address = data.decode("utf-8")
                    except:
                        pass
                    break
            await asyncio.sleep(0.0001)
        self.kill("BLEData.exe")
