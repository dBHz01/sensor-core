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

def connect(ring):
    asyncio.run(ring.connect())

def calib_time(ring_mac):
    calib_packet_num = 600
    calib_packet_interval = 0.05
    max_calib_delta = 0.014
    test_packet_num = 10

    ring = BLERing(ring_mac, index=0)
    ring_thread = Thread(target=connect, args=(ring,))
    ring_thread.daemon = True
    ring_thread.start()
    while not ring.connected:
        time.sleep(1)
    print('Connected')
    cnt = 0
    pc_timestamps = []
    ring_timestamps = []
    pc_delta_time = []
    for _ in trange(calib_packet_num):
        asyncio.run(ring.calib_time())
        time.sleep(calib_packet_interval)
        cnt += 1
    time.sleep(2)
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
    with open(f'data/calib_time_{int(time.time())}.json', 'w') as f:
        json.dump({
            'intercept': intercept,
            'slope': slope,
            'pc_timestamps': pc_timestamps.tolist(),
            'ring_timestamps': ring_timestamps.tolist(),
            'pc_delta_time': pc_delta_time.tolist(),
            'ring_mac': ring_mac,
        }, f)

    # # plot
    # import matplotlib.pyplot as plt
    # plt.plot(pc_timestamps, ring_timestamps, 'ro')
    # plt.plot(pc_timestamps, [slope * x + intercept for x in pc_timestamps], 'b-')
    # plt.xlabel('PC timestamp')
    # plt.ylabel('Ring timestamp')
    # plt.show()

    # test model
    # ring.ring_timestamps = []
    # ring.start_calib_timestamps = []
    # ring.end_calib_timestamps = []
    # cnt = 0
    # perdicted_pc_timestamps = []
    # while cnt < test_packet_num:
    #     asyncio.run(ring.calib_time())
    #     time.sleep(0.1)
    #     cnt += 1
    # ring_timestamps = np.array(ring.ring_timestamps)
    # pc_timestamps = (np.array(ring.start_calib_timestamps) + np.array(ring.end_calib_timestamps)) / 2
    # pc_delta_time = np.array(ring.end_calib_timestamps) - np.array(ring.start_calib_timestamps)

    # perdicted_pc_timestamps = [slope * x + intercept for x in ring_timestamps]

    # print('pc_timestamps:', pc_timestamps)
    # print('perdicted_pc_timestamps:', perdicted_pc_timestamps)
    # print('ring_timestamps:', ring_timestamps)
    # print('pc_delta_time:', pc_delta_time)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--ring', type=str, required=True)
    args = parser.parse_args()
    calib_time(args.ring)
