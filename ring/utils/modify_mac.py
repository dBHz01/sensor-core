import time
import asyncio
import argparse
from ..ble_ring_v1 import BLERing
from threading import Thread

def connect(ring):
  asyncio.run(ring.connect())

def modify_mac(ring_mac, new_mac, new_name):
  ring = BLERing(ring_mac, index=0)
  ring_thread = Thread(target=connect, args=(ring,))
  ring_thread.daemon = True
  ring_thread.start()
  while not ring.connected:
    time.sleep(1)
  ring.send_action('LEDSET=[RB]')
  time.sleep(1)
  confirm = input('Modify mac to [{}] and name to [{}]? [yes/no]'.format(new_mac, new_name))
  print(confirm)
  if confirm.strip().lower() == 'yes':
    ring.send_action('DEVINFO')
    time.sleep(2)
    ring.send_action('DEVINFO={},{}'.format(new_mac, new_name))
    time.sleep(2)
    ring.send_action('DEVINFO')
    time.sleep(2)
    ring.send_action('REBOOT')
    time.sleep(2)
    ring.send_action('disconnect')
  time.sleep(2)

if __name__ == '__main__':
  parser = argparse.ArgumentParser()
  parser.add_argument('--ring', type=str, required=True)
  parser.add_argument('--new_mac', type=str, required=True)
  parser.add_argument('--new_name', type=str, required=True)
  args = parser.parse_args()
  modify_mac(args.ring, args.new_mac, args.new_name)
