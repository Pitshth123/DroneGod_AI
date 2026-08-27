# prototype: แผนที่ 3D ใน QWebEngine

พิสูจน์ว่า cockpit ของ SwarmGod (PyQt5 + QWebEngineView) เรนเดอร์ภูมิประเทศ 3 มิติ
แบบ [SIAHRA](https://github.com/flukelaster/SIAHRA) ได้จริงไหม **ก่อน** ลงแรงทำของจริง

> โฟลเดอร์นี้**แยกจากโค้ดหลักทั้งหมด** ไม่ import `swarmgod_gui` และไม่มีไฟล์ไหน
> ในโปรเจคอ้างถึงมัน ลบทิ้งทั้งโฟลเดอร์ได้โดยไม่กระทบอะไร

## รัน

```bash
python prototypes/map3d/probe.py
```

```bash
python prototypes/map3d/probe.py --zoom 12 --shot shot.png
```

```bash
python prototypes/map3d/probe.py --no-imagery
```

`--zoom` = ระดับ tile ของภาพดาวเทียม (10 เร็ว · 11 สมดุล · 12 ละเอียด/หนัก)

## ผลบนเครื่องที่ทดสอบ (2026-08-24)

Windows 10 · Intel Iris Xe Graphics · PyQt5 5.15.2

```
[ok]   tile proxy       http://127.0.0.1:xxxxx (cache ~/.swarmgod/tiles)
[ok]   webgl            WebGL 2
[ok]   gpu              Intel(R) Iris(R) Xe Graphics      ← GPU จริง ไม่ใช่ SwiftShader
[ok]   three.js         r170 + OrbitControls (static import)
[ok]   manifest         นครราชสีมา (aoi 30)
[ok]   terrain.bin      1.18 MB · 808x767 @ 247m · 11ms
[ok]   ช่วงความสูง      3..1306 m (manifest 3..1307)
[ok]   mesh             620k verts · 1236k tris · 419ms
[ok]   ตรวจพิกัด UTM    bbox อยู่ใน raster · ขอบเผื่อ W3.1 E1.5 N2.6 S2.3 km
[ok]   ภาพดาวเทียม      z12 · 462/462 tiles · 5632x5376px · 5746ms
[ok]   จุด home SITL    อยู่ในขอบเขต · พื้นสูง 189 m
[ok]   fps เฉลี่ย       59.6
```

**สรุป: ทำได้** — 1.24 ล้านสามเหลี่ยม + ภาพดาวเทียมจริง ยังได้ ~60 fps

## ภาพดาวเทียม: ใช้ `tile_cache.py` ของ cockpit ได้เลย ✅

SIAHRA กับ DroneGod ใช้ **Esri World Imagery ตัวเดียวกันเป๊ะ** (URL template เดียวกัน)
prototype นี้จึง `import` `TileServer` ตัวจริงจาก `swarmgod_gui.core.tile_cache`
มาใช้ **โดยไม่แก้ไฟล์นั้นเลยสักบรรทัด** — ใช้ cache เดียวกันที่ `~/.swarmgod/tiles`
ด้วย แปลว่า tile ที่ cockpit เคยโหลดไว้ แผนที่ 3D หยิบมาใช้ได้ทันที และกลับกัน

| zoom | tiles | ขนาด texture | ครั้งแรก (โหลดจาก Esri) | ครั้งต่อไป (จาก cache) |
|---|---|---|---|---|
| 10 | 42 | 1792×1536 | 984 ms | — |
| 11 | 132 | 3072×2816 | 1946 ms | **810 ms** |
| 12 | 462 | 5632×5376 | 5746 ms | — |

**offline ใช้ได้จริง** — ยิงซ้ำที่ z11 เร็วขึ้น 2.4 เท่าเพราะอ่านจากดิสก์ล้วน
ถ้าไม่มีเน็ตและไม่มีใน cache `tile_cache` จะคืน tile เทาแทน ไม่ค้าง

### การอ้างอิงพิกัด (สำคัญ — ห้ามยืดภาพให้พอดีกรอบ)

ภูมิประเทศอยู่ใน **UTM** แต่ tile เป็น **Web Mercator** สองระบบนี้ไม่ทับกัน
ต้องแปลง **ทีละ vertex**: `UTM → WGS84 → Web Mercator → UV`
(ยืดภาพให้เต็มกรอบตรง ๆ ภาพจะเหลื่อมจากพื้นจริงหลายกิโลเมตร)

โค้ดแปลงพิกัดย่อมาจาก `apps/web/src/scene/projection.ts` ของ SIAHRA (MIT)

> **หมายเหตุการตรวจสอบ:** `raster` **กว้างกว่า** `bbox` ของจังหวัดเสมอ (ETL ปัดขึ้น
> เป็นเซลล์เต็ม) และสี่เหลี่ยมใน UTM ไม่มีทางตรงกับสี่เหลี่ยมใน lon/lat —
> ขอบซ้ายบน/ล่างต่างกัน ~3 km เป็นเรื่องปกติของ UTM ไม่ใช่บั๊ก
> เกณฑ์ที่ถูกคือ **"bbox ต้องอยู่ใน raster ครบ"** (ขอบเผื่อทุกด้าน ≥ 0)

## ข้อจำกัดที่เจอ (สำคัญ — ต้องออกแบบตามนี้)

QtWebEngine 5.15.2 = **Chromium 83** (พ.ค. 2020) เก่ากว่าที่ SIAHRA ตั้งเป้าไว้มาก

| อย่าง | สถานะ | หมายเหตุ |
|---|---|---|
| WebGL 2 | ✅ | max texture 16384 px |
| ES modules (static import) | ✅ | ทั้ง inline และ `<script type="module" src>` |
| **dynamic `import()`** | ❌ | ตอบ `Failed to fetch dynamically imported module` เสมอ ทั้ง path relative/absolute — ทั้งที่ `fetch()` ไฟล์เดียวกันได้ปกติ |
| **import maps** | ❌ | มาใน Chromium 89 → ต้อง import ด้วย path ตรง และต้องแก้ `from "three"` ในไฟล์ addon เอง |
| **top-level await** | ❌ | มาใน Chromium 89 → ต้องห่อด้วย `async function` |
| `fetch()` + ArrayBuffer | ✅ | terrain.bin 1.18 MB โหลด 11 ms |

### three.js: ใช้ได้ถึง r170 เท่านั้น

ไล่ทดสอบทีละเวอร์ชัน — **r178 ขึ้นไปพังทันที** (โมดูลไม่ยอม evaluate)

| เวอร์ชัน | ผล |
|---|---|
| r118 · r128 · r137 · r150 · r160 · **r170** | ✅ |
| r178 · r180 · r185 | ❌ |

SIAHRA ใช้ **r185** → ยกโค้ด `scene/` ของเขามาตรง ๆ ไม่ได้ ต้อง backport มาที่ r170
(หรืออัป Qt เป็น PyQt6/Qt 6.x ซึ่ง Chromium ใหม่กว่ามาก — เป็นงานใหญ่กว่า)

### headless ใช้ไม่ได้

`QT_QPA_PLATFORM=offscreen` + QtWebEngine = **segfault** ทดสอบใน CI แบบไม่มีจอไม่ได้
ต้องเปิดหน้าต่างจริง (`probe.py` ที่ไม่ใส่ `--headless`)

## รูปแบบไฟล์ terrain.bin

ง่ายมาก — **`Int16` ดิบ ไม่มี header**

- row-major, `row 0 = ขอบเหนือ` (ตรงกับ GDAL EHdr BIL upper-left origin)
- ขนาด = `width × height` เซลล์ จาก `manifest.terrain`
- ค่า = ความสูงเป็น**เมตรจริง** (ไม่เข้ารหัสแบบ Terrain-RGB)
- `-32768` = nodata
- ตรวจแล้ว: 808 × 767 × 2 = 1,239,472 bytes = ขนาดไฟล์เป๊ะ

## สิ่งที่ prototype นี้**ยังไม่ได้**พิสูจน์

- ❌ **quadtree LOD streaming** — โหลด overview 247 m ก้อนเดียว ยังไม่ได้แตะ tile pyramid (leaf 30 m)
- ⚠️ **ภาพดาวเทียมยังเป็น texture ก้อนเดียว** — z12 = 5632×5376 px กิน VRAM ~121 MB
  ต่อจังหวัด ของจริงต้องสตรีมเป็น tile ตาม LOD เหมือน `SatelliteImagery.ts` ของ SIAHRA
  ไม่ใช่ต่อเป็นผืนเดียวแบบนี้ (z13 ขึ้นไปจะชน `MAX_TEXTURE_SIZE` 16384 px ด้วย)
- ❌ **หลายจังหวัด / สลับ AOI** — ทดสอบแค่โคราช (aoi 30) จังหวัดเดียว
- ❌ **สะพาน Python↔JS** — ยังไม่ได้ต่อ `MapBridge` ให้ป้อนตำแหน่งโดรนสดจาก telemetry
- ❌ **เครื่องอื่น** — ผลนี้มาจาก Intel Iris Xe ตัวเดียว เครื่องที่ GPU ต่างออกไป
  (หรือ VM/remote desktop ที่ไม่มี GPU) อาจตกไป SwiftShader แล้วช้าลงมาก — `probe.py`
  จะขึ้น `[warn]` ที่บรรทัด `gpu` ให้เห็นเอง

## ⚠️ ข้อควรระวังด้านความปลอดภัย

ภูมิประเทศชุดนี้ละเอียด **30 m** (Copernicus GLO-30) ใช้เป็น**ฉากหลังให้มองเห็นภาพรวม**
เท่านั้น **ห้ามเอาไปคำนวณความสูงเหนือพื้น (AGL) หรือ terrain avoidance เด็ดขาด** —
ความคลาดเคลื่อนสูงเกินกว่าจะเชื่อได้ในงานที่เกี่ยวกับความปลอดภัยการบิน

## ที่มาของไฟล์ + license

| ไฟล์ | ที่มา | license |
|---|---|---|
| `vendor/three/three-0.170.0.js` | three.js r170 | MIT |
| `vendor/three/OrbitControls-170.js` | three.js r170 addons (แก้ import จาก `"three"` เป็น path ตรง) | MIT |
| `data/aoi/30/terrain.bin`, `manifest.json` | SIAHRA (สร้างจาก Copernicus GLO-30) | โค้ด MIT · **ข้อมูล DEM ต้องใส่ attribution ของ Copernicus** |

ถ้าจะเอา terrain ของ SIAHRA ไปใช้จริงในผลิตภัณฑ์ ต้องใส่ attribution ให้ครบ
(Copernicus DEM, OSM = ODbL ซึ่งเป็น share-alike ต้องอ่านเงื่อนไขก่อน)
