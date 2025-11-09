import time
import asyncio
import argparse
import sys
import os
import numpy as np
import json
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from ble_ring_v2 import BLERing
from threading import Thread
from tqdm import trange

async def calib_time(ring_mac):
    calib_packet_num = 300
    calib_packet_interval = 0.1
    max_calib_delta = 0.010

    ring = BLERing(ring_mac, index=0)
    await ring.connect()
    await asyncio.sleep(1)  # wait for connection to stabilize
    cnt = 0
    pc_timestamps = []
    ring_timestamps = []
    pc_delta_time = []
    for _ in trange(calib_packet_num):
        await ring.calib_time()
        await asyncio.sleep(calib_packet_interval)
        cnt += 1
    await asyncio.sleep(1)
    ring_timestamps = np.array(ring.ring_timestamps)
    pc_timestamps = (np.array(ring.start_calib_timestamps) + np.array(ring.end_calib_timestamps)) / 2
    pc_delta_time = np.array(ring.end_calib_timestamps) - np.array(ring.start_calib_timestamps)

    timestamps = list(zip(pc_timestamps, ring_timestamps, pc_delta_time))
    # sort with pc_delta
    timestamps = sorted(timestamps, key=lambda x: x[2])
    timestamps = [x for x in timestamps if x[2] < max_calib_delta]
    print(f"used calib packet num: {len(timestamps)}, percentage: {len(timestamps) / calib_packet_num}")
    if len(timestamps) < 20:
        print("Warning: not enough calib data")
    pc_timestamps, ring_timestamps, pc_delta_time = zip(*timestamps)

    # print('pc_timestamps:', pc_timestamps)
    # print('ring_timestamps:', ring_timestamps)
    # print('pc_delta_time:', pc_delta_time)

    # linear regression
    ring_timestamps = np.array(ring_timestamps)
    pc_timestamps = np.array(pc_timestamps)
    pc_delta_time = np.array(pc_delta_time)
    X_b = np.c_[np.ones((len(ring_timestamps), 1)), ring_timestamps]
    intercept, slope = np.linalg.inv(X_b.T.dot(X_b)).dot(X_b.T).dot(pc_timestamps)
    print('intercept:', intercept, 'slope:', slope)

    # save
    os.makedirs('data', exist_ok=True)
    with open(f'data/calib_time_{int(time.time())}.json', 'w') as f:
        json.dump({
            'intercept': intercept,
            'slope': slope,
            'pc_timestamps': pc_timestamps.tolist(),
            'ring_timestamps': ring_timestamps.tolist(),
            'pc_delta_time': pc_delta_time.tolist(),
            'ring_mac': ring_mac,
        }, f)

    await ring.disconnect()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--ring', type=str, required=True)
    args = parser.parse_args()
    asyncio.run(calib_time(args.ring))
