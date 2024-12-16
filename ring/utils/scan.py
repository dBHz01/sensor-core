import asyncio
from bleak import BleakClient, BleakScanner

async def scan():
  devices = await BleakScanner.discover()
  for device in devices:
    if device.name is not None and (device.name.startswith('BCL') or 'Ring' in device.name or 'Lenovo' in device.name):
      print(device.details)
      print(device.address)

if __name__ == '__main__':
  asyncio.run(scan())