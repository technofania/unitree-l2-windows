"""
l2_server.py - odbiera Unitree L2 i udostepnia punkty przez pamiec wspoldzielona (l2_shm.py).

Po co: TouchDesigner (i inne programy) czytaja gotowy bufor ~130 tys. punktow bez sieci
i bez obciazania wlasnego Pythona - odbior i dekodowanie dziela sie tutaj, w osobnym procesie.

Uruchamianie:
  - automatycznie przez komponent lidar_live w TD (w tle, bez okna, z --auto-exit),
  - albo recznie: start-server.bat (okno ze statusem).

    python l2_server.py [--auto-exit] [--bg-file plik.npy] [--local-ip 192.168.1.2] ...
"""
import argparse
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import l2_receiver  # noqa: E402
import l2_shm as S  # noqa: E402
from l2_engine import Engine  # noqa: E402


def open_block():
    """Tworzy blok pamieci; jesli istnieje i inny serwer zyje - zwraca None."""
    from multiprocessing import shared_memory
    try:
        return shared_memory.SharedMemory(name=S.NAME, create=True, size=S.TOTAL), True
    except FileExistsError:
        shm = shared_memory.SharedMemory(name=S.NAME, create=False)
        hdr = np.ndarray((S.HDR_N,), np.float64, buffer=shm.buf)
        alive = hdr[S.H['MAGIC']] == S.MAGIC and time.time() - hdr[S.H['BEAT']] < 3.0
        del hdr
        if alive:
            shm.close()
            return None, False
        return shm, False     # blok po martwym serwerze (trzyma go np. TD) - przejmujemy


def main():
    ap = argparse.ArgumentParser(description='Unitree L2 -> pamiec wspoldzielona')
    ap.add_argument('--local-ip', default='192.168.1.2')
    ap.add_argument('--local-port', type=int, default=6201)
    ap.add_argument('--lidar-ip', default='192.168.1.62')
    ap.add_argument('--lidar-port', type=int, default=6101)
    ap.add_argument('--range-min', type=float, default=0.25)
    ap.add_argument('--range-max', type=float, default=40.0)
    ap.add_argument('--voxel', type=float, default=0.10, help='rozdzielczosc modelu tla [m]')
    ap.add_argument('--bg-file', default=os.path.join(ROOT, 'scans', 'background.npy'),
                    help='plik z nauczonym tlem (wczytywany przy starcie)')
    ap.add_argument('--auto-exit', action='store_true',
                    help='zakoncz, gdy zaden klient (TD) nie daje znaku zycia przez 10 s')
    ap.add_argument('--hz', type=float, default=30.0, help='czestotliwosc mapy/statystyk')
    args = ap.parse_args()

    shm, created = open_block()
    if shm is None:
        print('Serwer juz dziala - koncze.')
        return
    hdr, ring_a, ring_b, top = S.views(shm.buf)
    hdr[:] = 0
    os.makedirs(os.path.dirname(args.bg_file), exist_ok=True)

    eng = Engine(l2_receiver, capacity=S.CAP, ring_a=ring_a, ring_b=ring_b, top_buf=top,
                 lidar_ip=args.lidar_ip, lidar_port=args.lidar_port, local_ip=args.local_ip,
                 local_port=args.local_port, range_min=args.range_min, range_max=args.range_max,
                 voxel=args.voxel, bg_file=args.bg_file)
    h = S.H
    hdr[h['EPOCH']] = eng.epoch
    hdr[h['CAP']] = S.CAP
    hdr[h['PID']] = os.getpid()
    hdr[h['RELAY']] = float(eng.relay)
    # domyslne parametry (klient je nadpisuje)
    defaults = dict(PERSIST=1.5, ZMIN=-0.5, ZMAX=1.5, EXTENT=5.0, TOPRES=128, TOPFG=1.0)
    for k, v in defaults.items():
        hdr[h[k]] = v
    hdr[h['CLIENT_BEAT']] = time.time() + 20          # 30 s laski na podlaczenie klienta
    hdr[h['MAGIC']] = S.MAGIC

    print(f'Serwer Unitree L2: pamiec "{S.NAME}" ({S.TOTAL / 1e6:.1f} MB), '
          f'{"przekaznik" if eng.relay else "port " + str(args.local_port)}, tlo: {args.bg_file}')
    learn_seen, clear_seen, quit_seen = hdr[h['LEARN_REQ']], hdr[h['CLEAR_REQ']], hdr[h['QUIT_REQ']]
    period = 1.0 / args.hz
    last_print = 0.0
    try:
        while True:
            t0 = time.time()
            # polecenia od klienta
            if hdr[h['LEARN_REQ']] != learn_seen:
                learn_seen = hdr[h['LEARN_REQ']]
                eng.learn_background(max(0.5, float(hdr[h['LEARN_SEC']] or 3.0)))
            if hdr[h['CLEAR_REQ']] != clear_seen:
                clear_seen = hdr[h['CLEAR_REQ']]
                eng.clear_background()
            if hdr[h['QUIT_REQ']] != quit_seen:
                print('Klient poprosil o zakonczenie.')
                break
            if args.auto_exit and t0 - hdr[h['CLIENT_BEAT']] > 10:
                print('Brak klienta od 10 s - koncze (--auto-exit).')
                break

            res = int(min(S.TOP_MAX, max(8, hdr[h['TOPRES']])))
            eng.compute_map(persistence=float(hdr[h['PERSIST']]), z_min=float(hdr[h['ZMIN']]),
                            z_max=float(hdr[h['ZMAX']]), extent=float(hdr[h['EXTENT']]),
                            top_res=res, top_fg=bool(hdr[h['TOPFG']]))
            hdr[h['TOP_RES']] = res
            hdr[h['HEAD']] = eng.head
            hdr[h['RATE']] = eng.rate
            hdr[h['N_ALIVE']] = eng.n_alive
            hdr[h['N_FG']] = eng.n_fg
            hdr[h['HAS_BG']] = float(eng.bg is not None)
            hdr[h['LEARNING']] = float(bool(eng.learning_until))
            hdr[h['MAP_MS']] = eng.map_ms
            hdr[h['PKTS']] = eng.rx.stats['points_pkts']
            hdr[h['CRC']] = eng.rx.stats['crc_errors']
            im = eng.rx.last_imu
            if im is not None:
                i = h['IMU']
                hdr[i:i + 13] = (*im.quaternion, *im.euler_deg(), *im.linear_acceleration,
                                 *im.angular_velocity)
                hdr[h['IMU_SEQ']] = im.seq
            st = eng.status.encode('utf-8')[:S.STATUS_LEN - 1] + b'\0'
            shm.buf[S.STATUS_OFF:S.STATUS_OFF + len(st)] = st
            hdr[h['BEAT']] = time.time()

            if t0 - last_print > 5:
                print(f'[{time.strftime("%H:%M:%S")}] {eng.rate:7.0f} pkt/s | zywych {eng.n_alive:6d} | '
                      f'nowe obiekty {eng.n_fg:5d} | mapa {eng.map_ms:4.1f} ms | {eng.status}', flush=True)
                last_print = t0
            time.sleep(max(0.002, period - (time.time() - t0)))
    except KeyboardInterrupt:
        pass
    finally:
        hdr[h['BEAT']] = 0
        eng.close()
        del hdr, ring_a, ring_b, top
        eng.ring_a = eng.ring_b = eng.top_buf = eng.top = None
        try:
            shm.close()
            if created:
                shm.unlink()
        except Exception:
            pass


if __name__ == '__main__':
    main()
