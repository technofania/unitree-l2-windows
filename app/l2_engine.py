"""
l2_engine.py - bufor kroczacy punktow Unitree L2 + model tla + mapa z gory.

Uzywany przez l2_server.py (osobny proces, ktory udostepnia dane TouchDesignerowi przez
pamiec wspoldzielona). Bufory moga byc podane z zewnatrz (np. widoki na pamiec wspoldzielona).

  ring_a[i] = x, y, z (osie TD: Y w gore = lidar Z), czas przyjscia [s od `epoch`]
  ring_b[i] = intensywnosc 0..1, fg (1 = nowy obiekt, spoza tla), 0, 1

Czas: time.time() - epoch (wspolny zegar dla wszystkich procesow na tym komputerze).
"""
import os
import threading
import time

import numpy as np


class Engine:
    MAP_SAT = 40.0          # tylu punktow w komorce mapy odpowiada pelna jasnosc

    def __init__(self, receiver_module, capacity=131072, ring_a=None, ring_b=None, top_buf=None,
                 epoch=None, lidar_ip='192.168.1.62', lidar_port=6101, local_ip='192.168.1.2',
                 local_port=6201, range_min=0.25, range_max=40.0, voxel=0.10, bg_file=None):
        self.cap = int(capacity)
        self.epoch = time.time() if epoch is None else float(epoch)
        self.ring_a = np.zeros((self.cap, 4), np.float32) if ring_a is None else ring_a
        self.ring_b = np.zeros((self.cap, 4), np.float32) if ring_b is None else ring_b
        self.ring_a[:] = 0
        self.ring_a[:, 3] = -1e6                      # "bardzo stare" = niewidoczne
        self.ring_b[:] = 0
        self.ring_b[:, 3] = 1.0
        self.top_buf = top_buf                        # plaski float32 (max_res*max_res*4) albo None
        self.top = None
        self.top_res = 0
        self.head = 0
        self.lock = threading.Lock()

        self.voxel = float(voxel)
        self.bg = None
        self.bg_file = bg_file
        self.learning_until = 0.0
        self._learn = []
        self.status = 'brak tla - kliknij "Naucz tlo" przy pustej scenie'

        self.n_alive = 0
        self.n_fg = 0
        self.map_ms = 0.0
        self.rate = 0.0
        self._rate_t, self._rate_head = time.time(), 0

        if bg_file and os.path.exists(bg_file):
            try:
                self.bg = np.load(bg_file)
                self.status = 'tlo wczytane z pliku'
            except Exception:
                pass

        self.rx = receiver_module.L2Receiver(lidar_ip, lidar_port, local_ip, local_port,
                                             range_min=range_min, range_max=range_max,
                                             buffer_packets=64)
        self.rx.points_callbacks.append(self._on_points)
        self.rx.start()
        self.relay = self.rx.is_relay_client

    def now(self):
        return time.time() - self.epoch

    def close(self):
        try:
            self.rx.close()
        except Exception:
            pass

    # ---------------- tlo ----------------
    def _keys(self, xyz):
        q = np.clip(np.floor(xyz / self.voxel).astype(np.int64) + 2048, 0, 4095)
        return (q[:, 0] << 24) | (q[:, 1] << 12) | q[:, 2]

    def learn_background(self, seconds=3.0):
        """Uczy sie tla przez `seconds` sekund - scena powinna byc wtedy pusta."""
        self._learn = []
        self.learning_until = time.time() + seconds
        self.status = 'uczenie tla...'

    def clear_background(self):
        self.bg = None
        self.learning_until = 0.0
        self.status = 'tlo wyczyszczone - brak nowych obiektow do czasu "Naucz tlo"'
        if self.bg_file and os.path.exists(self.bg_file):
            try:
                os.remove(self.bg_file)
            except Exception:
                pass

    def _finish_learning(self):
        if not self._learn:
            self.status = 'brak punktow do nauki tla'
            return
        keys = np.unique(np.concatenate(self._learn))
        # dylatacja 3x3x3 - szum pomiaru na granicy voxeli nie bedzie "nowym obiektem"
        d = np.array([(a << 24) + (b << 12) + c for a in (-1, 0, 1) for b in (-1, 0, 1)
                      for c in (-1, 0, 1)], np.int64)
        self.bg = np.unique((keys[:, None] + d[None, :]).ravel())
        self._learn = []
        self.status = f'tlo: {len(keys)} voxeli'
        if self.bg_file:
            try:
                np.save(self.bg_file, self.bg)
                self.status += ' (zapisane)'
            except Exception as e:
                self.status += f' (nie zapisano: {e})'

    # ---------------- watek odbioru ----------------
    def _on_points(self, xyz, inten):
        n = len(xyz)
        if n == 0:
            return
        now_abs = time.time()
        keys = None
        if self.learning_until:
            keys = self._keys(xyz)
            self._learn.append(np.unique(keys))
            if now_abs >= self.learning_until:
                self.learning_until = 0.0
                self._finish_learning()
        bg = self.bg
        if bg is not None and not self.learning_until:
            if keys is None:
                keys = self._keys(xyz)
            i = np.minimum(np.searchsorted(bg, keys), len(bg) - 1)
            fg = (bg[i] != keys).astype(np.float32)
        else:
            # bez tla (albo w trakcie nauki) nic nie jest "nowym obiektem"
            fg = np.zeros(n, np.float32)

        a = np.empty((n, 4), np.float32)
        a[:, 0], a[:, 1], a[:, 2] = xyz[:, 0], xyz[:, 2], -xyz[:, 1]   # lidar Z-up -> TD Y-up
        a[:, 3] = now_abs - self.epoch
        b = np.empty((n, 4), np.float32)
        b[:, 0] = inten * (1.0 / 255.0)
        b[:, 1] = fg
        b[:, 2] = 0.0
        b[:, 3] = 1.0

        with self.lock:
            s = self.head % self.cap
            k = min(n, self.cap - s)
            self.ring_a[s:s + k] = a[:k]
            self.ring_b[s:s + k] = b[:k]
            if k < n:
                self.ring_a[:n - k] = a[k:]
                self.ring_b[:n - k] = b[k:]
            self.head += n

    # ---------------- mapa z gory (do interakcji) ----------------
    def compute_map(self, persistence=1.5, z_min=-0.5, z_max=1.5, extent=5.0, top_res=128,
                    top_fg=True):
        """Mapa zajetosci z gory (osie lidaru: X w prawo, +Y u gory obrazu TD) + liczniki."""
        t_start = time.time()
        now = t_start - self.epoch
        a, b = self.ring_a, self.ring_b
        alive = a[:, 3] > (now - persistence)
        fg = b[:, 1] > 0.5
        self.n_alive = int(alive.sum())
        self.n_fg = int((alive & fg).sum())

        # kanal R: wszystkie punkty (do podgladu), kanal G: tylko nowe obiekty (do Blob Track)
        sel = alive & (a[:, 1] >= z_min) & (a[:, 1] <= z_max)   # a[:,1] = wysokosc (lidar Z)
        x = a[sel, 0]
        y = -a[sel, 2]
        f = fg[sel]
        cell = 2 * extent / top_res
        c = ((x + extent) / cell).astype(np.int32)
        r = ((y + extent) / cell).astype(np.int32)
        ok = (x > -extent) & (y > -extent) & (c < top_res) & (r < top_res)
        idx = r[ok] * top_res + c[ok]
        n_cells = top_res * top_res
        h_all = np.bincount(idx, minlength=n_cells).astype(np.float32)
        h_fg = np.bincount(idx[f[ok]], minlength=n_cells).astype(np.float32)
        # stala skala (log, nasycenie przy MAP_SAT punktach w komorce) - stabilny prog dla blobow,
        # pojedyncze punkty szumu daja slaby sygnal
        k = np.float32(1.0 / np.log1p(self.MAP_SAT))
        if self.top_buf is not None:
            top = self.top_buf[:n_cells * 4].reshape(top_res, top_res, 4)
        else:
            top = np.empty((top_res, top_res, 4), np.float32)
        top[..., 0] = np.minimum(np.log1p(h_all) * k, 1.0).reshape(top_res, top_res)
        top[..., 1] = np.minimum(np.log1p(h_fg) * k, 1.0).reshape(top_res, top_res)
        top[..., 2] = 0.0
        top[..., 3] = 1.0
        self.top, self.top_res = top, top_res

        t = time.time()
        if t - self._rate_t >= 0.5:
            head = self.head
            self.rate = (head - self._rate_head) / (t - self._rate_t)
            self._rate_t, self._rate_head = t, head
        self.map_ms = (t - t_start) * 1000
        return top
