"""
l2_shm.py - pamiec wspoldzielona miedzy l2_server.py a odbiorcami (TouchDesigner, wlasne skrypty).

Uklad bloku 'unitree_l2_v1':
  [0 .. 512)      naglowek: 64 x float64 (indeksy w H)
  [512 .. 1024)   status serwera (tekst UTF-8, zakonczony \\0)
  RING_A_OFF      ring_a: CAP x 4 float32 - x, y, z (osie TD, Y w gore), czas [s od EPOCH]
  RING_B_OFF      ring_b: CAP x 4 float32 - intensywnosc 0..1, fg 0/1, 0, 1
  TOP_OFF         mapa z gory: TOP_MAX*TOP_MAX*4 float32 (uzyte TOP_RES x TOP_RES x 4)

Przyklad (wlasny skrypt):
    from l2_shm import Client
    c = Client()
    if c.attach():
        a, b = c.alive_points(1.0)     # punkty z ostatniej sekundy
"""
import os
import subprocess
import sys
import time

import numpy as np

NAME = 'unitree_l2_v1'
MAGIC = 20261016.0
CAP = 131072                      # 512 x 256 tekstura
TEX_W, TEX_H = 512, 256
TOP_MAX = 512
HDR_N = 64
STATUS_OFF, STATUS_LEN = 512, 512
RING_A_OFF = 1024
RING_B_OFF = RING_A_OFF + CAP * 16
TOP_OFF = RING_B_OFF + CAP * 16
TOTAL = TOP_OFF + TOP_MAX * TOP_MAX * 16

# indeksy naglowka
H = dict(
    MAGIC=0, EPOCH=1, BEAT=2, HEAD=3, CAP=4, RATE=5, N_ALIVE=6, N_FG=7, HAS_BG=8, LEARNING=9,
    MAP_MS=10, TOP_RES=11, IMU=12,            # IMU: 12..24 = w x y z roll pitch yaw ax ay az gx gy gz
    IMU_SEQ=25, RELAY=26, PID=27, PKTS=28, CRC=29,
    # od klienta do serwera:
    CLIENT_BEAT=40, LEARN_REQ=41, LEARN_SEC=42, CLEAR_REQ=43, PERSIST=44, ZMIN=45, ZMAX=46,
    EXTENT=47, TOPRES=48, TOPFG=49, QUIT_REQ=50,
)
IMU_NAMES = ('w', 'x', 'y', 'z', 'roll', 'pitch', 'yaw', 'ax', 'ay', 'az', 'gx', 'gy', 'gz')


def views(buf):
    """Widoki numpy na blok pamieci."""
    hdr = np.ndarray((HDR_N,), np.float64, buffer=buf, offset=0)
    ring_a = np.ndarray((CAP, 4), np.float32, buffer=buf, offset=RING_A_OFF)
    ring_b = np.ndarray((CAP, 4), np.float32, buffer=buf, offset=RING_B_OFF)
    top = np.ndarray((TOP_MAX * TOP_MAX * 4,), np.float32, buffer=buf, offset=TOP_OFF)
    return hdr, ring_a, ring_b, top


def kit_path_file():
    return os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'UnitreeL2', 'kit_path.txt')


def find_kit():
    """Folder paczki (zapisany przez instalator) albo None."""
    try:
        with open(kit_path_file(), encoding='utf-8') as f:
            p = f.read().strip()
        return p if os.path.isdir(p) else None
    except OSError:
        return None


class Client:
    def __init__(self):
        self.shm = None
        self.hdr = self.ring_a = self.ring_b = self.top = None
        self.launched_at = 0.0
        self.proc = None

    @property
    def attached(self):
        return self.shm is not None

    def attach(self):
        if self.shm is not None:
            return True
        from multiprocessing import shared_memory
        try:
            shm = shared_memory.SharedMemory(name=NAME, create=False)
        except FileNotFoundError:
            return False
        hdr, a, b, top = views(shm.buf)
        if hdr[H['MAGIC']] != MAGIC:
            del hdr, a, b, top
            shm.close()
            return False
        self.shm, self.hdr, self.ring_a, self.ring_b, self.top = shm, hdr, a, b, top
        return True

    def detach(self):
        self.hdr = self.ring_a = self.ring_b = self.top = None
        if self.shm is not None:
            try:
                self.shm.close()
            except Exception:
                pass                     # Windows: zwolni sie przy zamknieciu procesu
            self.shm = None

    # ---- stan serwera ----
    def server_alive(self, timeout=3.0):
        return self.attached and (time.time() - self.hdr[H['BEAT']]) < timeout

    def now(self):
        """Czas w skali kanalu w ring_a (do porownan / uniform shadera)."""
        return time.time() - self.hdr[H['EPOCH']]

    def status(self):
        if not self.attached:
            return ''
        raw = bytes(self.shm.buf[STATUS_OFF:STATUS_OFF + STATUS_LEN])
        return raw.split(b'\0')[0].decode('utf-8', 'replace')

    def imu(self):
        i = H['IMU']
        return dict(zip(IMU_NAMES, self.hdr[i:i + 13].tolist()))

    def topdown(self):
        res = int(self.hdr[H['TOP_RES']])
        if res <= 0:
            return None
        return self.top[:res * res * 4].reshape(res, res, 4)

    def alive_points(self, persistence=1.0):
        """Kopie (xyz w osiach TD, atrybuty) punktow mlodszych niz `persistence` [s]."""
        m = self.ring_a[:, 3] > self.now() - persistence
        return self.ring_a[m].copy(), self.ring_b[m].copy()

    # ---- polecenia do serwera ----
    def heartbeat(self, **params):
        h = self.hdr
        h[H['CLIENT_BEAT']] = time.time()
        for k, v in params.items():
            h[H[k.upper()]] = float(v)

    def learn_background(self, seconds=3.0):
        self.hdr[H['LEARN_SEC']] = seconds
        self.hdr[H['LEARN_REQ']] += 1

    def clear_background(self):
        self.hdr[H['CLEAR_REQ']] += 1

    def request_quit(self):
        if self.attached:
            self.hdr[H['QUIT_REQ']] += 1

    # ---- uruchamianie serwera ----
    def launch(self, kit=None, extra_args=(), python=None):
        """Uruchamia l2_server.py w tle (bez okna). Zwraca komunikat."""
        kit = kit or find_kit()
        if not kit:
            return 'Nie znam folderu paczki - uruchom INSTALUJ.bat albo ustaw parametr Kitpath'
        py = python or os.path.join(kit, 'venv', 'Scripts', 'pythonw.exe')
        srv = os.path.join(kit, 'app', 'l2_server.py')
        if not os.path.exists(py) or not os.path.exists(srv):
            return f'Brak {py} lub {srv} - czy paczka jest zainstalowana?'
        flags = 0x08000000 if sys.platform == 'win32' else 0   # CREATE_NO_WINDOW
        self.proc = subprocess.Popen([py, srv, '--auto-exit', *extra_args], cwd=kit,
                                     creationflags=flags, close_fds=True)
        self.launched_at = time.time()
        return 'uruchamiam serwer...'
