"""
l2_receiver.py - natywny (bez ROS/SDK) odbiornik Unitree 4D LiDAR L2 po UDP.

Przeniesione 1:1 z unilidar_sdk2:
  - unitree_lidar_protocol.h  (struktury pakietow, typy, komendy)
  - unitree_lidar_utilities.h (crc32, parseFromPacketToPointCloud)

Uzycie jako modul:
    from l2_receiver import L2Receiver
    rx = L2Receiver()            # 192.168.1.2:6201 <- 192.168.1.62:6101
    rx.start()
    pts, inten = rx.get_points(packets=216)   # ostatnie 216 pakietow (~1 s)
    imu = rx.last_imu

Uzycie z linii polecen (test):
    python l2_receiver.py              # statystyki + 10 pierwszych punktow
    python l2_receiver.py --cmd version
    python l2_receiver.py --cmd start|stop|reset
"""
from __future__ import annotations

import argparse
import socket
import struct
import sys
import threading
import time
import zlib
from collections import deque
from dataclasses import dataclass

import numpy as np

# --------------------------------------------------------------------------
# Stale z unitree_lidar_protocol.h
# --------------------------------------------------------------------------
FRAME_HEADER = b"\x55\xAA\x05\x0A"
FRAME_TAIL = b"\x00\xFF"
HEADER_SIZE = 12  # header[4], packet_type u32, packet_size u32
TAIL_SIZE = 12    # crc32 u32, msg_type_check u32, reserve[2], tail[2]

LIDAR_USER_CMD_PACKET_TYPE = 100
LIDAR_ACK_DATA_PACKET_TYPE = 101
LIDAR_POINT_DATA_PACKET_TYPE = 102
LIDAR_2D_POINT_DATA_PACKET_TYPE = 103
LIDAR_IMU_DATA_PACKET_TYPE = 104
LIDAR_VERSION_PACKET_TYPE = 105
LIDAR_TIME_STAMP_PACKET_TYPE = 106
LIDAR_WORK_MODE_CONFIG_PACKET_TYPE = 107
LIDAR_IP_ADDRESS_CONFIG_PACKET_TYPE = 108
LIDAR_MAC_ADDRESS_CONFIG_PACKET_TYPE = 109

USER_CMD_RESET_TYPE = 1
USER_CMD_STANDBY_TYPE = 2   # value 0: start; value 1: standby
USER_CMD_VERSION_GET = 3
USER_CMD_LATENCY_TYPE = 4
USER_CMD_CONFIG_RESET = 5
USER_CMD_CONFIG_GET = 6
USER_CMD_CONFIG_AUTO_STANDBY = 7

ACK_STATUS = {1: "SUCCESS", 2: "CRC_ERROR", 3: "HEADER_ERROR", 4: "BLOCK_ERROR", 5: "WAIT_ERROR"}

PACKET_NAMES = {
    100: "USER_CMD", 101: "ACK", 102: "POINT", 103: "POINT_2D", 104: "IMU",
    105: "VERSION", 106: "TIME_STAMP", 107: "WORK_MODE", 108: "IP_CONFIG", 109: "MAC_CONFIG",
}

# --------------------------------------------------------------------------
# Struktury jako numpy dtype (little endian, bez paddingu - jak w C)
# --------------------------------------------------------------------------
_DATA_INFO = [("seq", "<u4"), ("payload_size", "<u4"), ("sec", "<u4"), ("nsec", "<u4")]

_INSIDE_STATE = [
    ("sys_rotation_period", "<u4"), ("com_rotation_period", "<u4"),
    ("dirty_index", "<f4"), ("packet_lost_up", "<f4"), ("packet_lost_down", "<f4"),
    ("apd_temperature", "<f4"), ("apd_voltage", "<f4"), ("laser_voltage", "<f4"),
    ("imu_temperature", "<f4"),
]

_CALIB = [
    ("a_axis_dist", "<f4"), ("b_axis_dist", "<f4"), ("theta_angle_bias", "<f4"),
    ("alpha_angle_bias", "<f4"), ("beta_angle", "<f4"), ("xi_angle", "<f4"),
    ("range_bias", "<f4"), ("range_scale", "<f4"),
]

POINT_DATA_DTYPE = np.dtype(
    _DATA_INFO + _INSIDE_STATE + _CALIB + [
        ("com_horizontal_angle_start", "<f4"), ("com_horizontal_angle_step", "<f4"),
        ("scan_period", "<f4"), ("range_min", "<f4"), ("range_max", "<f4"),
        ("angle_min", "<f4"), ("angle_increment", "<f4"), ("time_increment", "<f4"),
        ("point_num", "<u4"),
        ("ranges", "<u2", (300,)), ("intensities", "u1", (300,)),
    ]
)
assert POINT_DATA_DTYPE.itemsize == 1020

POINT_2D_DATA_DTYPE = np.dtype(
    _DATA_INFO + _INSIDE_STATE + _CALIB + [
        ("scan_period", "<f4"), ("range_min", "<f4"), ("range_max", "<f4"),
        ("angle_min", "<f4"), ("angle_increment", "<f4"), ("time_increment", "<f4"),
        ("point_num", "<u4"),
        ("ranges", "<u2", (1800,)), ("intensities", "u1", (1800,)),
    ]
)

IMU_DATA_DTYPE = np.dtype(
    _DATA_INFO + [
        ("quaternion", "<f4", (4,)),            # w, x, y, z (patrz ImuSample)
        ("angular_velocity", "<f4", (3,)),      # rad/s
        ("linear_acceleration", "<f4", (3,)),   # m/s^2
    ]
)
assert IMU_DATA_DTYPE.itemsize == 56


# --------------------------------------------------------------------------
# CRC i ramkowanie
# --------------------------------------------------------------------------
def crc32(buf: bytes) -> int:
    """crc32 z utilities.h (poly 0xEDB88320, init 0xFFFFFFFF, ~crc) == zlib.crc32."""
    return zlib.crc32(buf) & 0xFFFFFFFF


class PacketError(ValueError):
    pass


def check_packet(buf: bytes) -> tuple[int, memoryview]:
    """Sprawdza naglowek, dlugosc, stopke i CRC. Zwraca (packet_type, dane)."""
    if len(buf) < HEADER_SIZE + TAIL_SIZE:
        raise PacketError(f"za krotki pakiet ({len(buf)} B)")
    if buf[:4] != FRAME_HEADER:
        raise PacketError("zly naglowek")
    ptype, psize = struct.unpack_from("<II", buf, 4)
    if psize != len(buf):
        raise PacketError(f"packet_size={psize} != dlugosc UDP {len(buf)}")
    if buf[-2:] != FRAME_TAIL:
        raise PacketError("zla stopka")
    data_end = psize - TAIL_SIZE
    (crc_rx,) = struct.unpack_from("<I", buf, data_end)
    # Komentarz w protocol.h mowi "head and data", ale sprawdzone na prawdziwych
    # pakietach L2: CRC liczone jest wylacznie z czesci danych (bez FrameHeader).
    if crc32(buf[HEADER_SIZE:data_end]) != crc_rx:
        raise PacketError("blad CRC")
    return ptype, memoryview(buf)[HEADER_SIZE:data_end]


def build_packet(packet_type: int, payload: bytes, msg_type_check: int = 0) -> bytes:
    """Sklada pakiet w formacie FrameHeader + dane + FrameTail."""
    size = HEADER_SIZE + len(payload) + TAIL_SIZE
    header = FRAME_HEADER + struct.pack("<II", packet_type, size)
    return header + payload + struct.pack("<II", crc32(payload), msg_type_check) + b"\x00\x00" + FRAME_TAIL


def user_cmd_packet(cmd_type: int, cmd_value: int = 0) -> bytes:
    return build_packet(LIDAR_USER_CMD_PACKET_TYPE, struct.pack("<II", cmd_type, cmd_value))


def work_mode_packet(mode: int) -> bytes:
    return build_packet(LIDAR_WORK_MODE_CONFIG_PACKET_TYPE, struct.pack("<I", mode))


# --------------------------------------------------------------------------
# Dekodowanie
# --------------------------------------------------------------------------
def decode_points(data, range_min: float = 0.0, range_max: float = 100.0):
    """
    parseFromPacketToPointCloud() z unitree_lidar_utilities.h, zwektoryzowane.
    Zwraca (xyz float32 Nx3 [m], intensity float32 N, time float32 N, rekord naglowka).
    Obliczenia w float32 i z sekwencyjnym sumowaniem katow - tak jak petla w C.
    """
    d = np.frombuffer(data, dtype=POINT_DATA_DTYPE, count=1)[0]
    p = d  # aliasy jak w C: packet.data.param.* / packet.data.*
    f32 = np.float32
    n = int(min(d["point_num"], 300))
    if n == 0:
        empty = np.zeros((0,), f32)
        return np.zeros((0, 3), f32), empty, empty, d

    sin_beta, cos_beta = np.sin(f32(p["beta_angle"])), np.cos(f32(p["beta_angle"]))
    sin_xi, cos_xi = np.sin(f32(p["xi_angle"])), np.cos(f32(p["xi_angle"]))
    cos_beta_sin_xi = f32(cos_beta * sin_xi)
    sin_beta_cos_xi = f32(sin_beta * cos_xi)
    sin_beta_sin_xi = f32(sin_beta * sin_xi)
    cos_beta_cos_xi = f32(cos_beta * cos_xi)

    def accumulate(start, step):
        # alpha_cur += alpha_step w petli float -> sekwencyjna suma w float32
        a = np.full(n, f32(step), dtype=f32)
        a[0] = f32(start)
        return np.add.accumulate(a, dtype=f32)

    alpha = accumulate(f32(d["angle_min"]) + f32(p["alpha_angle_bias"]), d["angle_increment"])
    theta = accumulate(f32(d["com_horizontal_angle_start"]) + f32(p["theta_angle_bias"]),
                       d["com_horizontal_angle_step"])
    trel = accumulate(0.0, d["time_increment"])

    ranges = d["ranges"][:n]
    rng = f32(p["range_scale"]) * (ranges.astype(f32) + f32(p["range_bias"]))

    valid = (ranges >= 1)
    valid &= (rng >= d["range_min"]) & (rng <= d["range_max"])
    valid &= (rng >= range_min) & (rng <= range_max)

    rng, alpha, theta, trel = rng[valid], alpha[valid], theta[valid], trel[valid]
    inten = d["intensities"][:n][valid].astype(f32)

    sin_a, cos_a = np.sin(alpha), np.cos(alpha)
    sin_t, cos_t = np.sin(theta), np.cos(theta)

    A = (-cos_beta_sin_xi + sin_beta_cos_xi * sin_a) * rng + f32(p["b_axis_dist"])
    B = cos_a * cos_xi * rng
    C = (sin_beta_sin_xi + cos_beta_cos_xi * sin_a) * rng

    xyz = np.empty((rng.size, 3), f32)
    xyz[:, 0] = cos_t * A - sin_t * B
    xyz[:, 1] = sin_t * A + cos_t * B
    xyz[:, 2] = C + f32(p["a_axis_dist"])
    return xyz, inten, trel, d


def decode_points_2d(data, range_min: float = 0.0, range_max: float = 100.0):
    """parseFromPacketPointCloud2D() z utilities.h (tryb 2D)."""
    d = np.frombuffer(data, dtype=POINT_2D_DATA_DTYPE, count=1)[0]
    f32 = np.float32
    n = int(min(d["point_num"], 1800))
    a = np.full(n, f32(d["angle_increment"]), f32)
    if n:
        a[0] = f32(d["angle_min"]) + f32(d["alpha_angle_bias"])
    alpha = np.add.accumulate(a, dtype=f32)
    ranges = d["ranges"][:n]
    rng = f32(d["range_scale"]) * (ranges.astype(f32) + f32(d["range_bias"]))
    valid = (ranges >= 1) & (rng >= d["range_min"]) & (rng <= d["range_max"]) \
        & (rng >= range_min) & (rng <= range_max)
    rng, alpha = rng[valid], alpha[valid]
    xyz = np.zeros((rng.size, 3), f32)
    xyz[:, 1] = np.cos(alpha) * rng
    xyz[:, 2] = np.sin(alpha) * rng + f32(d["a_axis_dist"])
    return xyz, d["intensities"][:n][valid].astype(f32), d


@dataclass
class ImuSample:
    seq: int
    stamp: float
    # Kolejnosc (w, x, y, z): tak robi sterownik ROS1/TF w SDK i to zgadza sie
    # z akcelerometrem (lidar poziomo -> q ~ (1, 0, 0, 0)). Komentarz "(x, y, z, w)"
    # w example.h jest bledny.
    quaternion: tuple      # (w, x, y, z)
    angular_velocity: tuple       # rad/s
    linear_acceleration: tuple    # m/s^2

    def euler_deg(self):
        """roll, pitch, yaw [deg] z kwaternionu (w, x, y, z)."""
        w, x, y, z = self.quaternion
        roll = np.degrees(np.arctan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y)))
        pitch = np.degrees(np.arcsin(np.clip(2 * (w * y - z * x), -1, 1)))
        yaw = np.degrees(np.arctan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z)))
        return float(roll), float(pitch), float(yaw)


def decode_imu(data) -> ImuSample:
    d = np.frombuffer(data, dtype=IMU_DATA_DTYPE, count=1)[0]
    return ImuSample(
        seq=int(d["seq"]), stamp=d["sec"] + d["nsec"] / 1e9,
        quaternion=tuple(float(v) for v in d["quaternion"]),
        angular_velocity=tuple(float(v) for v in d["angular_velocity"]),
        linear_acceleration=tuple(float(v) for v in d["linear_acceleration"]),
    )


def describe_work_mode(mode: int) -> str:
    return ", ".join([
        "FOV szerokie 192°" if mode & 1 else "FOV standard 180°",
        "2D" if mode & 2 else "3D",
        "IMU WYLACZONE" if mode & 4 else "IMU wlaczone",
        "port szeregowy" if mode & 8 else "Ethernet",
        "czeka na komende startu" if mode & 16 else "autostart",
    ])


def decode_version(data) -> dict:
    b = bytes(data)
    return {
        "hardware": ".".join(str(v) for v in b[0:4]),
        "firmware": ".".join(str(v) for v in b[4:8]),
        "name": b[8:32].split(b"\x00")[0].decode(errors="replace"),
        "date": b[32:40].split(b"\x00")[0].decode(errors="replace"),
    }


# --------------------------------------------------------------------------
# Odbiornik
# --------------------------------------------------------------------------
class L2Receiver:
    """
    Odbiera pakiety w osobnym watku i trzyma bufor ostatnich pakietow chmury.
    "Klatka" = packets_per_frame pakietow (SDK: cloud_scan_num = 18, ok. 5400 pomiarow).
    """

    def __init__(self, lidar_ip="192.168.1.62", lidar_port=6101,
                 local_ip="192.168.1.2", local_port=6201,
                 packets_per_frame=18, buffer_packets=4000,
                 range_min=0.0, range_max=100.0):
        self.lidar_addr = (lidar_ip, lidar_port)
        self.local_addr = (local_ip, local_port)
        self.packets_per_frame = packets_per_frame
        self.range_min, self.range_max = range_min, range_max

        self._buf = deque(maxlen=buffer_packets)  # (xyz, intensity)
        self._lock = threading.Lock()
        self._thread = None
        self._running = False
        self.sock = None
        self._relay = None
        self.is_relay_client = False

        self.last_imu: ImuSample | None = None
        self.last_point_header = None
        self.last_ack = None
        self.version = None
        self.work_mode = None
        self.imu_callbacks = []
        self.points_callbacks = []  # cb(xyz Nx3, intensity N) dla kazdego pakietu chmury (watek odbioru!)
        self.stats = {"packets": 0, "points_pkts": 0, "imu_pkts": 0, "crc_errors": 0,
                      "bad_packets": 0, "other": {}, "seq_gaps": 0}
        self._last_seq = None
        self._frame_counter = 0  # liczba pakietow chmury od startu

    # -- gniazdo ----------------------------------------------------------
    # Port 6201 moze zajac tylko jeden program. Ten, ktory go zajmie ("glowny"),
    # przekazuje surowe datagramy na 127.0.0.1:6202..6205; kolejne programy (np. viewer,
    # osc_bridge i TouchDesigner naraz) automatycznie zajmuja pierwszy wolny z tych portow.
    RELAY_PORTS = (6202, 6203, 6204, 6205)

    def open(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 * 1024 * 1024)
        self.is_relay_client = False
        self.relay_port = None
        try:
            s.bind(self.local_addr)
        except OSError as e:
            for rp in self.RELAY_PORTS:
                try:
                    s.bind(("127.0.0.1", rp))
                    self.is_relay_client = True
                    self.relay_port = rp
                    break
                except OSError:
                    continue
            if not self.is_relay_client:
                s.close()
                raise OSError(
                    f"Nie moge zbindowac {self.local_addr[0]}:{self.local_addr[1]} ({e}). "
                    "Czy karta ma IP 192.168.1.2? Czy port nie jest zajety przez inny program "
                    "(max 5 programow naraz: glowny + 4 przez przekaznik 6202-6205)?") from e
        s.settimeout(0.5)
        self.sock = s
        self._relay = None if self.is_relay_client else socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        return self

    def close(self):
        self.stop()
        if self.sock:
            self.sock.close()
            self.sock = None
        if getattr(self, "_relay", None):
            self._relay.close()
            self._relay = None

    def send(self, packet: bytes):
        if self.sock is None:
            self.open()
        if self.is_relay_client:
            raise RuntimeError("Port 6201 zajmuje inny program - zamknij go, zeby wysylac komendy do lidaru")
        self.sock.sendto(packet, self.lidar_addr)

    # -- komendy (protokol) -----------------------------------------------
    def start_rotation(self):
        self.send(user_cmd_packet(USER_CMD_STANDBY_TYPE, 0))

    def stop_rotation(self):
        self.send(user_cmd_packet(USER_CMD_STANDBY_TYPE, 1))

    def reset_lidar(self):
        self.send(user_cmd_packet(USER_CMD_RESET_TYPE, 1))

    def request_version(self):
        self.send(user_cmd_packet(USER_CMD_VERSION_GET, 0))

    def request_latency(self):
        self.send(user_cmd_packet(USER_CMD_LATENCY_TYPE, 0))

    def request_config(self):
        """Lidar odpowiada pakietem WORK_MODE (107) z aktualnym trybem pracy."""
        self.send(user_cmd_packet(USER_CMD_CONFIG_GET, 0))

    def set_work_mode(self, mode: int):
        """UWAGA: zapisuje konfiguracje w lidarze (bity: 0 FOV, 1 2D, 2 IMU off, 3 serial, 4 bez autostartu)."""
        self.send(work_mode_packet(mode))

    # -- parsowanie -------------------------------------------------------
    def handle(self, datagram: bytes):
        """Jeden datagram UDP moze zawierac kilka sklejonych pakietow (np. VERSION + ACK)."""
        ptype = None
        off = 0
        while off + HEADER_SIZE + TAIL_SIZE <= len(datagram):
            if datagram[off:off + 4] != FRAME_HEADER:
                nxt = datagram.find(FRAME_HEADER, off + 1)
                self.stats["bad_packets"] += 1
                if nxt < 0:
                    break
                off = nxt
                continue
            (psize,) = struct.unpack_from("<I", datagram, off + 8)
            if psize < HEADER_SIZE + TAIL_SIZE or off + psize > len(datagram):
                self.stats["bad_packets"] += 1
                break
            ptype = self._handle_packet(datagram[off:off + psize])
            off += psize
        return ptype

    def _handle_packet(self, buf: bytes):
        self.stats["packets"] += 1
        try:
            ptype, data = check_packet(buf)
        except PacketError as e:
            if "CRC" in str(e):
                self.stats["crc_errors"] += 1
            else:
                self.stats["bad_packets"] += 1
            return None

        if ptype == LIDAR_POINT_DATA_PACKET_TYPE:
            xyz, inten, _, hdr = decode_points(data, self.range_min, self.range_max)
            self.stats["points_pkts"] += 1
            seq = int(hdr["seq"])
            if self._last_seq is not None and seq != (self._last_seq + 1) & 0xFFFFFFFF:
                self.stats["seq_gaps"] += 1
            self._last_seq = seq
            self.last_point_header = hdr
            with self._lock:
                self._buf.append((xyz, inten))
                self._frame_counter += 1
            for cb in self.points_callbacks:
                cb(xyz, inten)
        elif ptype == LIDAR_2D_POINT_DATA_PACKET_TYPE:
            xyz, inten, hdr = decode_points_2d(data, self.range_min, self.range_max)
            self.stats["points_pkts"] += 1
            with self._lock:
                self._buf.append((xyz, inten))
                self._frame_counter += 1
            for cb in self.points_callbacks:
                cb(xyz, inten)
        elif ptype == LIDAR_IMU_DATA_PACKET_TYPE:
            self.last_imu = decode_imu(data)
            self.stats["imu_pkts"] += 1
            for cb in self.imu_callbacks:
                cb(self.last_imu)
        elif ptype == LIDAR_ACK_DATA_PACKET_TYPE:
            pt, ct, cv, st = struct.unpack_from("<IIII", data)
            self.last_ack = {"packet_type": pt, "cmd_type": ct, "cmd_value": cv,
                             "status": ACK_STATUS.get(st, st)}
        elif ptype == LIDAR_VERSION_PACKET_TYPE:
            self.version = decode_version(data)
        elif ptype == LIDAR_WORK_MODE_CONFIG_PACKET_TYPE:
            (self.work_mode,) = struct.unpack_from("<I", data)
        else:
            self.stats["other"][ptype] = self.stats["other"].get(ptype, 0) + 1
        return ptype

    def recv_once(self):
        try:
            buf, addr = self.sock.recvfrom(65535)
        except socket.timeout:
            return None
        except ConnectionResetError:
            return None  # Windows: ICMP "port unreachable" po wyslaniu na nieaktywny port
        if self._relay is not None:
            for rp in self.RELAY_PORTS:
                try:
                    self._relay.sendto(buf, ("127.0.0.1", rp))
                except OSError:
                    pass
        return self.handle(buf)

    # -- watek ------------------------------------------------------------
    def _loop(self):
        while self._running:
            try:
                self.recv_once()
            except OSError:
                if self._running:
                    time.sleep(0.1)
            except Exception as e:  # np. blad w callbacku - nie zabijamy watku
                self.stats["callback_errors"] = self.stats.get("callback_errors", 0) + 1
                self.last_error = repr(e)

    def start(self):
        if self.sock is None:
            self.open()
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="L2Receiver")
        self._thread.start()
        return self

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None

    # -- dostep do danych -------------------------------------------------
    @property
    def frame_count(self):
        return self._frame_counter // self.packets_per_frame

    def get_points(self, packets: int | None = None, frames: int | None = None):
        """Zwraca (xyz Nx3, intensity N) z ostatnich `packets` pakietow albo `frames` klatek."""
        if frames is not None:
            packets = frames * self.packets_per_frame
        with self._lock:
            items = list(self._buf)[-packets:] if packets else list(self._buf)
        if not items:
            return np.zeros((0, 3), np.float32), np.zeros((0,), np.float32)
        return (np.concatenate([i[0] for i in items]),
                np.concatenate([i[1] for i in items]))


# --------------------------------------------------------------------------
# Test z linii polecen
# --------------------------------------------------------------------------
def _main():
    ap = argparse.ArgumentParser(description="Test odbioru Unitree L2")
    ap.add_argument("--seconds", type=float, default=5.0)
    ap.add_argument("--local-ip", default="192.168.1.2")
    ap.add_argument("--local-port", type=int, default=6201)
    ap.add_argument("--lidar-ip", default="192.168.1.62")
    ap.add_argument("--lidar-port", type=int, default=6101)
    ap.add_argument("--cmd", choices=["version", "config", "start", "stop", "reset", "latency"],
                    help="wyslij komende do lidaru przed odbiorem")
    ap.add_argument("--set-work-mode", type=int, metavar="N",
                    help="ZAPISZ tryb pracy w lidarze (bity: 0 FOV192, 1 2D, 2 IMU off, 3 serial, "
                         "4 bez autostartu). Fabrycznie 0. Dziala po resecie/restarcie lidaru.")
    ap.add_argument("--enable-imu", action="store_true",
                    help="wlacz IMU (kasuje tylko bit 2 trybu pracy, reszte zostawia) i zresetuj lidar")
    args = ap.parse_args()

    rx = L2Receiver(args.lidar_ip, args.lidar_port, args.local_ip, args.local_port).open()
    if rx.is_relay_client:
        print("UWAGA: port 6201 zajmuje inny program (podglad / OSC). Zamknij go, zeby wysylac komendy.")
        if args.cmd or args.set_work_mode is not None or args.enable_imu:
            rx.close()
            sys.exit(1)

    if args.enable_imu:
        t0 = time.time()
        while rx.work_mode is None and time.time() - t0 < 3:
            rx.request_config()
            t1 = time.time()
            while rx.work_mode is None and time.time() - t1 < 0.5:
                rx.recv_once()
        if rx.work_mode is None:
            print("Lidar nie odpowiedzial na zapytanie o tryb pracy - czy jest podlaczony i wlaczony?")
            rx.close()
            sys.exit(1)
        print(f"Obecny tryb pracy: {rx.work_mode} -> {describe_work_mode(rx.work_mode)}")
        if not rx.work_mode & 4:
            print("IMU juz jest wlaczone - nic nie zmieniam.")
            rx.close()
            return
        new_mode = rx.work_mode & ~4
        rx.set_work_mode(new_mode)
        time.sleep(0.3)
        rx.reset_lidar()
        print(f"Ustawiono tryb {new_mode} -> {describe_work_mode(new_mode)}. Lidar sie restartuje (~20 s).")
        rx.close()
        return

    if args.set_work_mode is not None:
        if args.set_work_mode & 8:
            ap.error("tryb z bitem 3 (serial) odcialby lidar od Ethernetu - odmawiam")
        rx.set_work_mode(args.set_work_mode)
        print(f"Wyslano tryb pracy {args.set_work_mode}: {describe_work_mode(args.set_work_mode)}")
        time.sleep(0.3)
    if args.cmd:
        {"version": rx.request_version, "config": rx.request_config, "start": rx.start_rotation,
         "stop": rx.stop_rotation, "reset": rx.reset_lidar, "latency": rx.request_latency}[args.cmd]()
        print(f"Wyslano komende: {args.cmd}")

    first = None
    t0 = time.time()
    while time.time() - t0 < args.seconds:
        pt = rx.recv_once()
        if pt == LIDAR_POINT_DATA_PACKET_TYPE and first is None:
            first = rx.get_points(packets=1)
    dt = time.time() - t0
    s = rx.stats
    print(f"\nW {dt:.1f} s: pakietow {s['packets']}, chmura {s['points_pkts']} "
          f"({s['points_pkts'] / dt:.0f}/s), IMU {s['imu_pkts']}, bledy CRC {s['crc_errors']}, "
          f"inne bledne {s['bad_packets']}, luki seq {s['seq_gaps']}, inne typy {s['other']}")
    if rx.last_ack:
        print("ACK:", rx.last_ack)
    if rx.version:
        print("Wersja:", rx.version)
    if rx.work_mode is not None:
        print(f"Tryb pracy: {rx.work_mode} -> {describe_work_mode(rx.work_mode)}")

    h = rx.last_point_header
    if h is not None:
        print("\nStan lidaru (ostatni pakiet):")
        for k in ("sys_rotation_period", "com_rotation_period", "dirty_index", "apd_temperature",
                  "imu_temperature", "scan_period", "range_min", "range_max", "point_num",
                  "range_scale", "range_bias", "a_axis_dist", "b_axis_dist", "beta_angle", "xi_angle"):
            print(f"  {k:22s} = {h[k]}")

        xyz, inten = rx.get_points(frames=1)
        print(f"\nPunktow w klatce ({rx.packets_per_frame} pakietow): {len(xyz)}")
        print("10 pierwszych punktow (x, y, z [m], intensywnosc):")
        for p, i in zip(xyz[:10], inten[:10]):
            print(f"  ({p[0]:8.3f}, {p[1]:8.3f}, {p[2]:8.3f})  I={i:.0f}")
        all_xyz, all_i = rx.get_points()
        r = np.linalg.norm(all_xyz, axis=1)
        print(f"\nWszystkie {len(all_xyz)} punktow: odleglosc min {r.min():.2f} / mediana "
              f"{np.median(r):.2f} / max {r.max():.2f} m")
        for ax, name in enumerate("xyz"):
            print(f"  {name}: {np.percentile(all_xyz[:, ax], 2):7.2f} .. {np.percentile(all_xyz[:, ax], 98):7.2f} m (2-98 percentyl)")
    if rx.last_imu:
        im = rx.last_imu
        print(f"\nIMU: q={np.round(im.quaternion, 4)}, rpy={np.round(im.euler_deg(), 1)} deg, "
              f"acc={np.round(im.linear_acceleration, 3)}, gyro={np.round(im.angular_velocity, 4)}")
    rx.close()


if __name__ == "__main__":
    _main()
