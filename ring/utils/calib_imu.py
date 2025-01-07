import time
import asyncio
import argparse
import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from ble_ring_v2 import BLERing
from threading import Thread

def connect(ring):
    asyncio.run(ring.connect())

def modify_mac(ring_mac):
    ring = BLERing(ring_mac, index=0)
    ring_thread = Thread(target=connect, args=(ring,))
    ring_thread.daemon = True
    ring_thread.start()
    while not ring.connected:
        time.sleep(1)
    print('Connected')
    asyncio.run(ring.calibrate_imu())
    time.sleep(3)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--ring', type=str, required=True)
    args = parser.parse_args()
    modify_mac(args.ring)
