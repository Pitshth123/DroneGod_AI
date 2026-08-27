// terrain.js — ภูมิประเทศ 3D แบบสตรีมตามรัศมี (ไม่โหลดทั้งจังหวัด)
//
// หลักการเดียวกับแผนที่ 2D เดิมเป๊ะ: โหลดเป็น XYZ tile รอบจุดที่มองอยู่ แล้วค่อย ๆ
// เติมเมื่อขยับ — ต่างกันแค่แต่ละ tile มีความสูงด้วย ไม่ใช่ภาพแบน
//
//   ความสูง : /tile/dem/{z}/{x}/{y}.png   (Terrarium: (R*256+G+B/256)-32768 เมตร)
//   ภาพพื้น : /tile/sat/{z}/{x}/{y}.png   (Esri World Imagery — อันเดียวกับแผนที่เดิม)
//
// ทั้งสองเป็น tile กริดเดียวกัน (z/x/y ตรงกัน) ภาพจึงลงบนพื้นพอดีโดยไม่ต้อง
// แปลงพิกัดใด ๆ — UV ของแต่ละ tile คือ 0..1 ตรง ๆ
//
// ข้อจำกัดที่ต้องเขียนตาม: QtWebEngine 5.15 = Chromium 83
//   ห้ามใช้ dynamic import() / import maps / top-level await
import * as THREE from "/three/three.module.js";
import { OrbitControls } from "/three/OrbitControls.js";

export { THREE };

const DEG = Math.PI / 180;
const MERC = 20037508.342789244;          // ครึ่งเส้นรอบโลกใน Web Mercator (m)
const TILE_PX = 256;
const NODATA_FLAT = 0;

export function lonLatToMerc(lon, lat) {
  const x = (lon / 180) * MERC;
  const y = Math.log(Math.tan((90 + lat) * Math.PI / 360)) / (Math.PI / 180) / 180 * MERC;
  return [x, y];
}
export function mercToLonLat(x, y) {
  const lon = (x / MERC) * 180;
  let lat = (y / MERC) * 180;
  lat = 180 / Math.PI * (2 * Math.atan(Math.exp(lat * Math.PI / 180)) - Math.PI / 2);
  return [lon, lat];
}
export function lonLatToTile(lon, lat, z) {
  const n = 2 ** z;
  const la = Math.max(-85.05112878, Math.min(85.05112878, lat)) * DEG;
  return [((lon + 180) / 360) * n,
          ((1 - Math.log(Math.tan(la) + 1 / Math.cos(la)) / Math.PI) / 2) * n];
}

// สีประจำลำ — ให้ตรงกับ drone_color() ใน theme.py ของ cockpit
const DRONE_COLORS = [0x38e08b, 0x38bdf8, 0xeab308, 0xf97316, 0xa78bfa, 0xf472b6];
export const droneColor = (id) => DRONE_COLORS[((id - 1) % DRONE_COLORS.length + DRONE_COLORS.length) % DRONE_COLORS.length];

/**
 * createTerrain — คืน handle แบบ imperative ให้ Python เรียกผ่าน runJavaScript
 * (pattern เดียวกับที่ cockpit ใช้กับ Leaflet อยู่แล้ว — ดู _js() ใน app.py)
 *
 * opts.origin = {lat, lon} จุดกึ่งกลางฉาก (ปกติ = home/จุดปล่อย)
 * opts.zoom   = ระดับ tile (13 ≈ 19 m/px)
 * opts.radius = โหลดกี่ tile รอบจุดกลาง (2 = 5x5, 3 = 7x7)
 */
export function createTerrain(opts) {
  const mount = opts.mount;
  const say = opts.onStatus || function () {};
  const ZOOM = opts.zoom || 13;
  const RADIUS = opts.radius || 3;
  // 96 ช่อง/tile: DEM มา 256x256 ถ้าย่อเหลือ 48 จะทิ้งรายละเอียดไป ~5 เท่า
  // ภูมิประเทศเลยดูเรียบผิดจริง (frustum culling เหลือ ~16 tile จึงยังไหว)
  const SEG = opts.segments || 96;
  // ตัวคูณความสูง — พื้นที่ราบอย่างเมืองโคราชสูงต่างกันแค่ ~60 m ใน 8 km
  // ถ้าวาดตามจริง 1:1 จะดูแบนจนอ่านภูมิประเทศไม่ออก
  // **ป้ายบอกตัวคูณต้องโชว์ตลอด** เพราะนี่คือภาพเกินจริง ห้ามเอาไปวัดระยะ
  let exag = opts.exaggeration || 2.0;
  // ภาพพื้นละเอียดกว่าความสูงกี่ระดับ (1 = คมขึ้น 2 เท่า, 2 = 4 เท่าแต่กิน VRAM 4 เท่า)
  const IMG_UP = opts.imageryUp === undefined ? 1 : Math.max(0, Math.min(2, opts.imageryUp));

  let origin = opts.origin || { lat: 14.9581695, lon: 102.0986187 };
  let [oMx, oMy] = lonLatToMerc(origin.lon, origin.lat);
  // Web Mercator ยืดตามละติจูด — คูณ cos(lat) ให้ระยะในฉากเป็นเมตรจริง
  // (ไม่งั้นที่ละติจูด 15 ระยะจะเพี้ยนไป ~3.5% และความสูงจะดูแบนผิดส่วน)
  const SCALE = Math.cos(origin.lat * DEG);

  const mercToLocal = (mx, my) => [(mx - oMx) * SCALE, -(my - oMy) * SCALE];
  function lonLatToLocal(lon, lat) {
    return mercToLocal(...lonLatToMerc(lon, lat));
  }
  function localToLonLat(x, z) {
    return mercToLonLat(x / SCALE + oMx, -z / SCALE + oMy);
  }

  // ── ฉาก ──────────────────────────────────────────────────────
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x04090b);
  const camera = new THREE.PerspectiveCamera(
    55, Math.max(1, mount.clientWidth) / Math.max(1, mount.clientHeight), 2, 400000);
  const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.setSize(mount.clientWidth, mount.clientHeight);
  mount.appendChild(renderer.domElement);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.maxPolarAngle = Math.PI * 0.495;   // กันกล้องมุดลงใต้พื้น

  // แสงเฉียงแรง + ฟ้าอ่อน = เห็นสันเขา/ร่องน้ำชัด (แสงนุ่มเกินไปภูมิประเทศจะแบน)
  scene.add(new THREE.HemisphereLight(0xbfd4e8, 0x33301f, 0.72));
  const sun = new THREE.DirectionalLight(0xfff2dc, 1.75);
  sun.position.set(-1, 1.15, 0.75).multiplyScalar(50000);   // ต่ำ ๆ ให้เงาทอดยาว
  scene.add(sun);

  // ทุกอย่างที่ต้องยืดตามตัวคูณความสูงอยู่ในกลุ่มนี้
  const terrainGroup = new THREE.Group();
  terrainGroup.scale.y = exag;
  scene.add(terrainGroup);

  // ── tile ภูมิประเทศ ──────────────────────────────────────────
  const tiles = new Map();           // "z/x/y" -> {mesh, state}
  const stats = { loaded: 0, pending: 0, failed: 0, fps: 0 };
  let heightLookup = [];             // tile ที่โหลดแล้ว ใช้หาความสูงพื้น

  function tileMercBounds(z, x, y) {
    const span = (2 * MERC) / 2 ** z;
    return { x0: -MERC + x * span, y0: MERC - y * span, span };
  }

  function decodeTerrarium(img) {
    const cv = document.createElement("canvas");
    cv.width = TILE_PX; cv.height = TILE_PX;
    const ctx = cv.getContext("2d", { willReadFrequently: true });
    ctx.drawImage(img, 0, 0);
    const d = ctx.getImageData(0, 0, TILE_PX, TILE_PX).data;
    const h = new Float32Array(TILE_PX * TILE_PX);
    for (let i = 0; i < h.length; i++) {
      h[i] = (d[i * 4] * 256 + d[i * 4 + 1] + d[i * 4 + 2] / 256) - 32768;
    }
    return h;
  }

  function loadImage(url) {
    return new Promise((res, rej) => {
      const img = new Image();
      img.crossOrigin = "anonymous";
      img.onload = () => res(img);
      img.onerror = () => rej(new Error("tile load failed"));
      img.src = url;
    });
  }

  /**
   * ภาพพื้นของ tile ภูมิประเทศ 1 ใบ — ต่อภาพ zoom ที่ละเอียดกว่ามาเป็นผืนเดียว
   * IMG_UP=0 คือใช้ tile ตรง ๆ (256px), 1 = 2x2 (512px), 2 = 4x4 (1024px)
   */
  async function loadImageryFor(z, x, y) {
    const up = IMG_UP;
    if (up <= 0) {
      const img = await loadImage(`/tile/sat/${z}/${x}/${y}.png`);
      const t = new THREE.Texture(img);
      t.colorSpace = THREE.SRGBColorSpace;
      t.anisotropy = renderer.capabilities.getMaxAnisotropy();
      t.needsUpdate = true;
      return t;
    }
    const n = 1 << up, iz = z + up, ix0 = x * n, iy0 = y * n;
    const cv = document.createElement("canvas");
    cv.width = cv.height = 256 * n;
    const ctx = cv.getContext("2d");
    ctx.fillStyle = "#5a6b46";
    ctx.fillRect(0, 0, cv.width, cv.height);
    let got = 0;
    await Promise.all(Array.from({ length: n * n }, (_, i) => {
      const gx = i % n, gy = (i / n) | 0;
      return loadImage(`/tile/sat/${iz}/${ix0 + gx}/${iy0 + gy}.png`)
        .then((im) => { ctx.drawImage(im, gx * 256, gy * 256); got++; })
        .catch(() => {});
    }));
    if (!got) return null;               // ออฟไลน์ล้วน → คงสีพื้นไว้
    const t = new THREE.CanvasTexture(cv);
    t.colorSpace = THREE.SRGBColorSpace;
    t.anisotropy = renderer.capabilities.getMaxAnisotropy();
    return t;
  }

  async function buildTile(z, x, y) {
    const key = `${z}/${x}/${y}`;
    if (tiles.has(key)) return;
    tiles.set(key, { state: "pending", mesh: null });
    stats.pending++;
    try {
      // ความสูงมาก่อน — ถ้าไม่มีก็ไม่ต้องเสียเวลาโหลดภาพ
      const demImg = await loadImage(`/tile/dem/${z}/${x}/${y}.png`);
      const heights = decodeTerrarium(demImg);

      const b = tileMercBounds(z, x, y);
      const [lx0, lz0] = mercToLocal(b.x0, b.y0);
      const sizeM = b.span * SCALE;

      const geo = new THREE.PlaneGeometry(sizeM, sizeM, SEG, SEG);
      geo.rotateX(-Math.PI / 2);
      const pos = geo.attributes.position;
      // sample จาก 256x256 ลงเป็น (SEG+1)^2 จุด
      for (let r = 0; r <= SEG; r++) {
        for (let c = 0; c <= SEG; c++) {
          const sx = Math.min(TILE_PX - 1, Math.round((c / SEG) * (TILE_PX - 1)));
          const sy = Math.min(TILE_PX - 1, Math.round((r / SEG) * (TILE_PX - 1)));
          let h = heights[sy * TILE_PX + sx];
          if (!isFinite(h) || h < -1000) h = NODATA_FLAT;
          pos.setY(r * (SEG + 1) + c, h);
        }
      }
      geo.computeVertexNormals();

      // สีพื้นชั่วคราวก่อนภาพดาวเทียมมาถึง (และเป็นสีสำรองตอน offline)
      const mat = new THREE.MeshLambertMaterial({ color: 0x5a6b46 });
      const mesh = new THREE.Mesh(geo, mat);
      // PlaneGeometry มีจุดกึ่งกลางที่ (0,0) → เลื่อนไปมุมบนซ้ายของ tile
      mesh.position.set(lx0 + sizeM / 2, 0, lz0 + sizeM / 2);
      terrainGroup.add(mesh);

      const rec = { state: "ok", mesh, z, x, y, heights,
                    lx0, lz0, sizeM, geo, mat };
      tiles.set(key, rec);
      heightLookup.push(rec);
      stats.loaded++;

      // ภาพดาวเทียม — ดึงที่ zoom ละเอียดกว่าความสูง IMG_UP ระดับ
      // (ความสูงไม่ต้องละเอียดมาก แต่ภาพยิ่งชัดยิ่งอ่านพื้นที่ออก)
      // IMG_UP=1 → 2x2 tile ต่อ 1 tile ภูมิประเทศ = ภาพคมขึ้น 2 เท่า
      // ทุกใบอยู่ในกรอบเดียวกันเป๊ะ UV จึงยังเป็น 0..1 เหมือนเดิม
      loadImageryFor(z, x, y).then((tex) => {
        if (!tex) return;
        mat.map = tex;
        mat.color.set(0xffffff);
        mat.needsUpdate = true;
      }).catch(() => { /* offline → คงสีพื้น ไม่ถือว่า tile พัง */ });
    } catch (err) {
      tiles.set(key, { state: "failed", mesh: null });
      stats.failed++;
    } finally {
      stats.pending--;
      if (opts.onProgress) opts.onProgress(stats);
    }
  }

  function dropTile(key) {
    const t = tiles.get(key);
    if (t && t.mesh) {
      terrainGroup.remove(t.mesh);
      t.geo.dispose();
      if (t.mat.map) t.mat.map.dispose();
      t.mat.dispose();
      heightLookup = heightLookup.filter((r) => r !== t);
      stats.loaded--;
    }
    tiles.delete(key);
  }

  /**
   * โหลด tile รอบจุด lon/lat ตามรัศมี แล้วปล่อยตัวที่ไกลเกินทิ้ง
   * เรียงตามระยะ → ใกล้ตรงกลางมาก่อน (ค่อย ๆ เห็นภาพขยายออก)
   */
  function ensureAround(lon, lat) {
    const [fx, fy] = lonLatToTile(lon, lat, ZOOM);
    const cx = Math.floor(fx), cy = Math.floor(fy);
    const want = new Set();
    const jobs = [];
    for (let dy = -RADIUS; dy <= RADIUS; dy++) {
      for (let dx = -RADIUS; dx <= RADIUS; dx++) {
        const x = cx + dx, y = cy + dy;
        const n = 2 ** ZOOM;
        if (x < 0 || y < 0 || x >= n || y >= n) continue;
        want.add(`${ZOOM}/${x}/${y}`);
        if (!tiles.has(`${ZOOM}/${x}/${y}`)) {
          jobs.push({ x, y, d: dx * dx + dy * dy });
        }
      }
    }
    jobs.sort((a, b) => a.d - b.d);
    for (const j of jobs) buildTile(ZOOM, j.x, j.y);
    // ปล่อย tile ที่หลุดรัศมี — กัน VRAM บวมเมื่อบินไกล
    for (const key of [...tiles.keys()]) if (!want.has(key)) dropTile(key);
    return jobs.length;
  }

  /** ความสูงพื้น (m) ที่พิกัดฉาก — คืน null ถ้ายังไม่ได้โหลด tile ตรงนั้น */
  function sampleHeight(x, z) {
    for (const t of heightLookup) {
      const u = (x - t.lx0) / t.sizeM, v = (z - t.lz0) / t.sizeM;
      if (u < 0 || u >= 1 || v < 0 || v >= 1) continue;
      const px = Math.min(TILE_PX - 1, Math.max(0, u * (TILE_PX - 1)));
      const py = Math.min(TILE_PX - 1, Math.max(0, v * (TILE_PX - 1)));
      const x0 = Math.floor(px), y0 = Math.floor(py);
      const x1 = Math.min(TILE_PX - 1, x0 + 1), y1 = Math.min(TILE_PX - 1, y0 + 1);
      const tx = px - x0, ty = py - y0;
      const h = t.heights;
      return (h[y0 * TILE_PX + x0] * (1 - tx) + h[y0 * TILE_PX + x1] * tx) * (1 - ty) +
             (h[y1 * TILE_PX + x0] * (1 - tx) + h[y1 * TILE_PX + x1] * tx) * ty;
    }
    return null;
  }

  // ── หมุดโดรน ────────────────────────────────────────────────
  const markers = new Map();
  const markerGroup = new THREE.Group();
  scene.add(markerGroup);

  // ── โมเดลควอดคอปเตอร์ ────────────────────────────────────────
  // ทำเป็นชิ้นส่วนจริง (ลำตัว + แขน 4 + มอเตอร์ + ใบพัดหมุน + ขาลง) แทนทรงกลม
  // ขนาด ~28 m ในฉาก: ใหญ่เกินจริงมากเพราะโดรนจริงกว้างไม่ถึงเมตร
  // ถ้าวาดตามสเกลจริงจะเล็กกว่า 1 พิกเซลจนมองไม่เห็นเลย
  const DRONE_R = 14;                    // รัศมีจากกลางลำถึงมอเตอร์

  function makeMarker(id, color) {
    const g = new THREE.Group();
    const solid = (c) => new THREE.MeshLambertMaterial({ color: c });
    const dark = 0x11161c;

    const craft = new THREE.Group();     // เฉพาะตัวโดรน (หมุนตาม heading)

    // ลำตัว — แคปซูลแบน ๆ สีประจำลำ
    const body = new THREE.Mesh(new THREE.BoxGeometry(11, 4.5, 15), solid(color));
    // หัวชี้ทิศ ให้รู้ว่าหันไปทางไหนแม้ใบพัดหมุนเหมือนกันหมด
    const nose = new THREE.Mesh(new THREE.ConeGeometry(3.4, 8, 4), solid(0xffffff));
    nose.rotation.x = -Math.PI / 2;
    nose.position.set(0, 0, -10.5);
    craft.add(body, nose);

    const rotors = [];
    for (let i = 0; i < 4; i++) {
      const a = Math.PI / 4 + i * Math.PI / 2;      // ทแยงแบบ X
      const px = Math.cos(a) * DRONE_R, pz = Math.sin(a) * DRONE_R;
      const arm = new THREE.Mesh(new THREE.BoxGeometry(DRONE_R, 1.8, 2.4), solid(dark));
      arm.position.set(px / 2, 0, pz / 2);
      arm.rotation.y = -a;
      const motor = new THREE.Mesh(
        new THREE.CylinderGeometry(2.2, 2.2, 4, 10), solid(dark));
      motor.position.set(px, 1.6, pz);
      // ใบพัด — แผ่นบางโปร่ง หมุนใน loop ให้ดูมีชีวิต
      const disc = new THREE.Mesh(
        new THREE.BoxGeometry(15, 0.5, 1.6),
        new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.55 }));
      disc.position.set(px, 3.6, pz);
      rotors.push(disc);
      craft.add(arm, motor, disc);
    }
    // ขาลงจอด
    for (const s of [-1, 1]) {
      const leg = new THREE.Mesh(new THREE.BoxGeometry(1.4, 7, 1.4), solid(dark));
      leg.position.set(s * 5, -5, 0);
      craft.add(leg);
    }
    g.add(craft);

    // เส้นดิ่งลงพื้น + วงเงาที่พื้น — อ่านตำแหน่งจริงได้โดยไม่ต้องเดาจากมุมกล้อง
    const stem = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()]),
      new THREE.LineDashedMaterial({ color, dashSize: 22, gapSize: 14,
                                     transparent: true, opacity: 0.65 }));
    const ring = new THREE.Mesh(new THREE.RingGeometry(16, 22, 36),
      new THREE.MeshBasicMaterial({ color, side: THREE.DoubleSide,
                                    transparent: true, opacity: 0.8 }));
    ring.rotation.x = -Math.PI / 2;
    g.add(stem, ring);
    markerGroup.add(g);
    return { group: g, craft, body, nose, rotors, stem, ring, color };
  }

  // ── เส้นทางที่บินผ่านมาแล้ว (trail) — เหมือน trails[] ของแผนที่ 2D ──
  // เก็บเป็นเส้นทึบตามรอยบินจริงใน 3 มิติ (เห็นทั้งเส้นทางและระดับความสูง)
  // จำกัด 500 จุดเท่าฝั่ง 2D กันหน่วยความจำบวมตอนบินนาน ๆ
  const TRAIL_MAX = 500;
  const trailGroup = new THREE.Group();
  scene.add(trailGroup);

  // เก็บเป็น {x, msl, z} ไม่ใช่พิกัดฉากสำเร็จรูป — พอเปลี่ยนตัวคูณความสูง
  // จะได้คำนวณใหม่ได้ ไม่งั้นรอยบินเก่าจะค้างอยู่ระดับเดิมไม่ตรงกับพื้นที่ยืดใหม่
  function pushTrail(m, x, msl, z) {
    if (!m.trailPts) {
      m.trailPts = [];
      // จองบัฟเฟอร์เต็มขนาดตั้งแต่แรกแล้วใช้ setDrawRange
      // setFromPoints() ขยายบัฟเฟอร์เดิมไม่ได้ — ถ้าปล่อยให้โตทีละจุด three จะเตือน
      // "Buffer size too small" แล้ว **เส้นหยุดอัปเดตเงียบ ๆ** (รอยบินค้างอยู่ไม่กี่จุด)
      const g = new THREE.BufferGeometry();
      g.setAttribute("position",
        new THREE.BufferAttribute(new Float32Array(TRAIL_MAX * 3), 3));
      g.setDrawRange(0, 0);
      m.trail = new THREE.Line(g,
        new THREE.LineBasicMaterial({ color: m.color, transparent: true,
                                      opacity: 0.85 }));
      m.trail.frustumCulled = false;
      trailGroup.add(m.trail);
    }
    const last = m.trailPts[m.trailPts.length - 1];
    // ขยับน้อยกว่า 1 m ไม่ต้องเก็บ — ไม่งั้นจอดนิ่งก็ยังอัดจุดไปเรื่อย ๆ
    if (last && Math.abs(last.x - x) < 1 && Math.abs(last.z - z) < 1
             && Math.abs(last.msl - msl) < 1) return;
    m.trailPts.push({ x, msl, z });
    if (m.trailPts.length > TRAIL_MAX) m.trailPts.shift();
    rebuildTrail(m);
  }

  function rebuildTrail(m) {
    if (!m.trail || !m.trailPts) return;
    const arr = m.trail.geometry.attributes.position.array;
    for (let i = 0; i < m.trailPts.length; i++) {
      const p = m.trailPts[i];
      arr[i * 3] = p.x; arr[i * 3 + 1] = p.msl * exag; arr[i * 3 + 2] = p.z;
    }
    m.trail.geometry.attributes.position.needsUpdate = true;
    m.trail.geometry.setDrawRange(0, m.trailPts.length);
    m.trail.geometry.computeBoundingSphere();
  }

  function clearTrail(id) {
    const m = markers.get(Number(id));
    if (!m || !m.trail) return false;
    trailGroup.remove(m.trail);
    m.trail.geometry.dispose();
    m.trail.material.dispose();
    m.trail = null; m.trailPts = null;
    return true;
  }

  function clearTrails() {
    for (const [id] of markers) clearTrail(id);
    return true;
  }

  /** เปลี่ยนสีทุกชิ้นของหมุด (ผู้ใช้เปลี่ยนสีประจำลำได้จาก cockpit) */
  function setMarkerColor(m, color) {
    m.color = color;
    m.body.material.color.setHex(color);
    for (const r of m.rotors) r.material.color.setHex(color);
    m.stem.material.color.setHex(color);
    m.ring.material.color.setHex(color);
    if (m.trail) m.trail.material.color.setHex(color);
  }

  // ความสูงพื้นที่ "จุดปล่อย" (จุดกึ่งกลางฉาก) — ฐานของการวางความสูงโดรน
  // ลองใหม่ทุกครั้งจนกว่า tile ตรงนั้นจะโหลดเสร็จ (คืน null ระหว่างนั้น)
  // ห้ามรีบ fallback เป็น 0: พื้นแถวโคราชสูง ~200 m ถ้าใช้ 0 โดรนจะไปโผล่
  // ใต้ภูมิประเทศ 400 หน่วย = มองไม่เห็นเลย
  let _homeG = null;
  function homeGroundM() {
    if (_homeG === null) {
      const h = sampleHeight(0, 0);
      if (h !== null) _homeG = h;
    }
    return _homeG;
  }

  /** อัปเดตหมุดจาก telemetry
   *  list = [{id, lat, lon, alt(=alt_rel), alt_abs, hdg, armed, mode, color}] */
  function setDrones(list) {
    const seen = new Set();
    for (const d of list || []) {
      const id = Number(d.id);
      if (!id || !isFinite(d.lat) || !isFinite(d.lon)) continue;
      if (!d.lat && !d.lon) continue;                 // ยังไม่มี GPS fix
      seen.add(id);
      // สีมาจาก cockpit (ผู้ใช้เปลี่ยนสีประจำลำได้) — ไม่มีค่อยใช้สีมาตรฐาน
      const color = (d.color !== undefined && d.color !== null)
        ? (typeof d.color === "string"
            ? parseInt(String(d.color).replace("#", ""), 16) : Number(d.color))
        : droneColor(id);
      let m = markers.get(id);
      if (!m) { m = makeMarker(id, color); markers.set(id, m); }
      else if (m.color !== color) { setMarkerColor(m, color); }

      const [x, z] = lonLatToLocal(d.lon, d.lat);
      // ยังไม่มี tile ตรงนั้น → ยึดพื้น 0 ไว้ก่อน เดี๋ยวโหลดเสร็จค่อยตรง
      const ground = sampleHeight(x, z) ?? 0;

      // ── ความสูง: ใช้ "พื้นที่จุดปล่อย + alt_rel" เป็นหลัก ──
      //
      // alt_rel = สูงจาก **จุดปล่อย** (ไม่ใช่จากพื้นใต้ตัวโดรน) เอาไปบวกกับ
      // ความสูงพื้นที่จุดปล่อย ซึ่งอ่านจาก DEM ก้อนเดียวกับที่วาดอยู่ → ฐานตรงกันเป๊ะ
      //
      // ไม่ใช้ alt_abs เป็นหลักเพราะคนละ datum: GPS ให้ความสูงเทียบทรงรี WGS84
      // แต่ DEM เทียบ geoid ในไทยต่างกันราว -30 m — เอามาวางตรง ๆ โดรนจมดินได้
      // (ใช้เป็นทางสำรองเฉพาะตอนยังไม่รู้ความสูงพื้นที่จุดปล่อย)
      const hg = homeGroundM();
      const rel = Math.max(0, Number(d.alt) || 0);
      let msl;
      if (hg !== null) {
        msl = hg + rel;
      } else if (isFinite(Number(d.alt_abs)) && Number(d.alt_abs) !== 0) {
        msl = Number(d.alt_abs);
      } else {
        msl = ground + rel;      // ยังไม่รู้อะไรเลย — อย่างน้อยอย่าให้จมดิน
      }
      // กันจมดินทุกกรณี: โดรนอยู่ใต้ผิวภูมิประเทศไม่ได้
      const y = Math.max(msl * exag, ground * exag);
      const gy = ground * exag;
      m.group.position.set(x, 0, z);
      m.craft.position.set(0, y, 0);
      m.craft.rotation.y = -(Number(d.hdg) || 0) * DEG;
      m.ring.position.set(0, gy + 1.5, 0);
      m.stem.geometry.setFromPoints([
        new THREE.Vector3(0, gy, 0), new THREE.Vector3(0, y, 0)]);
      m.stem.computeLineDistances();
      m.craft.visible = true;
      const op = d.armed ? 1 : 0.55;
      m.body.material.opacity = op; m.body.material.transparent = op < 1;
      m.armed = !!d.armed;
      m.pos = { x, z, y, gy, msl };     // เก็บไว้ให้เส้นนำทางใช้
      pushTrail(m, x, msl, z);
    }
    for (const [id, m] of markers) {
      if (!seen.has(id)) {
        clearTrail(id);
        markerGroup.remove(m.group);
        markers.delete(id);
      }
    }
    // ลำที่เพิ่งโผล่มาต้องได้วงเลือก/วงหัวขบวนด้วย ถ้าถูกเลือกไว้ก่อนหน้า
    setSelection([...selected]);
    setLeader(leaderId);
    updateNavLines();      // โดรนขยับ = เส้นนำทางต้องหดตาม
    return markers.size;
  }

  // ── จุดหมาย + เส้นประนำทาง (ให้เหมือนแผนที่ 2D) ───────────────
  const targets = new Map();          // drone_id -> {group, ...}
  const targetGroup = new THREE.Group();
  scene.add(targetGroup);

  // จำนวนช่วงของเส้นนำทาง — ยิ่งมากเส้นยิ่งทาบพื้นเนียน
  const NAV_SEGS = 24;
  const NAV_ARROWS = 5;        // ลูกศรที่วิ่งไล่ไปตามเส้นพร้อมกัน

  function makeTarget(id, color) {
    const g = new THREE.Group();
    // เสาแสงตั้งที่จุดหมาย — มองเห็นแม้อยู่หลังเนิน
    const beam = new THREE.Mesh(
      new THREE.CylinderGeometry(9, 9, 500, 10, 1, true),
      new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.35,
                                    side: THREE.DoubleSide, depthWrite: false }));
    const ring = new THREE.Mesh(new THREE.RingGeometry(55, 78, 44),
      new THREE.MeshBasicMaterial({ color, side: THREE.DoubleSide,
                                    transparent: true, depthWrite: false }));
    ring.rotation.x = -Math.PI / 2;
    const ring2 = ring.clone();
    ring2.material = ring.material.clone();
    // เส้นประจากโดรนไปจุดหมาย — LineDashedMaterial ต้องเรียก
    // computeLineDistances() ทุกครั้งที่ย้ายจุด ไม่งั้นจะกลายเป็นเส้นทึบ
    // จองให้ครบ NAV_SEGS+1 จุดตั้งแต่แรก — setFromPoints ขยายบัฟเฟอร์เดิมไม่ได้
    const lineGeo = new THREE.BufferGeometry().setFromPoints(
      Array.from({ length: NAV_SEGS + 1 }, () => new THREE.Vector3()));
    // เส้นบาง ๆ จาง ๆ ไว้บอก "แนว" ส่วนทิศทางบอกด้วยลูกศรที่วิ่งไปตามเส้น
    const line = new THREE.Line(lineGeo,
      new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.32 }));
    line.frustumCulled = false;

    // ลูกศรวิ่งไล่ไปทางจุดหมาย — อ่านทิศทางออกทันทีโดยไม่ต้องเทียบสองปลายเส้น
    const arrows = [];
    for (let i = 0; i < NAV_ARROWS; i++) {
      const a = new THREE.Mesh(
        new THREE.ConeGeometry(22, 60, 4),
        new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.95 }));
      a.frustumCulled = false;
      g.add(a);
      arrows.push(a);
    }
    g.add(beam, ring, ring2, line);
    targetGroup.add(g);
    return { group: g, beam, ring, ring2, line, arrows, color };
  }

  /**
   * ตั้งจุดหมายที่สั่งไว้ — list = [{id, lat, lon}]
   * ส่ง [] เพื่อล้างทั้งหมด (ตอนกด Cancel Nav / ถึงเป้าแล้ว)
   */
  function setTargets(list) {
    const seen = new Set();
    for (const t of list || []) {
      const id = Number(t.id);
      if (!id || (!t.lat && !t.lon)) continue;
      seen.add(id);
      // ใช้สีเดียวกับหมุดโดรนของลำนั้น (ผู้ใช้เปลี่ยนสีได้จาก cockpit)
      const dm = markers.get(id);
      const color = dm ? dm.color : droneColor(id);
      let m = targets.get(id);
      if (!m) { m = makeTarget(id, color); targets.set(id, m); }
      else if (m.color !== color) {
        m.color = color;
        for (const o of [m.beam, m.ring, m.ring2, m.line]) o.material.color.setHex(color);
        for (const a of (m.arrows || [])) a.material.color.setHex(color);
      }
      const [x, z] = lonLatToLocal(t.lon, t.lat);
      const gy = (sampleHeight(x, z) ?? 0) * exag;
      m.beam.position.set(x, gy + 250, z);
      m.ring.position.set(x, gy + 3, z);
      m.ring2.position.set(x, gy + 3, z);
      m.pos = { x, z, gy };
      m.visible = true;
    }
    for (const [id, m] of targets) {
      if (!seen.has(id)) { targetGroup.remove(m.group); targets.delete(id); }
    }
    updateNavLines();
    return targets.size;
  }

  /**
   * เส้นประนำทาง — ลากไปตาม "พื้น" จากใต้ตัวโดรนถึงจุดหมาย เหมือนแผนที่ 2D
   * (เดิมลากจากตัวโดรนกลางอากาศไปจบที่ความสูงคงที่ 260 เหนือเป้า ทำให้เส้น
   *  พุ่งขึ้นฟ้าเฉียง ๆ ดูไม่ออกว่าจะไปไหน)
   * แบ่งเป็นช่วง ๆ แล้ววางตามความสูงพื้นจริง เส้นจึงทาบไปกับภูมิประเทศ ไม่ทะลุเนิน
   */
  function updateNavLines() {
    for (const [id, t] of targets) {
      const d = markers.get(id);
      if (!d || !d.pos || !t.pos) { t.line.visible = false; continue; }
      t.line.visible = true;
      const pts = [];
      for (let i = 0; i <= NAV_SEGS; i++) {
        const f = i / NAV_SEGS;
        const x = d.pos.x + (t.pos.x - d.pos.x) * f;
        const z = d.pos.z + (t.pos.z - d.pos.z) * f;
        const g = (sampleHeight(x, z) ?? 0) * exag;
        pts.push(new THREE.Vector3(x, g + 12, z));
      }
      t.line.geometry.setFromPoints(pts);
      t.path = pts;                      // เก็บไว้ให้ลูกศรวิ่งตาม
    }
  }

  /**
   * ขยับลูกศรไปตามเส้นนำทาง (เรียกทุกเฟรม)
   * ลูกศรกระจายกันเท่า ๆ กันแล้ววนกลับไปต้นทาง — เห็นเป็นสายไหลไปทางจุดหมาย
   */
  function animateNavArrows(now) {
    for (const [, t] of targets) {
      if (!t.path || t.path.length < 2 || !t.arrows) continue;
      const segs = t.path.length - 1;
      for (let i = 0; i < t.arrows.length; i++) {
        const a = t.arrows[i];
        // เฟสของแต่ละลูกศร วนอยู่ในช่วง 0..1 ตามเวลา
        const f = ((now * 0.00022) + i / t.arrows.length) % 1;
        const s = Math.min(segs - 1e-6, f * segs);
        const i0 = Math.floor(s), k = s - i0;
        const p0 = t.path[i0], p1 = t.path[i0 + 1];
        a.position.lerpVectors(p0, p1, k);
        a.position.y += 26;              // ลอยเหนือพื้นนิดหน่อย ไม่ให้จมเนิน
        // หันหัวกรวยไปทางที่กำลังวิ่ง (กรวยของ three ชี้ +Y โดยปริยาย)
        const dir = p1.clone().sub(p0);
        if (dir.lengthSq() > 1e-9) {
          a.quaternion.setFromUnitVectors(
            new THREE.Vector3(0, 1, 0), dir.normalize());
        }
        // จางหัว-ท้ายเส้น ไม่ให้ลูกศรโผล่/หายกึ่งกลางแบบกระตุก
        a.material.opacity = 0.95 * Math.min(1, Math.sin(f * Math.PI) * 2.2);
      }
    }
  }

  // ══════════════════════════════════════════════════════════
  //  ความสามารถที่แผนที่ 2D มี — 3D ต้องมีให้ครบ
  // ══════════════════════════════════════════════════════════
  let selected = new Set();
  let leaderId = 0;

  /** ลำที่ถูกเลือก (คลิกการ์ดฝั่งซ้าย) — กะพริบให้เห็นว่าเลือกลำไหนอยู่ */
  function setSelection(ids) {
    selected = new Set((ids || []).map(Number));
    for (const [id, m] of markers) {
      const on = selected.has(id);
      if (!on && m.halo) { m.group.remove(m.halo); m.halo.geometry.dispose();
                           m.halo.material.dispose(); m.halo = null; }
      if (on && !m.halo) {
        m.halo = new THREE.Mesh(
          new THREE.RingGeometry(26, 34, 40),
          new THREE.MeshBasicMaterial({ color: 0xffffff, side: THREE.DoubleSide,
                                        transparent: true, depthWrite: false }));
        m.halo.rotation.x = -Math.PI / 2;
        m.group.add(m.halo);
      }
    }
    return selected.size;
  }

  /** ★ หัวขบวน — วงแหวนทองรอบลำแม่ (เหมือน setLeaderId ของแผนที่ 2D) */
  function setLeader(id) {
    leaderId = Number(id) || 0;
    for (const [did, m] of markers) {
      const on = did === leaderId;
      if (!on && m.crown) { m.group.remove(m.crown); m.crown.geometry.dispose();
                            m.crown.material.dispose(); m.crown = null; }
      if (on && !m.crown) {
        m.crown = new THREE.Mesh(
          new THREE.TorusGeometry(30, 2.6, 8, 40),
          new THREE.MeshBasicMaterial({ color: 0xffd54a }));
        m.crown.rotation.x = -Math.PI / 2;
        m.group.add(m.crown);
      }
    }
    return leaderId;
  }

  /** เส้นเชื่อมขบวน แม่ -> ลูก (เหมือน setSwarmEdges) */
  const swarmGroup = new THREE.Group();
  scene.add(swarmGroup);
  let swarmEdges = [];
  function setSwarmEdges(edges) {
    for (const o of swarmGroup.children.slice()) {
      swarmGroup.remove(o); o.geometry.dispose(); o.material.dispose();
    }
    swarmEdges = (edges || []).map((e) => ({
      a: Number(e.leader ?? e.leader_id ?? e[0]),
      b: Number(e.follower ?? e.follower_id ?? e[1]),
    }));
    for (const _ of swarmEdges) {
      swarmGroup.add(new THREE.Line(
        new THREE.BufferGeometry().setFromPoints(
          [new THREE.Vector3(), new THREE.Vector3()]),
        new THREE.LineBasicMaterial({ color: 0xffd54a, transparent: true,
                                      opacity: 0.55 })));
    }
    updateSwarmEdges();
    return swarmEdges.length;
  }
  function updateSwarmEdges() {
    swarmEdges.forEach((e, i) => {
      const ln = swarmGroup.children[i];
      const A = markers.get(e.a), B = markers.get(e.b);
      if (!ln) return;
      if (!A || !B || !A.pos || !B.pos) { ln.visible = false; return; }
      ln.visible = true;
      ln.geometry.setFromPoints([
        new THREE.Vector3(A.pos.x, A.pos.y, A.pos.z),
        new THREE.Vector3(B.pos.x, B.pos.y, B.pos.z)]);
    });
  }

  /** Geofence — วาดเป็นกำแพงโปร่งให้เห็นขอบเขตในสามมิติ */
  let fenceMesh = null;
  function setFence(points) {
    if (fenceMesh) {
      scene.remove(fenceMesh);
      fenceMesh.traverse((o) => { if (o.geometry) o.geometry.dispose();
                                  if (o.material) o.material.dispose(); });
      fenceMesh = null;
    }
    const pts = points || [];
    if (pts.length < 3) return 0;
    const g = new THREE.Group();
    const WALL = 400;                     // สูงพอให้เห็นว่าเป็นรั้ว
    const loop = pts.concat([pts[0]]);
    const verts = [];
    for (let i = 0; i < loop.length - 1; i++) {
      const [x1, z1] = lonLatToLocal(loop[i].lon, loop[i].lat);
      const [x2, z2] = lonLatToLocal(loop[i + 1].lon, loop[i + 1].lat);
      const y1 = (sampleHeight(x1, z1) ?? 0) * exag;
      const y2 = (sampleHeight(x2, z2) ?? 0) * exag;
      // สองสามเหลี่ยมต่อหนึ่งด้าน
      verts.push(x1, y1, z1, x2, y2, z2, x2, y2 + WALL, z2);
      verts.push(x1, y1, z1, x2, y2 + WALL, z2, x1, y1 + WALL, z1);
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.Float32BufferAttribute(verts, 3));
    geo.computeVertexNormals();
    g.add(new THREE.Mesh(geo, new THREE.MeshBasicMaterial({
      color: 0xef4444, transparent: true, opacity: 0.16,
      side: THREE.DoubleSide, depthWrite: false })));
    // เส้นขอบบน/ล่างให้อ่านรูปทรงออกชัด
    for (const off of [0, WALL]) {
      const lp = loop.map((p) => {
        const [x, z] = lonLatToLocal(p.lon, p.lat);
        return new THREE.Vector3(x, (sampleHeight(x, z) ?? 0) * exag + off, z);
      });
      g.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(lp),
        new THREE.LineBasicMaterial({ color: 0xef4444, transparent: true,
                                      opacity: 0.85 })));
    }
    fenceMesh = g;
    scene.add(g);
    return pts.length;
  }

  /** เส้นทาง Waypoint — จุดกลม + เส้นประเชื่อม (เหมือน addWaypoint ของ 2D) */
  const wpGroup = new THREE.Group();
  scene.add(wpGroup);
  function setWaypoints(routes) {
    for (const o of wpGroup.children.slice()) {
      wpGroup.remove(o);
      o.traverse((c) => { if (c.geometry) c.geometry.dispose();
                          if (c.material) c.material.dispose(); });
    }
    let n = 0;
    for (const r of routes || []) {
      const color = r.color !== undefined
        ? (typeof r.color === "string"
            ? parseInt(String(r.color).replace("#", ""), 16) : r.color)
        : droneColor(Number(r.id) || 1);
      const g = new THREE.Group();
      const pts = [];
      (r.points || []).forEach((p, i) => {
        const [x, z] = lonLatToLocal(p.lon, p.lat);
        const y = (sampleHeight(x, z) ?? 0) * exag;
        pts.push(new THREE.Vector3(x, y + 120, z));
        const done = i < (r.done || 0);
        const pointColor = p.action === "servo_a" ? 0xef4444 :
                           p.action === "servo_b" ? 0xeab308 : color;
        const dot = new THREE.Mesh(
          p.action ? new THREE.OctahedronGeometry(23, 0) : new THREE.SphereGeometry(16, 12, 10),
          new THREE.MeshBasicMaterial({ color: pointColor, transparent: true,
                                        opacity: done ? 0.35 : 1 }));
        dot.position.set(x, y + 120, z);
        const pole = new THREE.Line(
          new THREE.BufferGeometry().setFromPoints([
            new THREE.Vector3(x, y, z), new THREE.Vector3(x, y + 120, z)]),
          new THREE.LineBasicMaterial({ color: pointColor, transparent: true, opacity: 0.5 }));
        g.add(dot, pole);
        n++;
      });
      if (pts.length > 1) {
        const line = new THREE.Line(
          new THREE.BufferGeometry().setFromPoints(pts),
          new THREE.LineDashedMaterial({ color, dashSize: 70, gapSize: 45,
                                         transparent: true, opacity: 0.9 }));
        line.computeLineDistances();
        g.add(line);
      }
      wpGroup.add(g);
    }
    return n;
  }

  /** จุดสถานี/GCS */
  let gcsMark = null;
  function setGCS(lat, lon) {
    if (gcsMark) { scene.remove(gcsMark); gcsMark = null; }
    if (!lat && !lon) return false;
    const [x, z] = lonLatToLocal(lon, lat);
    const y = (sampleHeight(x, z) ?? 0) * exag;
    const g = new THREE.Group();
    const mast = new THREE.Mesh(new THREE.CylinderGeometry(2, 2, 90, 8),
      new THREE.MeshBasicMaterial({ color: 0x38bdf8 }));
    mast.position.set(x, y + 45, z);
    const base = new THREE.Mesh(new THREE.RingGeometry(24, 32, 32),
      new THREE.MeshBasicMaterial({ color: 0x38bdf8, side: THREE.DoubleSide,
                                    transparent: true, opacity: 0.8 }));
    base.rotation.x = -Math.PI / 2;
    base.position.set(x, y + 2, z);
    g.add(mast, base);
    gcsMark = g;
    scene.add(g);
    return true;
  }

  /**
   * เลื่อนมุมมองไปหาโดรนลำหนึ่ง (คลิกการ์ด → โฟกัส)
   *
   * **คงมุมกล้องกับระยะซูมที่ผู้ใช้ตั้งไว้** — เลื่อนแบบแพนเฉย ๆ
   * เดิมเรียก focus() ซึ่งรีเซ็ตกล้องกลับมุมตั้งต้นทุกครั้ง พอคลิกสลับลำไปมา
   * ภาพจะกระตุกเปลี่ยนมุมทุกคลิก เวียนหัวและเสียมุมที่อุตส่าห์หมุนไว้
   */
  function focusDrone(id) {
    const m = markers.get(Number(id));
    if (!m || !m.pos) return false;
    // ย้ายทั้งกล้องและจุดเล็งด้วยเวกเตอร์เดียวกัน = มุมและระยะเดิมเป๊ะ
    const off = camera.position.clone().sub(controls.target);
    const tgt = new THREE.Vector3(m.pos.x, m.pos.y, m.pos.z);
    controls.target.copy(tgt);
    camera.position.copy(tgt).add(off);
    controls.update();
    const [lon, lat] = localToLonLat(m.pos.x, m.pos.z);
    ensureAround(lon, lat);
    return true;
  }

  /** สถานะหมุดลำหนึ่ง (ใช้ตรวจสอบ/ทดสอบ) */
  function droneState(id) {
    const m = markers.get(Number(id));
    if (!m || !m.pos) return null;
    return { x: m.pos.x, y: m.pos.y, z: m.pos.z, gy: m.pos.gy,
             msl: m.pos.msl, color: m.color,
             selected: selected.has(Number(id)), leader: leaderId === Number(id) };
  }

  /** ตัวคูณความสูง — ยืดพื้นแล้วต้องขยับหมุด/จุดหมายตามให้ตรงกัน */
  function setExaggeration(f) {
    exag = Math.max(1, Math.min(6, Number(f) || 1));
    terrainGroup.scale.y = exag;
    // เก็บ AMSL ไว้แล้ว จึงคำนวณใหม่ตรง ๆ ไม่ต้องถอดค่าเดิมกลับ
    for (const [, m] of markers) {
      if (!m.pos) continue;
      const y = m.pos.msl * exag;
      const gy = (sampleHeight(m.pos.x, m.pos.z) ?? 0) * exag;
      m.craft.position.y = y;
      m.ring.position.y = gy + 1.5;
      m.stem.geometry.setFromPoints([
        new THREE.Vector3(0, gy, 0), new THREE.Vector3(0, y, 0)]);
      m.stem.computeLineDistances();
      m.pos.y = y; m.pos.gy = gy;
      rebuildTrail(m);
    }
    for (const [id, t] of targets) {
      if (!t.pos) continue;
      const gy = (sampleHeight(t.pos.x, t.pos.z) ?? 0) * exag;
      t.beam.position.y = gy + 250;
      t.ring.position.y = gy + 3;
      t.ring2.position.y = gy + 3;
      t.pos.gy = gy;
    }
    exagPrev = exag;
    updateNavLines();
    return exag;
  }
  let exagPrev = exag;

  /** เล็งกล้องไปที่พิกัด (dist = ระยะกล้อง m) + โหลด tile รอบจุดนั้น */
  function focus(lon, lat, dist) {
    const d = dist || 2500;
    const [x, z] = lonLatToLocal(lon, lat);
    const g = (sampleHeight(x, z) ?? 0) * exag;
    camera.position.set(x + d * 0.5, g + d * 0.55, z + d * 0.75);
    controls.target.set(x, g, z);
    controls.update();
    ensureAround(lon, lat);
  }

  // ── คลิกบนภูมิประเทศ -> คืน lon/lat (ใช้สั่ง GOTO เหมือนแผนที่เดิม) ──
  const ray = new THREE.Raycaster();
  function pick(clientX, clientY) {
    const r = renderer.domElement.getBoundingClientRect();
    const ndc = new THREE.Vector2(
      ((clientX - r.left) / r.width) * 2 - 1,
      -((clientY - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(ndc, camera);
    const hit = ray.intersectObjects(terrainGroup.children, false)[0];
    if (!hit) return null;
    const [lon, lat] = localToLonLat(hit.point.x, hit.point.z);
    return { lon, lat, alt: hit.point.y };
  }

  // ── loop ────────────────────────────────────────────────────
  let frames = 0, last = performance.now(), lastEnsure = 0;
  function loop() {
    requestAnimationFrame(loop);
    controls.update();
    const now = performance.now();
    if (markers.size) {
      const dt = now / 1000;
      for (const [, m] of markers) {
        // ขนาดคงที่บนจอ: โดรนจริงกว้างไม่ถึงเมตร ถ้าวาดตามสเกลบนแผนที่กว้าง
        // หลายกิโลจะเล็กกว่า 1 พิกเซล มองไม่เห็นเลย — หมุดบนแผนที่ต้อง "อ่านออก"
        // ที่ทุกระยะซูม (ตำแหน่ง/ความสูงยังตรงเป๊ะ ยืดแค่ขนาดที่วาด)
        if (m.pos) {
          const d = camera.position.distanceTo(
            new THREE.Vector3(m.pos.x, m.pos.y, m.pos.z));
          const s = Math.max(1, Math.min(60, d * 0.0022));
          m.craft.scale.setScalar(s);
          m.ring.scale.setScalar(Math.max(1, s * 0.55));
        }
        // ลำที่เลือกอยู่ → วงขาวกะพริบ (เหมือนคลิกการ์ดแล้วหมุดเด้งบนแผนที่ 2D)
        if (m.halo) {
          const b = 0.5 + 0.5 * Math.sin(dt * 6.0);
          m.halo.material.opacity = 0.25 + 0.7 * b;
          const hs = (m.craft.scale.x || 1) * (0.85 + 0.3 * b);
          m.halo.scale.setScalar(hs);
          m.halo.position.y = m.pos ? m.pos.gy + 2.5 : 0;
        }
        if (m.crown) {
          m.crown.scale.setScalar(m.craft.scale.x || 1);
          m.crown.position.y = m.pos ? m.pos.y : 0;
          m.crown.rotation.z = dt * 0.9;
        }
        // ใบพัดหมุนเฉพาะลำที่ armed — เห็นปุ๊บรู้เลยว่าลำไหนพร้อมบิน
        if (!m.armed) continue;
        for (let i = 0; i < m.rotors.length; i++) {
          m.rotors[i].rotation.y = dt * (i % 2 ? 26 : -26);
        }
      }
      updateSwarmEdges();
    }
    // จุดหมายกะพริบเหมือนแผนที่ 2D — วงในกับวงนอกเต้นคนละจังหวะให้สังเกตง่าย
    if (targets.size) {
      animateNavArrows(now);
      const p = now / 1000;
      const a = 0.35 + 0.45 * (0.5 + 0.5 * Math.sin(p * 5.2));
      for (const [, t] of targets) {
        t.ring.material.opacity = a;
        const s = 1 + 0.45 * (0.5 + 0.5 * Math.sin(p * 5.2 + Math.PI));
        t.ring2.scale.set(s, s, s);
        t.ring2.material.opacity = 0.75 - 0.5 * (s - 1) / 0.45;
      }
    }
    // ขยับกล้องไปไกล → เติม tile รอบจุดที่มองอยู่ (ทุก 500 ms พอ)
    if (now - lastEnsure > 500) {
      lastEnsure = now;
      const t = controls.target;
      const [lon, lat] = localToLonLat(t.x, t.z);
      ensureAround(lon, lat);
    }
    renderer.render(scene, camera);
    frames++;
    if (now - last >= 1000) {
      stats.fps = frames * 1000 / (now - last);
      frames = 0; last = now;
      if (opts.onFps) opts.onFps(stats.fps);
    }
  }
  function resize() {
    // ใช้ขนาดจริงของ mount ถ้าอ่านได้ ไม่งั้นถอยไปใช้ขนาดหน้าต่าง
    // (ตอนสร้างฉาก layout อาจยังไม่เสร็จ → clientWidth = 0 แล้วได้ canvas จิ๋ว
    //  ซึ่งดูเผิน ๆ เหมือนทำงานปกติเพราะ fps/สามเหลี่ยมยังขึ้นครบ)
    const w = Math.max(1, mount.clientWidth || innerWidth);
    const h = Math.max(1, mount.clientHeight || innerHeight);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    renderer.setSize(w, h, false);
    renderer.domElement.style.width = "100%";
    renderer.domElement.style.height = "100%";
  }
  addEventListener("resize", resize);
  // ติดตามขนาดของ mount เองด้วย — แผง/แท็บใน cockpit ย่อขยายได้โดยหน้าต่างไม่ขยับ
  if (typeof ResizeObserver !== "undefined") {
    new ResizeObserver(resize).observe(mount);
  }
  requestAnimationFrame(resize);      // เก็บขนาดจริงหลัง layout รอบแรก
  loop();

  /** ตั้งจุดกึ่งกลางฉากใหม่ (เช่นย้ายฐานบิน) — ล้าง tile เดิมทิ้งทั้งหมด */
  function setOrigin(lon, lat) {
    for (const key of [...tiles.keys()]) dropTile(key);
    origin = { lat, lon };
    [oMx, oMy] = lonLatToMerc(lon, lat);
    focus(lon, lat, 2500);
  }

  function capture(type, quality) {
    renderer.render(scene, camera);
    return renderer.domElement.toDataURL(type || "image/jpeg", quality || 0.9);
  }

  focus(origin.lon, origin.lat, 3000);
  say("terrain", `สตรีมตามรัศมี z${ZOOM} · ${(RADIUS * 2 + 1)}x${RADIUS * 2 + 1} tiles`, "ok");

  return { scene, camera, renderer, controls, stats,
           setDrones, setTargets, focus, setOrigin, ensureAround, sampleHeight,
           setExaggeration, getExaggeration: () => exag,
           // ความสามารถชุดเดียวกับแผนที่ 2D
           setSelection, setLeader, setSwarmEdges, setFence, setWaypoints,
           setGCS, focusDrone, droneState, clearTrail, clearTrails,
           trailLength: (id) => {
             const m = markers.get(Number(id));
             return m && m.trailPts ? m.trailPts.length : 0;
           },
           lonLatToLocal, localToLonLat, pick, capture, resize,
           tileCount: () => stats.loaded, droneCount: () => markers.size,
           targetCount: () => targets.size,
           selectionCount: () => selected.size };
}
