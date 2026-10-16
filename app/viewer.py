"""
viewer.py - podglad chmury punktow Unitree L2 na zywo (Open3D).

Klawisze (w oknie podgladu):
    S       zapisz aktualna (zakumulowana) chmure do scans\\l2_RRRRMMDD_GGMMSS.ply
    C       przelacz kolor: intensywnosc <-> wysokosc (Z)
    + / -   wiecej / mniej klatek w akumulacji
    Spacja  pauza
    R       wycentruj widok
    Q, Esc  wyjscie

Uruchomienie:
    python viewer.py [--frames 12] [--color intensity|height] [--point-size 2]
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import traceback
from datetime import datetime

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)  # glowny folder paczki (app\ jest podfolderem)
sys.path.insert(0, HERE)
from l2_receiver import L2Receiver  # noqa: E402

# Paleta "turbo" (skrocona) - bez zaleznosci od matplotlib
_TURBO = np.array([
    [0.19, 0.07, 0.23], [0.27, 0.35, 0.82], [0.16, 0.63, 0.98], [0.10, 0.85, 0.75],
    [0.35, 0.98, 0.42], [0.71, 0.96, 0.21], [0.96, 0.78, 0.22], [0.98, 0.49, 0.13],
    [0.85, 0.22, 0.04], [0.48, 0.02, 0.01],
], dtype=np.float32)


def colormap(v: np.ndarray) -> np.ndarray:
    """v w [0, 1] -> RGB Nx3."""
    v = np.clip(v, 0, 1) * (len(_TURBO) - 1)
    i = np.minimum(v.astype(np.int32), len(_TURBO) - 2)
    t = (v - i)[:, None]
    return _TURBO[i] * (1 - t) + _TURBO[i + 1] * t


def colorize(xyz: np.ndarray, inten: np.ndarray, mode: str) -> np.ndarray:
    if len(xyz) == 0:
        return np.zeros((0, 3), np.float32)
    if mode == "height":
        z = xyz[:, 2]
        lo, hi = np.percentile(z, 2), np.percentile(z, 98)
    else:
        z = inten
        lo, hi = np.percentile(inten, 2), np.percentile(inten, 98)
    return colormap((z - lo) / max(hi - lo, 1e-6))


def save_ply(path: str, xyz: np.ndarray, inten: np.ndarray, rgb: np.ndarray):
    """Binarny PLY: x y z (float), intensity (float), red green blue (uchar)."""
    n = len(xyz)
    rec = np.empty(n, dtype=[("x", "<f4"), ("y", "<f4"), ("z", "<f4"), ("intensity", "<f4"),
                             ("red", "u1"), ("green", "u1"), ("blue", "u1")])
    rec["x"], rec["y"], rec["z"] = xyz[:, 0], xyz[:, 1], xyz[:, 2]
    rec["intensity"] = inten
    c = (np.clip(rgb, 0, 1) * 255).astype(np.uint8)
    rec["red"], rec["green"], rec["blue"] = c[:, 0], c[:, 1], c[:, 2]
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        "comment Unitree L2, unit: meters\n"
        f"element vertex {n}\n"
        "property float x\nproperty float y\nproperty float z\nproperty float intensity\n"
        "property uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n"
    )
    with open(path, "wb") as f:
        f.write(header.encode("ascii"))
        f.write(rec.tobytes())


def main():
    ap = argparse.ArgumentParser(description="Podglad Unitree L2")
    ap.add_argument("--frames", type=int, default=12,
                    help="ile klatek akumulowac (1 klatka = 18 pakietow ~ 83 ms; 12 ~ 1 s)")
    ap.add_argument("--color", choices=["intensity", "height"], default="intensity")
    ap.add_argument("--point-size", type=float, default=2.0)
    ap.add_argument("--range-max", type=float, default=40.0, help="odrzucaj punkty dalej niz [m]")
    ap.add_argument("--range-min", type=float, default=0.25, help="odrzucaj punkty blizej niz [m]")
    ap.add_argument("--local-ip", default="192.168.1.2")
    ap.add_argument("--local-port", type=int, default=6201)
    ap.add_argument("--save-dir", default=os.path.join(ROOT, "scans"))
    ap.add_argument("--snapshot", help="(test) zapisz zrzut okna do pliku PNG i zakoncz")
    ap.add_argument("--snapshot-after", type=float, default=3.0)
    args = ap.parse_args()

    import open3d as o3d

    rx = L2Receiver(local_ip=args.local_ip, local_port=args.local_port,
                    range_min=args.range_min, range_max=args.range_max).start()

    state = {"frames": max(1, args.frames), "color": args.color, "paused": False,
             "quit": False, "reset_view": True, "xyz": None, "inten": None, "rgb": None}

    vis = o3d.visualization.VisualizerWithKeyCallback()
    vis.create_window("Unitree L2 - podglad na zywo", width=1280, height=800)
    opt = vis.get_render_option()
    opt.background_color = np.array([0.06, 0.06, 0.08])
    opt.point_size = args.point_size

    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(np.zeros((1, 3)))
    vis.add_geometry(pcd)
    axes = o3d.geometry.TriangleMesh.create_coordinate_frame(size=0.3)
    vis.add_geometry(axes)

    def log(msg):
        print(msg, flush=True)

    def on_save(_):
        if state["xyz"] is None or len(state["xyz"]) == 0:
            log("Brak punktow do zapisania")
            return False
        os.makedirs(args.save_dir, exist_ok=True)
        path = os.path.join(args.save_dir, datetime.now().strftime("l2_%Y%m%d_%H%M%S.ply"))
        save_ply(path, state["xyz"], state["inten"], state["rgb"])
        log(f"Zapisano {len(state['xyz'])} punktow -> {path}")
        return False

    def on_color(_):
        state["color"] = "height" if state["color"] == "intensity" else "intensity"
        log(f"Kolor: {state['color']}")
        return False

    def on_more(_):
        state["frames"] += 1 if state["frames"] < 12 else 6
        log(f"Akumulacja: {state['frames']} klatek (~{state['frames'] * rx.packets_per_frame / 216:.1f} s)")
        return False

    def on_less(_):
        state["frames"] = max(1, state["frames"] - (1 if state["frames"] <= 12 else 6))
        log(f"Akumulacja: {state['frames']} klatek (~{state['frames'] * rx.packets_per_frame / 216:.1f} s)")
        return False

    def on_pause(_):
        state["paused"] = not state["paused"]
        log("Pauza" if state["paused"] else "Wznowiono")
        return False

    def on_reset(_):
        state["reset_view"] = True
        return False

    def on_quit(_):
        state["quit"] = True
        return False

    vis.register_key_callback(ord("S"), on_save)
    vis.register_key_callback(ord("C"), on_color)
    vis.register_key_callback(ord("R"), on_reset)
    vis.register_key_callback(ord("Q"), on_quit)
    vis.register_key_callback(ord(" "), on_pause)
    for k in (ord("="), ord("+"), 334):   # 334 = '+' na klawiaturze numerycznej (GLFW)
        vis.register_key_callback(k, on_more)
    for k in (ord("-"), 333):             # 333 = '-' numeryczny
        vis.register_key_callback(k, on_less)

    log("Podglad L2: S=zapis PLY, C=kolor, +/-=klatki, Spacja=pauza, R=widok, Q=wyjscie")
    t_start = time.time()
    last_info = 0.0
    no_data_warned = False

    while not state["quit"]:
        if not state["paused"]:
            xyz, inten = rx.get_points(frames=state["frames"])
            if len(xyz):
                rgb = colorize(xyz, inten, state["color"])
                state.update(xyz=xyz, inten=inten, rgb=rgb)
                pcd.points = o3d.utility.Vector3dVector(xyz.astype(np.float64))
                pcd.colors = o3d.utility.Vector3dVector(rgb.astype(np.float64))
                vis.update_geometry(pcd)
                if state["reset_view"] and rx.stats["points_pkts"] > rx.packets_per_frame * 6:
                    vis.reset_view_point(True)
                    ctr = vis.get_view_control()
                    ctr.set_up([0, 0, 1])
                    ctr.set_front([-0.6, -0.6, 0.55])
                    ctr.set_lookat([0, 0, 0.8])
                    ctr.set_zoom(0.45)
                    state["reset_view"] = False
            elif not no_data_warned and time.time() - t_start > 5:
                log("Brak danych z lidaru od 5 s - sprawdz zasilanie/kabel/IP (README: Typowe problemy). "
                    "Zimny lidar moze sie najpierw nagrzewac.")
                no_data_warned = True

        if not vis.poll_events():
            break
        vis.update_renderer()

        now = time.time()
        if now - last_info > 5:
            s = rx.stats
            n = 0 if state["xyz"] is None else len(state["xyz"])
            log(f"pakiety chmury {s['points_pkts']}, IMU {s['imu_pkts']}, CRC err {s['crc_errors']}, "
                f"punktow w widoku {n}, klatek {state['frames']}")
            last_info = now

        if args.snapshot and now - t_start > args.snapshot_after:
            vis.capture_screen_image(args.snapshot, do_render=True)
            log(f"Zrzut: {args.snapshot}")
            break
        time.sleep(0.01)

    vis.destroy_window()
    rx.close()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        msg = traceback.format_exc()
        with open(os.path.join(ROOT, "viewer-error.log"), "w", encoding="utf-8") as f:
            f.write(msg)
        print(msg, file=sys.stderr)
        try:  # przy uruchomieniu bez konsoli (pythonw) pokaz okienko z bledem
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, msg[-1500:], "Unitree L2 viewer - blad", 0x10)
        except Exception:
            pass
        sys.exit(1)
