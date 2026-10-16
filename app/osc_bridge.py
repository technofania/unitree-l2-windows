"""
osc_bridge.py - wysyla dane z Unitree L2 po OSC (np. do TouchDesigner).

Adresy OSC (wszystkie wartosci float32):
  /l2/imu          w x y z  roll pitch yaw  ax ay az  gx gy gz       (13 wartosci)
                   kwaternion, katy [deg], przyspieszenie [m/s^2], zyroskop [rad/s]
  /l2/imu/quat     w x y z
  /l2/imu/euler    roll pitch yaw  [deg]
  /l2/imu/acc      ax ay az        [m/s^2]
  /l2/imu/gyro     gx gy gz        [rad/s]
  /l2/cloud        x0 y0 z0 i0  x1 y1 z1 i1 ...  (do --points punktow, i = intensywnosc 0..1)
  /l2/cloud/count  n
  /l2/grid         W*H wartosci 0..1, wiersz po wierszu (widok z gory, siatka zajetosci)
  /l2/grid/info    W H rozmiar_komorki_m zakres_m

Uruchomienie:
    python osc_bridge.py [--host 127.0.0.1] [--port 9000] [--points 400] [--frames 12]
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import numpy as np
from pythonosc.osc_message_builder import OscMessageBuilder
from pythonosc.udp_client import SimpleUDPClient

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from l2_receiver import L2Receiver  # noqa: E402


def downsample(xyz: np.ndarray, inten: np.ndarray, n: int, voxel: float, rng: np.random.Generator):
    """Rownomierny przestrzennie downsampling: 1 punkt na voxel, potem losowo do n."""
    if len(xyz) == 0:
        return xyz, inten
    keys = np.floor(xyz / voxel).astype(np.int32)
    _, idx = np.unique(keys, axis=0, return_index=True)
    if len(idx) > n:
        idx = rng.choice(idx, n, replace=False)
    idx.sort()
    return xyz[idx], inten[idx]


def occupancy_grid(xyz: np.ndarray, size: int, extent: float, zmin: float, zmax: float):
    """Siatka size x size komorek, srodek = lidar, bok = 2*extent [m]. Wartosci 0..1."""
    sel = (xyz[:, 2] >= zmin) & (xyz[:, 2] <= zmax)
    p = xyz[sel]
    cell = 2 * extent / size
    # wiersz 0 = +Y (przod na gorze obrazu), kolumna 0 = -X
    col = np.floor((p[:, 0] + extent) / cell).astype(np.int32)
    row = np.floor((extent - p[:, 1]) / cell).astype(np.int32)
    ok = (col >= 0) & (col < size) & (row >= 0) & (row < size)
    grid = np.zeros((size, size), np.float32)
    np.add.at(grid, (row[ok], col[ok]), 1.0)
    if grid.max() > 0:
        # log-skala, zeby pojedyncze punkty tez bylo widac
        grid = np.log1p(grid) / np.log1p(grid.max())
    return grid, cell


def send_floats(client: SimpleUDPClient, address: str, values):
    msg = OscMessageBuilder(address=address)
    for v in values:
        msg.add_arg(float(v), OscMessageBuilder.ARG_TYPE_FLOAT)
    client.send(msg.build())


def main():
    ap = argparse.ArgumentParser(description="Unitree L2 -> OSC")
    ap.add_argument("--host", default="127.0.0.1", help="adres odbiorcy OSC")
    ap.add_argument("--port", type=int, default=9000, help="port odbiorcy OSC")
    ap.add_argument("--points", type=int, default=400, help="max punktow w /l2/cloud")
    ap.add_argument("--voxel", type=float, default=0.15, help="rozmiar voxela do downsamplingu [m]")
    ap.add_argument("--frames", type=int, default=12, help="akumulacja klatek (12 ~ 1 s)")
    ap.add_argument("--cloud-rate", type=float, default=10.0, help="ile razy/s wysylac chmure i siatke")
    ap.add_argument("--imu-rate", type=float, default=60.0, help="ile razy/s wysylac IMU (lidar daje ~250)")
    ap.add_argument("--grid", type=int, default=32, help="rozdzielczosc siatki (N x N), 0 = wylacz")
    ap.add_argument("--grid-extent", type=float, default=5.0, help="polowa boku siatki [m]")
    ap.add_argument("--grid-zmin", type=float, default=-0.5, help="min wysokosc punktow w siatce [m]")
    ap.add_argument("--grid-zmax", type=float, default=1.5, help="max wysokosc punktow w siatce [m]")
    ap.add_argument("--range-min", type=float, default=0.25)
    ap.add_argument("--local-ip", default="192.168.1.2")
    ap.add_argument("--local-port", type=int, default=6201)
    ap.add_argument("--duration", type=float, default=0, help="(test) zakoncz po N s")
    args = ap.parse_args()

    client = SimpleUDPClient(args.host, args.port)
    rx = L2Receiver(local_ip=args.local_ip, local_port=args.local_port,
                    range_min=args.range_min).start()
    rng = np.random.default_rng()
    if rx.is_relay_client:
        print("Port 6201 zajety przez inny program (np. viewer) - odbieram przez przekaznik 127.0.0.1:6202.")

    print(f"Unitree L2 -> OSC {args.host}:{args.port}  (chmura {args.points} pkt, "
          f"{args.cloud_rate:g}/s, IMU {args.imu_rate:g}/s, siatka {args.grid}x{args.grid})")
    print("Zamknij to okno albo Ctrl+C, zeby zatrzymac.", flush=True)

    t0 = time.time()
    next_cloud = next_imu = next_info = t0
    sent = {"imu": 0, "cloud": 0}
    last_imu_seq = None
    try:
        while True:
            now = time.time()
            if args.duration and now - t0 > args.duration:
                break

            imu = rx.last_imu
            if now >= next_imu and imu is not None and imu.seq != last_imu_seq:
                last_imu_seq = imu.seq
                q, acc, gyr = imu.quaternion, imu.linear_acceleration, imu.angular_velocity
                eul = imu.euler_deg()
                send_floats(client, "/l2/imu", (*q, *eul, *acc, *gyr))
                send_floats(client, "/l2/imu/quat", q)
                send_floats(client, "/l2/imu/euler", eul)
                send_floats(client, "/l2/imu/acc", acc)
                send_floats(client, "/l2/imu/gyro", gyr)
                sent["imu"] += 1
                next_imu = max(next_imu + 1.0 / args.imu_rate, now - 0.1)

            if now >= next_cloud:
                next_cloud = now + 1.0 / args.cloud_rate
                xyz, inten = rx.get_points(frames=args.frames)
                if len(xyz):
                    pts, it = downsample(xyz, inten, args.points, args.voxel, rng)
                    flat = np.column_stack([pts, it / 255.0]).ravel()
                    send_floats(client, "/l2/cloud", flat)
                    send_floats(client, "/l2/cloud/count", [len(pts)])
                    if args.grid > 0:
                        grid, cell = occupancy_grid(xyz, args.grid, args.grid_extent,
                                                    args.grid_zmin, args.grid_zmax)
                        send_floats(client, "/l2/grid", grid.ravel())
                        send_floats(client, "/l2/grid/info",
                                    [args.grid, args.grid, cell, args.grid_extent])
                    sent["cloud"] += 1

            if now >= next_info:
                s = rx.stats
                state = "OK" if s["points_pkts"] else "CZEKAM NA DANE (lidar sie nagrzewa? kabel? IP?)"
                print(f"[{time.strftime('%H:%M:%S')}] {state} | odebrane: chmura {s['points_pkts']}, "
                      f"IMU {s['imu_pkts']} | wyslane OSC: chmura {sent['cloud']}, IMU {sent['imu']}",
                      flush=True)
                next_info = now + 5

            time.sleep(0.002)
    except KeyboardInterrupt:
        pass
    finally:
        rx.close()


if __name__ == "__main__":
    main()
