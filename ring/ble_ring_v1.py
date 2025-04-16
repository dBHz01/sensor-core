import time
import asyncio
import struct
import queue

from .utils.imu_data import IMUData

from bleak import BleakScanner, BleakClient
from types import FunctionType

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

  def __init__(self, address:str, index:int, imu_callback=None, battery_callback=None, touch_callback=None, imu_freq=200, ring_type='V1'):
    self.address = address
    self.index = index
    self.imu_mode = False
    self.raw_imu_data = bytearray()
    self.touch_callback = touch_callback
    self.battery_callback = battery_callback
    self.imu_callback = imu_callback
    self.acc_fsr = '0'
    self.gyro_fsr = '0'
    self.imu_freq = imu_freq
    self.client = None
    self.connected = False
    self.notify_characteristic = '0000FF11-0000-1000-8000-00805F9B34FB' if ring_type == 'V1' else 'C1D02505-2D20-400A-95D2-6A2F7BCA0C25' # 大创 / 指环王
    self.spp_read_characteristic = 'A6ED0202-D344-460A-8075-B9E8EC90D71B'
    self.spp_write_characteristic = 'A6ED0203-D344-460A-8075-B9E8EC90D71B'
    self.action_queue = queue.Queue()
    self.isack = False
  
  @property
  def name(self):
    return 'Ring' + str(self.index)

  def on_disconnect(self, clients):
    print('Disconnected')

  def ble_notify_callback(self, sender, data):
    crc = self.crc16(data)
    crc_l = crc & 0xFF
    crc_h = (crc >> 8) & 0xFF
    if crc_l != data[1] or crc_h != data[2]:
      print(f"Error: crc is wrong! Data: {data}")
      return

    match data[0]:
      case self.EDPT_QUERY_SS:
        print(f'ring time: {int.from_bytes(data[17:21])}')
        if self.battery_callback is not None:
          self.battery_callback(data[15] & 0xFF)
      case self.EDPT_OP_GSENSOR_STATE:
        union_val = ((data[6] & 0xFF) << 24) | ((data[5] & 0xFF) << 16) | ((data[4] & 0xFF) << 8) | (data[3] & 0xFF)
        chip_state = union_val & 0x1
        work_state = (union_val >> 1) & 0x1
        step_count = (union_val >> 8) & 0x00FFFFFF
        print('gsensor_state', chip_state, work_state, step_count)
      case self.EDPT_OP_GSENSOR_DATA:
        data_type = data[3]
        print(data_type)
      case self.EDPT_OP_TOUCH_ACTION:
        op_type = data[3] & 0x3
        report_path = (data[3] >> 2) & 0x3
        action_code = data[4]
        if op_type < 2:
          if report_path == 0:
            print('HID')
          elif report_path == 1:
            print('BLE')
          else:
            print('HID & BLE')
        elif op_type == 2:
          if self.touch_callback != None:
            self.touch_callback(self.index, action_code)
            # print(f'action code: {action_code}')
        # print('op_type:', op_type, 'report_path:', report_path, 'action_code', action_code)

  def spp_notify_callback(self, sender, data:bytearray):
    if self.imu_mode:
      self.raw_imu_data.extend(data)
      for i in range(len(self.raw_imu_data) - 1):
        if self.raw_imu_data[i] == 0xAA and self.raw_imu_data[i + 1] == 0x55:
          self.raw_imu_data = self.raw_imu_data[i:]
          break
      while len(self.raw_imu_data) > 36:
        imu_frame = self.raw_imu_data[:36]
        imu_data = IMUData(
          struct.unpack("f", imu_frame[4:8])[0],
          struct.unpack("f", imu_frame[8:12])[0],
          struct.unpack("f", imu_frame[12:16])[0],
          struct.unpack("f", imu_frame[16:20])[0],
          struct.unpack("f", imu_frame[20:24])[0],
          struct.unpack("f", imu_frame[24:28])[0],
        #   struct.unpack("Q", imu_frame[28:36])[0]
          time.time() * 16384
        )
        crc = self.crc16(imu_frame, offset=4)
        try:
            assert (crc & 0xFF) == imu_frame[2]
            assert ((crc >> 8) & 0xFF) == imu_frame[3]
        except:
            print(f"Error: crc is wrong!")
            self.raw_imu_data = self.raw_imu_data[36:]
            break
        self.imu_callback(self.index, imu_data)
        self.raw_imu_data = self.raw_imu_data[36:]
    else:
      data = data.decode()
      results = data.strip().split('\r\n')
      for result in results:
        if result.startswith('ACK'):
          if result == 'ACK:ENDB6AX':
            self.imu_mode = True
          print(result)
        elif result.startswith('ACC'):
          args = list(map(lambda x: x.split(':')[1], result.split(',')))
          acc_dict = {'0': '16g', '1': '8g', '2': '4g', '3': '2g'}
          gyro_dict = {'0': '2000dps', '1': '1000dps', '2': '500dps', '3': '250dps'}
          self.acc_fsr = args[0]
          self.gyro_fsr = args[1]
          self.imu_freq = float(args[3])
          print('IMU ACC FSR: ' + acc_dict[args[0]] + '   IMU GYRO FSY: ' + gyro_dict[args[1]] + '   IMU FREQ: ' + args[3] + 'Hz')
        else:
          # pass
          print(result)

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

  async def send(self, str):
    await self.client.write_gatt_char(self.spp_write_characteristic, bytearray(str + '\r\n', encoding='utf-8'))
    await asyncio.sleep(0.5)

  async def get_battery(self):
    await self.client.write_gatt_char(self.notify_characteristic, self.query_power_sync_ts())
    await asyncio.sleep(0.5)

  def send_action(self, action:str):
    self.action_queue.put(action)

  async def connect(self, callback:FunctionType=None):
    self.client = BleakClient(self.address)
    await self.client.connect()
    if self.client.is_connected and callback is not None:
      callback(self.index)

    self.client.set_disconnected_callback(self.on_disconnect)

    print("Start notify")
    await self.client.start_notify(self.spp_read_characteristic, self.spp_notify_callback)
    await self.client.start_notify(self.notify_characteristic, self.ble_notify_callback)

    # get touch patch
    # await self.client.write_gatt_char(self.notify_characteristic, self.do_op_touch_action(0, 0, 0))
    # await asyncio.sleep(0.5)
    # send touch action (VOL UP)
    # await self.client.write_gatt_char(self.notify_characteristic, self.do_op_touch_action(3, 2, 10))
    # await asyncio.sleep(0.5)

    # get battery and sync time
    # await self.get_battery()
    # print('Sync time')

    # disable hid
    await self.client.write_gatt_char(self.notify_characteristic, self.do_op_touch_action(1, 1, 0))

    await self.send('ENSPP')
    await self.send('ENFAST')
    await self.send('TPOPS=' + '0,0,0' if self.touch_callback is None else '1,1,1')
    # for imu
    if self.imu_callback != None:
      await self.send('IMUARG=0,0,0,' + str(self.imu_freq))
      await self.send('ENDB6AX')

    self.connected = True

    while True:
      if not self.client.is_connected:
        break
      if self.battery_callback is not None:
        await self.get_battery()
      
      while not self.action_queue.empty():
        data = self.action_queue.get()
        if data == 'disconnect':
          await self.client.disconnect()
        else:
          await self.send(data)
      
      await asyncio.sleep(0.2)


def imu_callback(name, data):
  print(f'[{name}]: {data}')

async def scan_rings():
  ring_macs = []
  devices = await BleakScanner.discover()
  for d in devices:
    if d.name is not None and 'QHDX Ring' in d.name:
      print('Found ring:', d.name, d.address)
      ring_macs.append(d.address)
  return ring_macs

async def connect():
  ring_macs = await scan_rings()
  print(ring_macs)

  coroutines = []
  for i, ring_mac in enumerate(ring_macs):
    print(f'Found Ring {i}: UUID[{ring_mac}]')

    ring = BLERing(ring_mac, index=i, imu_callback=imu_callback)
    coroutines.append(ring.connect())

  await asyncio.gather(*coroutines)

if __name__ == '__main__':
  asyncio.run(connect())
