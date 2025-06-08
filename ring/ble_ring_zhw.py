import math
import time
import asyncio
import struct
import queue

from .utils.imu_data import IMUData

from bleak import BleakScanner, BleakClient
from types import FunctionType


class BLERing:
    EDPT_QUERY_SS = 0
    EDPT_OP_SYS_CONF = 3
    EDPT_OP_ACTION_CLASS = 4
    EDPT_OP_GSENSOR_STATE = 10
    EDPT_OP_GSENSOR_CTL = 11
    EDPT_OP_HR_BO_STATE = 14
    EDPT_OP_HR_BO_CTL = 15
    EDPT_OP_REPORT_HR_BO_LOG = 0x11
    EDPT_OP_SYS_DEBUG_BIT = 0x12
    EDPT_OP_TEMPERSTURE_QUERY = 0x13
    EDPT_OP_GSENSOR_SWITCH_MODE = 0x20
    EDPT_OP_GSENSOR_DATA = 0x21
    EDPT_OP_RESET_SYS_CONF = 0x22
    EDPT_OP_LED_FLASH = 0x23
    EDPT_OP_TOUCH_ACTION = 0x24
    ELPT_OP_IMU_CTL = 0x1B

    def __init__(
        self,
        address: str,
        index: int,
        imu_callback=None,
        battery_callback=None,
        touch_callback=None,
        imu_freq=200,
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
        self.notify_characteristic = "C1D02505-2D20-400A-95D2-6A2F7BCA0C25"
        self.action_queue = queue.Queue()
        self.isack = False

    @property
    def name(self):
        return "Ring" + str(self.index)

    def on_disconnect(self, clients):
        print("Disconnected")


    def spp_notify_callback(self, sender, data: bytearray):
        if data[0] == 0x19:
            crc = self.crc16(data)
            try:
                assert (crc & 0xFF) == data[1]
                assert ((crc >> 8) & 0xFF) == data[2]
            except:
                print(f"Error: crc is wrong!")
                return
            # imu data packet
            for i in range(8):
                imu_frame = data[3 + i * 28 : 3 + (i + 1) * 28]
                if len(imu_frame) < 28:
                    print("Error: imu frame is too short!")
                    break
                acc_scale = 32768 / 4 / 9.8
                gyr_scale = 32768 / 2000 / (math.pi / 180)
                imu_data = IMUData(
                    struct.unpack("i", imu_frame[0:4])[0] / 1e3,
                    struct.unpack("i", imu_frame[4:8])[0] / 1e3,
                    struct.unpack("i", imu_frame[8:12])[0] / 1e3,
                    struct.unpack("i", imu_frame[12:16])[0] / 1e3,
                    struct.unpack("i", imu_frame[16:20])[0] / 1e3,
                    struct.unpack("i", imu_frame[20:24])[0] / 1e3,
                    struct.unpack("I", imu_frame[24:28])[0] / 1e6 * 16384,
                )

                self.imu_callback(self.index, imu_data)

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

    def open_imu_packet(self):
        data = bytearray(8)
        data[3] = 0x01
        data[4] = 0x01
        return self.check_data(data, self.ELPT_OP_IMU_CTL)

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

    async def send(self, str):
        await self.client.write_gatt_char(
            self.notify_characteristic, bytearray(str + "\r\n", encoding="utf-8")
        )
        await asyncio.sleep(0.5)

    async def get_battery(self):
        await self.client.write_gatt_char(
            self.notify_characteristic, self.query_power_sync_ts()
        )
        await asyncio.sleep(0.5)

    def send_action(self, action: str):
        self.action_queue.put(action)

    async def connect(self, callback: FunctionType = None):
        self.client = BleakClient(self.address)
        await self.client.connect()
        if self.client.is_connected and callback is not None:
            callback(self.index)

        self.client.set_disconnected_callback(self.on_disconnect)

        print("Start notify")
        await self.client.start_notify(
            self.notify_characteristic, self.spp_notify_callback
        )
        # await self.client.start_notify(self.notify_characteristic, self.ble_notify_callback)

        # disable hid
        # await self.client.write_gatt_char(
        #     self.notify_characteristic, self.do_op_touch_action(1, 1, 0)
        # )

        # await self.send("ENSPP")
        # await self.send("ENFAST")
        # await self.send("TPOPS=" + "0,0,0" if self.touch_callback is None else "1,1,1")
        # for imu
        if self.imu_callback != None:
            # await self.send("IMUARG=0,0,0," + str(self.imu_freq))
            # await self.send("ENDB6AX")
            await self.client.write_gatt_char(
                self.notify_characteristic, self.open_imu_packet()
            )

        self.connected = True

        while True:
            if not self.client.is_connected:
                break
            # if self.battery_callback is not None:
            #     await self.get_battery()

            while not self.action_queue.empty():
                data = self.action_queue.get()
                if data == "disconnect":
                    await self.client.disconnect()
                else:
                    await self.send(data)

            await asyncio.sleep(0.2)


def imu_callback(name, data):
    print(f"[{name}]: {data}")


async def scan_rings():
    ring_macs = []
    devices = await BleakScanner.discover()
    for d in devices:
        if d.name is not None and "QHDX Ring" in d.name:
            print("Found ring:", d.name, d.address)
            ring_macs.append(d.address)
    return ring_macs


async def connect():
    ring_macs = await scan_rings()
    print(ring_macs)

    coroutines = []
    for i, ring_mac in enumerate(ring_macs):
        print(f"Found Ring {i}: UUID[{ring_mac}]")

        ring = BLERing(ring_mac, index=i, imu_callback=imu_callback)
        coroutines.append(ring.connect())

    await asyncio.gather(*coroutines)


if __name__ == "__main__":
    asyncio.run(connect())
