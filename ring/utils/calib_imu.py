import time
import asyncio
import argparse
import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from ble_ring_v2 import BLERing
from threading import Thread

async def calib_imu(ring_mac):
    ring = BLERing(ring_mac, index=0)
    await ring.connect()
    await asyncio.sleep(1)  # wait for connection to stabilize
    await ring.calibrate_imu()
    await asyncio.sleep(3)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--ring', type=str, required=True)
    args = parser.parse_args()
    asyncio.run(calib_imu(args.ring))
