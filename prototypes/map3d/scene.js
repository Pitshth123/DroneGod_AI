// scene.js — ภูมิประเทศ 3D + ภาพดาวเทียม + หมุดโดรน (ใช้ร่วมกันทั้ง probe และ live)
//
// three r170 = ตัวใหม่สุดที่ QtWebEngine 5.15 (Chromium 83) รันได้ — ดู README
// ห้ามใช้ dynamic import() / import maps / top-level await ในไฟล์นี้เด็ดขาด
import * as THREE from "/vendor/three/three-0.170.0.js";
import { OrbitControls } from "/vendor/three/OrbitControls-170.js";

export { THREE };

const NODATA = -32768;
const DEG = Math.PI / 180;

// ══════════════════════════════════════════════════════════════
//  geodesy — ย่อจาก SIAHRA apps/web/src/scene/projection.ts (MIT)
//  ต้องมีทั้งสองทาง: ขาไปวางหมุดโดรน (lon/lat -> ฉาก) ขากลับแปะภาพ (ฉาก -> tile)
// ══════════════════════════════════════════════════════════════
const EA = 6378137, EF = 1 / 298.257223563, K0 = 0.9996;
const NF = EF / (2 - EF), NF2 = NF * NF, NF3 = NF2 * NF, NF4 = NF3 * NF;
const AA = (EA / (1 + NF)) * (1 + NF2 / 4 + NF4 / 64);
const ALPHA = [
  NF / 2 - (2 / 3) * NF2 + (5 / 16) * NF3 + (41 / 180) * NF4,
  (13 / 48) * NF2 - (3 / 5) * NF3 + (557 / 1440) * NF4,
  (61 / 240) * NF3 - (103 / 140) * NF4,
  (49561 / 161280) * NF4,
];
const BETA = [
  NF / 2 - (2 / 3) * NF2 + (37 / 96) * NF3 - (1 / 360) * NF4,
  (1 / 48) * NF2 + (1 / 15) * NF3 - (437 / 1440) * NF4,
  (17 / 480) * NF3 - (37 / 840) * NF4,
  (4397 / 161280) * NF4,
];
const FALSE_E = 500000;
const cm = (zone) => zone * 6 - 183;

export function wgs84ToUtm(lon, lat, zone) {
  const phi = lat * DEG, lam = (lon - cm(zone)) * DEG, sp = Math.sin(phi);
  const t = Math.sinh(Math.atanh(sp) -
    ((2 * Math.sqrt(NF)) / (1 + NF)) * Math.atanh(((2 * Math.sqrt(NF)) / (1 + NF)) * sp));
  const xi0 = Math.atan2(t, Math.cos(lam));
  const eta0 = Math.atanh(Math.sin(lam) / Math.sqrt(1 + t * t));
  let xi = xi0, eta = eta0;
  for (let j = 1; j <= 4; j++) {
    xi += ALPHA[j - 1] * Math.sin(2 * j * xi0) * Math.cosh(2 * j * eta0);
    eta += ALPHA[j - 1] * Math.cos(2 * j * xi0) * Math.sinh(2 * j * eta0);
  }
  return [FALSE_E + K0 * AA * eta, K0 * AA * xi];
}

export function utmToWgs84(easting, northing, zone) {
  const xi = northing / (K0 * AA), eta = (easting - FALSE_E) / (K0 * AA);
  let xiP = xi, etaP = eta;
  for (let j = 1; j <= 4; j++) {
    xiP -= BETA[j - 1] * Math.sin(2 * j * xi) * Math.cosh(2 * j * eta);
    etaP -= BETA[j - 1] * Math.cos(2 * j * xi) * Math.sinh(2 * j * eta);
  }
  const chi = Math.asin(Math.sin(xiP) / Math.cosh(etaP));
  const D1 = 2 * NF - (2 / 3) * NF2 - 2 * NF3;
  const D2 = (7 / 3) * NF2 - (8 / 5) * NF3, D3 = (56 / 15) * NF3;
  const phi = chi + D1 * Math.sin(2 * chi) + D2 * Math.sin(4 * chi) + D3 * Math.sin(6 * chi);
  return [cm(zone) + Math.atan2(Math.sinh(etaP), Math.cos(xiP)) / DEG, phi / DEG];
}

export function lonLatToTile(lon, lat, z) {
  const n = 2 ** z, la = Math.max(-85.05112878, Math.min(85.05112878, lat)) * DEG;
  return [((lon + 180) / 360) * n,
          ((1 - Math.log(Math.tan(la) + 1 / Math.cos(la)) / Math.PI) / 2) * n];
}

// สีประจำลำ — ให้ตรงกับ drone_color() ของ cockpit (theme.py)
const DRONE_COLORS = [0x38e08b, 0x38bdf8, 0xeab308, 0xf97316, 0xa78bfa, 0xf472b6];
export const droneColor = (id) => DRONE_COLORS[(id - 1) % DRONE_COLORS.length];

// ══════════════════════════════════════════════════════════════
//  createTerrainScene — โหลด AOI แล้วคืน handle แบบ imperative
//  (เลียนแบบ setupScene.ts ของ SIAHRA เพราะเรียกจาก Python ผ่าน
//   runJavaScript ได้ตรง ๆ — เข้ากับ pattern MapBridge เดิมของ cockpit)
// ══════════════════════════════════════════════════════════════
export async function createTerrainScene(opts) {
  const { mount, aoi = "30", tilesUrl = null, zoom = 11 } = opts;
  const say = opts.onStatus || function () {};
  const stats = {};

  // ── manifest + terrain.bin ──────────────────────────────────
  const manifest = await (await fetch(`/aoi/${aoi}/manifest.json`)).json();
  const T = manifest.terrain, B = manifest.bbox;
  const W = T.width, H = T.height, CELL = T.cellSizeM;
  const ZONE = Number(String(manifest.utmZone).slice(-2));
  const NORTH_EDGE = manifest.originNorthing + H * CELL;
  say("manifest", `${manifest.provinceNameTh} (aoi ${manifest.aoiId})`, "ok");

  const t0 = performance.now();
  const buf = await (await fetch(T.url)).arrayBuffer();
  const raw = new Int16Array(buf);       // Int16 ดิบ row-major, row 0 = ขอบเหนือ
  if (raw.length !== W * H) {
    throw new Error(`terrain.bin ขนาดไม่ตรง: ได้ ${raw.length} ต้องการ ${W * H}`);
  }
  say("terrain.bin", `${(buf.byteLength / 1048576).toFixed(2)} MB · ${W}x${H} @ ` +
      `${CELL}m · ${(performance.now() - t0).toFixed(0)}ms`, "ok");

  const heights = new Float32Array(raw.length);
  let mn = Infinity, mx = -Infinity;
  for (let i = 0; i < raw.length; i++) {
    const v = raw[i] === NODATA ? T.minZ : raw[i];
    heights[i] = v;
    if (v < mn) mn = v;
    if (v > mx) mx = v;
  }
  stats.minZ = mn; stats.maxZ = mx;

  // ── พิกัด: เซลล์ (r,c) มีจุดศูนย์กลางที่ origin + (c+0.5)·cell (pixel-is-area) ──
  const cellUtm = (r, c) => [manifest.originEasting + (c + 0.5) * CELL,
                             NORTH_EDGE - (r + 0.5) * CELL];
  const cellLonLat = (r, c) => utmToWgs84(...cellUtm(r, c), ZONE);

  // local metres: +X ตะวันออก, -Z เหนือ, จุดกำเนิด = กลาง raster
  function utmToLocal(e, n) {
    return [e - (manifest.originEasting + W * CELL / 2),
            -(n - (manifest.originNorthing + H * CELL / 2))];
  }
  function lonLatToLocal(lon, lat) {
    return utmToLocal(...wgs84ToUtm(lon, lat, ZONE));
  }

  /** ความสูงพื้น (m) ที่พิกัดฉาก — bilinear */
  function sampleHeight(x, z) {
    const fc = (x + W * CELL / 2) / CELL - 0.5;
    const fr = (z + H * CELL / 2) / CELL - 0.5;
    const c = Math.min(W - 2, Math.max(0, Math.floor(fc)));
    const r = Math.min(H - 2, Math.max(0, Math.floor(fr)));
    const tx = Math.min(1, Math.max(0, fc - c)), tz = Math.min(1, Math.max(0, fr - r));
    const h00 = heights[r * W + c], h10 = heights[r * W + c + 1];
    const h01 = heights[(r + 1) * W + c], h11 = heights[(r + 1) * W + c + 1];
    return (h00 * (1 - tx) + h10 * tx) * (1 - tz) + (h01 * (1 - tx) + h11 * tx) * tz;
  }

  // ตรวจการอ้างอิงพิกัด: bbox จังหวัดต้องอยู่ใน raster ครบ (ขอบเผื่อ >= 0)
  // raster กว้างกว่า bbox เสมอ และสี่เหลี่ยม UTM ไม่ทับสี่เหลี่ยม lon/lat — ปกติ
  {
    const cs = [cellLonLat(0, 0), cellLonLat(0, W - 1),
                cellLonLat(H - 1, 0), cellLonLat(H - 1, W - 1)];
    const lons = cs.map((p) => p[0]), lats = cs.map((p) => p[1]);
    const kmLon = 111.32 * Math.cos(B.minLat * DEG);
    const m = [(B.minLon - Math.min(...lons)) * kmLon,
               (Math.max(...lons) - B.maxLon) * kmLon,
               (Math.max(...lats) - B.maxLat) * 111.32,
               (B.minLat - Math.min(...lats)) * 111.32];
    say("ตรวจพิกัด UTM", `bbox อยู่ใน raster · ขอบเผื่อ ` +
        `W${m[0].toFixed(1)} E${m[1].toFixed(1)} N${m[2].toFixed(1)} S${m[3].toFixed(1)} km`,
        Math.min(...m) >= 0 ? "ok" : "bad");
  }

  // ── three.js ────────────────────────────────────────────────
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x04090b);
  const camera = new THREE.PerspectiveCamera(
    55, mount.clientWidth / mount.clientHeight, 5, CELL * Math.max(W, H) * 4);
  // preserveDrawingBuffer: ให้ toDataURL() อ่านภาพได้ — จำเป็นเพราะ QWidget.grab()
  // จับ layer ที่ GPU composite ไม่ได้ (ได้ภาพดำ) ต้องดึงจากใน WebGL เองแทน
  const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.setSize(mount.clientWidth, mount.clientHeight);
  mount.appendChild(renderer.domElement);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;

  const t1 = performance.now();
  const geo = new THREE.PlaneGeometry((W - 1) * CELL, (H - 1) * CELL, W - 1, H - 1);
  geo.rotateX(-Math.PI / 2);
  const pos = geo.attributes.position;
  for (let i = 0; i < heights.length; i++) pos.setY(i, heights[i]);
  geo.computeVertexNormals();
  say("mesh", `${(pos.count / 1000).toFixed(0)}k verts · ` +
      `${((W - 1) * (H - 1) * 2 / 1000).toFixed(0)}k tris · ` +
      `${(performance.now() - t1).toFixed(0)}ms`, "ok");

  // ── ภาพดาวเทียมจาก tile proxy ของ cockpit ───────────────────
  let tex = null;
  if (tilesUrl) {
    try {
      const t2 = performance.now();
      const [ax, ay] = lonLatToTile(B.minLon, B.maxLat, zoom);
      const [bx, by] = lonLatToTile(B.maxLon, B.minLat, zoom);
      const x0 = Math.floor(ax), y0 = Math.floor(ay);
      const nx = Math.floor(bx) - x0 + 1, ny = Math.floor(by) - y0 + 1;
      const cv = document.createElement("canvas");
      cv.width = nx * 256; cv.height = ny * 256;
      const ctx = cv.getContext("2d");
      ctx.fillStyle = "#16221a"; ctx.fillRect(0, 0, cv.width, cv.height);
      let okN = 0;
      await Promise.all(Array.from({ length: nx * ny }, (_, i) => {
        const gx = i % nx, gy = (i / nx) | 0;
        return new Promise((res) => {
          const img = new Image();
          img.crossOrigin = "anonymous";
          img.onload = () => { ctx.drawImage(img, gx * 256, gy * 256); okN++; res(); };
          img.onerror = () => res();     // offline → tile เทาจาก cache, ไม่ค้าง
          img.src = `${tilesUrl}/tile/sat/${zoom}/${x0 + gx}/${y0 + gy}.png`;
        });
      }));
      // UV ต่อ vertex: UTM -> lon/lat -> Web Mercator (ห้ามยืดภาพให้พอดีกรอบ)
      const uv = new Float32Array(pos.count * 2);
      for (let r = 0; r < H; r++) {
        for (let c = 0; c < W; c++) {
          const [tx, ty] = lonLatToTile(...cellLonLat(r, c), zoom);
          const i = r * W + c;
          uv[i * 2] = (tx - x0) * 256 / cv.width;
          uv[i * 2 + 1] = 1 - (ty - y0) * 256 / cv.height;
        }
      }
      geo.setAttribute("uv", new THREE.BufferAttribute(uv, 2));
      tex = new THREE.CanvasTexture(cv);
      tex.colorSpace = THREE.SRGBColorSpace;
      tex.anisotropy = renderer.capabilities.getMaxAnisotropy();
      say("ภาพดาวเทียม", `z${zoom} · ${okN}/${nx * ny} tiles · ` +
          `${cv.width}x${cv.height}px · ${(performance.now() - t2).toFixed(0)}ms`,
          okN ? "ok" : "warn");
    } catch (err) {
      say("ภาพดาวเทียม", "ล้มเหลว: " + err.message, "bad");
    }
  }

  if (!tex) {   // ไม่มีภาพ → ไล่สีตามความสูง ยังอ่านภูมิประเทศได้
    const col = new Float32Array(pos.count * 3);
    const lo = new THREE.Color(0x1d3f2b), mid = new THREE.Color(0x6b7a3a),
          hi = new THREE.Color(0xd8d2c0), tmp = new THREE.Color();
    for (let i = 0; i < pos.count; i++) {
      const t = Math.min(1, Math.max(0, (heights[i] - mn) / Math.max(1, mx - mn)));
      tmp.copy(t < 0.5 ? lo.clone().lerp(mid, t * 2) : mid.clone().lerp(hi, (t - 0.5) * 2));
      col[i * 3] = tmp.r; col[i * 3 + 1] = tmp.g; col[i * 3 + 2] = tmp.b;
    }
    geo.setAttribute("color", new THREE.BufferAttribute(col, 3));
  }

  scene.add(new THREE.Mesh(geo, tex
    ? new THREE.MeshLambertMaterial({ map: tex })
    : new THREE.MeshLambertMaterial({ vertexColors: true })));
  scene.add(new THREE.HemisphereLight(0xbfd4e8, 0x2a2a20, 1.15));
  const sun = new THREE.DirectionalLight(0xffffff, 1.5);
  sun.position.set(-1, 2, 1).multiplyScalar(CELL * W);
  scene.add(sun);

  // ── หมุดโดรน ────────────────────────────────────────────────
  const markers = new Map();            // id -> {group, body, stem, ring, label}

  function makeMarker(id) {
    const color = droneColor(id);
    const g = new THREE.Group();
    const body = new THREE.Mesh(new THREE.SphereGeometry(120, 16, 12),
                                new THREE.MeshBasicMaterial({ color }));
    // เส้นดิ่งลงพื้น + วงแหวนที่พื้น — อ่านตำแหน่งจริงได้ ไม่ต้องเดาจากมุมกล้อง
    const stem = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()]),
      new THREE.LineBasicMaterial({ color }));
    const ring = new THREE.Mesh(new THREE.RingGeometry(400, 520, 40),
      new THREE.MeshBasicMaterial({ color, side: THREE.DoubleSide,
                                    transparent: true, opacity: 0.7 }));
    ring.rotation.x = -Math.PI / 2;
    // ลูกศรบอกทิศหัวโดรน
    const head = new THREE.Mesh(new THREE.ConeGeometry(90, 260, 12),
                                new THREE.MeshBasicMaterial({ color }));
    head.rotation.x = Math.PI / 2;
    g.add(body, stem, ring, head);
    scene.add(g);
    return { group: g, body, stem, ring, head };
  }

  /**
   * อัปเดตหมุดจาก telemetry — เรียกจาก Python ผ่าน runJavaScript
   * list = [{id, lat, lon, alt, hdg, armed}]  (alt = AGL เป็นเมตร)
   */
  function setDrones(list) {
    const seen = new Set();
    for (const d of list || []) {
      const id = Number(d.id);
      if (!id || !isFinite(d.lat) || !isFinite(d.lon)) continue;
      if (d.lat === 0 && d.lon === 0) continue;      // ยังไม่มี GPS fix
      seen.add(id);
      let m = markers.get(id);
      if (!m) { m = makeMarker(id); markers.set(id, m); }
      const [x, z] = lonLatToLocal(d.lon, d.lat);
      const ground = sampleHeight(x, z);
      const y = ground + Math.max(0, Number(d.alt) || 0);
      m.body.position.set(x, y, z);
      m.ring.position.set(x, ground + 20, z);
      m.head.position.set(x, y, z);
      m.head.rotation.z = -(Number(d.hdg) || 0) * DEG;
      m.stem.geometry.setFromPoints([
        new THREE.Vector3(x, ground, z), new THREE.Vector3(x, y, z)]);
      m.body.material.opacity = d.armed ? 1 : 0.55;
      m.body.material.transparent = !d.armed;
    }
    // ลำที่หายไปจาก snapshot = หลุดการเชื่อมต่อ → เอาหมุดออก ไม่ปล่อยค้างให้เข้าใจผิด
    for (const [id, m] of markers) {
      if (!seen.has(id)) { scene.remove(m.group); markers.delete(id); }
    }
    return markers.size;
  }

  /** เล็งกล้องไปที่พิกัดหนึ่ง (dist = ระยะกล้อง เป็นเมตร) */
  function focusLonLat(lon, lat, dist = 14000) {
    const [x, z] = lonLatToLocal(lon, lat);
    const ground = sampleHeight(x, z);
    camera.position.set(x + dist * 0.55, ground + dist * 0.42, z + dist * 0.75);
    controls.target.set(x, ground, z);
    controls.update();
  }

  /** วางกล้องให้เห็นทั้ง AOI — ต้องเรียกตอนสร้าง ไม่งั้นกล้องค้างที่ (0,0,0)
   *  ซึ่งอยู่ "ใน" ผืนภูมิประเทศพอดี → จอดำสนิททั้งที่ mesh สร้างสำเร็จ */
  function frameAll() {
    const extent = Math.max(W, H) * CELL;
    camera.position.set(extent * 0.35, mx + extent * 0.5, extent * 0.6);
    controls.target.set(0, mn, 0);
    controls.update();
  }
  frameAll();

  // ── render loop + FPS ───────────────────────────────────────
  let frames = 0, last = performance.now();
  stats.fps = 0;
  function loop() {
    requestAnimationFrame(loop);
    controls.update();
    renderer.render(scene, camera);
    frames++;
    const now = performance.now();
    if (now - last >= 1000) {
      stats.fps = frames * 1000 / (now - last);
      frames = 0; last = now;
      if (opts.onFps) opts.onFps(stats.fps);
    }
  }
  addEventListener("resize", () => {
    camera.aspect = mount.clientWidth / mount.clientHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(mount.clientWidth, mount.clientHeight);
  });
  loop();

  /** ภาพหน้าจอของฉาก 3D เป็น data URL (วาดสดก่อนอ่านเสมอ)
   *  ค่าเริ่มต้นเป็น JPEG เพราะ PNG ของจอใหญ่โตหลาย MB แล้วส่งข้ามสะพาน
   *  JS -> Python ไม่รอด (runJavaScript คืนค่าว่าง) */
  function capture(type = "image/jpeg", quality = 0.92) {
    renderer.render(scene, camera);
    return renderer.domElement.toDataURL(type, quality);
  }

  return { scene, camera, renderer, controls, manifest, stats,
           sampleHeight, lonLatToLocal, cellLonLat, setDrones, focusLonLat,
           frameAll, capture, droneCount: () => markers.size };
}
