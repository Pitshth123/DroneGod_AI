"""
tile_cache.py — local HTTP tile proxy + cache (สำหรับใช้งาน offline ในสนาม)
- เสิร์ฟ map.html + leaflet lib จาก assets/
- /tile/{layer}/{z}/{x}/{y}.png:
    มีใน cache → เสิร์ฟจากดิสก์ (offline ได้)
    ไม่มี + ออนไลน์ → ดาวน์โหลด + เซฟ cache + เสิร์ฟ
    ไม่มี + offline → เสิร์ฟ tile เทา (placeholder)
- prefetch(layer, bounds, zmin, zmax): ดาวน์โหลดพื้นที่ล่วงหน้า (ปุ่ม CACHE AREA)
"""
import math
import os
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np

# tile provider ต่อ layer ({z}/{x}/{y} หรือ {z}/{y}/{x} ตาม provider)
LAYERS = {
    # ภาพถ่าย / พื้นฐาน
    "sat":     "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    "labels":  "https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
    "streets": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
    "topo":    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
    "terrain": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Terrain_Base/MapServer/tile/{z}/{y}/{x}",
    "gray":    "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
    # ── ความสูงภูมิประเทศ (สำหรับแผนที่ 3D) ──
    # Terrarium PNG: ความสูง(m) = (R*256 + G + B/256) - 32768
    # เป็น XYZ tile เหมือน layer อื่นทุกอย่าง → cache/offline/prefetch ใช้ทางเดียวกันหมด
    # และตรงกรอบกับ tile ภาพดาวเทียมเป๊ะ (z/x/y เดียวกัน) จึงแปะภาพได้โดยไม่ต้องแปลงพิกัด
    "dem":     "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png",
    # OSM / Carto
    "road":    "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    "otm":     "https://tile.opentopomap.org/{z}/{x}/{y}.png",
    "dark":    "https://basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png",
    "light":   "https://basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png",
}
_UA = "SwarmGod/0.1 (offline GCS tile cache)"


def _gray_tile() -> bytes:
    img = np.full((256, 256, 3), 24, np.uint8)
    img[::64, :] = 40
    img[:, ::64] = 40
    return cv2.imencode(".png", img)[1].tobytes()


class _Handler(BaseHTTPRequestHandler):
    server_version = "SwarmGodTiles/0.1"

    def log_message(self, *a):  # เงียบ
        pass

    # โฟลเดอร์ย่อยใน assets/ ที่ยอมให้เสิร์ฟ (allowlist — ไม่เปิดทั้ง assets)
    _ASSET_DIRS = ("leaflet/", "three/", "map3d/")

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/" or path == "/map.html":
            return self._serve_asset("map.html", "text/html")
        if path == "/map3d.html":
            return self._serve_asset("map3d.html", "text/html")
        if any(path.startswith("/" + d) for d in self._ASSET_DIRS):
            return self._serve_asset(path.lstrip("/"), self._mime(path))
        if path.startswith("/tile/"):
            return self._serve_tile(path)
        self.send_error(404)

    # ── static assets ──
    def _serve_asset(self, rel, mime):
        root = os.path.abspath(self.server.assets_dir)
        full = os.path.abspath(os.path.join(root, rel.replace("/", os.sep)))
        # กัน path traversal — ".." ต้องพาออกนอก assets/ ไม่ได้
        if os.path.commonpath([root, full]) != root or not os.path.isfile(full):
            self.send_error(404)
            return
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ── tiles (cache-first) ──
    def _serve_tile(self, path):
        parts = path.strip("/").split("/")  # tile, layer, z, x, y.png
        if len(parts) != 5:
            self.send_error(400)
            return
        _, layer, z, x, yp = parts
        y = yp.replace(".png", "")
        data = self.server.get_tile(layer, z, x, y)
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    @staticmethod
    def _mime(path):
        if path.endswith(".css"):
            return "text/css"
        if path.endswith(".js"):
            return "application/javascript"
        if path.endswith(".png"):
            return "image/png"
        return "application/octet-stream"


class TileServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, assets_dir, cache_dir, host="127.0.0.1", port=0):
        super().__init__((host, port), _Handler)
        self.assets_dir = assets_dir
        self.cache_dir = cache_dir
        self._gray = _gray_tile()
        self._online = True
        os.makedirs(cache_dir, exist_ok=True)

    @property
    def base_url(self):
        h, p = self.server_address
        return f"http://{h}:{p}"

    def _cache_path(self, layer, z, x, y):
        d = os.path.join(self.cache_dir, layer, str(z), str(x))
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, f"{y}.png")

    def _download(self, layer, z, x, y) -> bytes | None:
        tmpl = LAYERS.get(layer, LAYERS["sat"])
        url = tmpl.format(z=z, x=x, y=y)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=6) as r:
                return r.read()
        except Exception:
            self._online = False
            return None

    def get_tile(self, layer, z, x, y) -> bytes:
        cp = self._cache_path(layer, z, x, y)
        if os.path.isfile(cp):
            with open(cp, "rb") as f:
                return f.read()
        data = self._download(layer, z, x, y)
        if data:
            self._online = True
            with open(cp, "wb") as f:
                f.write(data)
            return data
        return self._gray  # offline + ไม่มี cache

    # ── prefetch พื้นที่ล่วงหน้า (ปุ่ม CACHE AREA) ──
    def prefetch(self, layer, north, south, east, west, zmin, zmax, progress=None):
        total, done = 0, 0
        jobs = []
        for z in range(zmin, zmax + 1):
            x0, y0 = _deg2tile(north, west, z)
            x1, y1 = _deg2tile(south, east, z)
            for x in range(min(x0, x1), max(x0, x1) + 1):
                for y in range(min(y0, y1), max(y0, y1) + 1):
                    jobs.append((z, x, y))
        total = len(jobs)
        for (z, x, y) in jobs:
            self.get_tile(layer, z, x, y)
            done += 1
            if progress and done % 10 == 0:
                progress(done, total)
        if progress:
            progress(done, total)
        return total

    def start(self):
        t = threading.Thread(target=self.serve_forever, daemon=True)
        t.start()
        return self.base_url


def _deg2tile(lat, lon, z):
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    lat_r = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n)
    return x, y
