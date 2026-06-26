/**
 * BGP Tree Rings — AS を中心としたインターネット年輪可視化
 *
 * レイアウト:
 *  XZ 平面に同心円リング（1リング = 1 AS ホップ）
 *  Y 軸 = log(プレフィックス数) → 3D 地形のような起伏
 *  5 扇形セクター（RIR 地域別）
 *  中心から広がる波紋アニメーション
 */

import * as THREE from 'three';
import { OrbitControls }   from 'three/addons/controls/OrbitControls.js';
import { EffectComposer }  from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass }      from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { OutputPass }      from 'three/addons/postprocessing/OutputPass.js';

// ============================================================
// 定数
// ============================================================

const RING_UNIT    = 130;             // 1ホップあたりの半径 (units)
const HOP_Y_SCALE  = 22;              // ホップ数² × この値 → Y（重力井戸の深さ）
const PFX_Y_JITTER = 2.5;             // プレフィックス数による微細な高さ変化
const BG_COLOR  = 0x000208;

// セクター順序（時計回り、12時から APNIC が始まると地理的に自然）
const REGIONS = ['APNIC', 'RIPE', 'AFRINIC', 'LACNIC', 'ARIN'];
const SECTOR  = (Math.PI * 2) / REGIONS.length;   // 72°

const REGION_COLORS = {
  RIPE:    new THREE.Color(0x4499ff),
  ARIN:    new THREE.Color(0xff6633),
  APNIC:   new THREE.Color(0x44ffcc),
  LACNIC:  new THREE.Color(0xcc44ff),
  AFRINIC: new THREE.Color(0xffdd33),
  PRIVATE: new THREE.Color(0x889ab4),
};

// ============================================================
// ローディング UI
// ============================================================

function setStatus(msg, detail = '') {
  const m = document.getElementById('status-msg');
  const d = document.getElementById('status-detail');
  if (m) m.textContent = msg;
  if (d) d.textContent = detail;
}

function hideLoading() {
  const el = document.getElementById('loading');
  if (!el) return;
  el.style.opacity = '0';
  el.addEventListener('transitionend', () => el.remove(), { once: true });
}

// ============================================================
// シーン初期化
// ============================================================

let composer;   // resize ハンドラから参照するためモジュールスコープ

function initScene() {
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(BG_COLOR);
  scene.fog = new THREE.FogExp2(BG_COLOR, 0.00045);

  const camera = new THREE.PerspectiveCamera(50, innerWidth / innerHeight, 1, 6000);
  camera.position.set(0, 250, 1100);
  camera.lookAt(0, 400, 0);

  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.setSize(innerWidth, innerHeight);
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 0.9;
  document.body.appendChild(renderer.domElement);

  window.addEventListener('resize', () => {
    camera.aspect = innerWidth / innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(innerWidth, innerHeight);
    if (composer) composer.setSize(innerWidth, innerHeight);
  });

  return { scene, camera, renderer };
}

// ============================================================
// 背景星フィールド
// ============================================================

function createStarField(scene) {
  const N   = 18000;
  const pos = new Float32Array(N * 3);
  for (let i = 0; i < N; i++) {
    const theta = Math.random() * Math.PI * 2;
    const phi   = Math.acos(2 * Math.random() - 1);
    const r     = 2500 + Math.random() * 2000;
    pos[i * 3]     = r * Math.sin(phi) * Math.cos(theta);
    pos[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
    pos[i * 3 + 2] = r * Math.cos(phi);
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  scene.add(new THREE.Points(geo, new THREE.PointsMaterial({
    color: 0xffffff, size: 1.0,
    sizeAttenuation: false, transparent: true, opacity: 0.4,
  })));
}

// ============================================================
// 年輪ガイド（同心円 & セクター境界線）
// ============================================================

// 重力井戸の Y 座標（放物面）
function hopY(hop) {
  return hop * hop * HOP_Y_SCALE;
}

function createGuides(scene, maxHops) {
  const N = 128;

  // 同心円リング（ホップごと）
  for (let hop = 1; hop <= maxHops; hop++) {
    const r   = hop * RING_UNIT;
    const pts = [];
    for (let i = 0; i <= N; i++) {
      const a = (i / N) * Math.PI * 2;
      pts.push(new THREE.Vector3(r * Math.cos(a), 0, r * Math.sin(a)));
    }
    const geo   = new THREE.BufferGeometry().setFromPoints(pts);
    const alpha = 0.12 + hop * 0.04;   // 外リングほど少し濃く
    scene.add(new THREE.Line(geo, new THREE.LineBasicMaterial({
      color: 0x0a2a4a, transparent: true, opacity: alpha, depthWrite: false,
    })));
  }

  // 最外リング（境界として少し明るく）
  const outerR = (maxHops + 0.35) * RING_UNIT;
  const outerPts = [];
  for (let i = 0; i <= N; i++) {
    const a = (i / N) * Math.PI * 2;
    outerPts.push(new THREE.Vector3(outerR * Math.cos(a), 0, outerR * Math.sin(a)));
  }
  scene.add(new THREE.Line(
    new THREE.BufferGeometry().setFromPoints(outerPts),
    new THREE.LineBasicMaterial({ color: 0x112244, transparent: true, opacity: 0.5, depthWrite: false }),
  ));

  // セクター境界線
  for (let i = 0; i < REGIONS.length; i++) {
    const a = i * SECTOR - Math.PI / 2;  // 12時方向スタート
    scene.add(new THREE.Line(
      new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(0, 0, 0),
        new THREE.Vector3(outerR * Math.cos(a), 0, outerR * Math.sin(a)),
      ]),
      new THREE.LineBasicMaterial({ color: 0x0a2a4a, transparent: true, opacity: 0.3, depthWrite: false }),
    ));
  }
}

// ============================================================
// ノード配置（極座標 + Y 高さ）
// ============================================================

function computeLayout(nodes, centerId) {
  // hop × region ごとにグループ化
  const groups = {};
  nodes.forEach(n => {
    if (n.id === centerId) return;
    const region = REGIONS.includes(n.region) ? n.region : 'APNIC';
    const key = `${n.hop}:${region}`;
    (groups[key] = groups[key] || []).push(n);
  });

  const posMap = { [centerId]: new THREE.Vector3(0, 0, 0) };

  for (const [key, group] of Object.entries(groups)) {
    const [hopStr, region] = key.split(':');
    const hop  = parseInt(hopStr);
    const r    = hop * RING_UNIT;
    const si   = REGIONS.indexOf(region);
    // 12時方向スタート、セクター内に均等配置（両端は少し内側）
    const sStart = si * SECTOR - Math.PI / 2 + 0.10;
    const sEnd   = (si + 1) * SECTOR - Math.PI / 2 - 0.10;

    group.forEach((node, i) => {
      const t     = group.length > 1 ? i / (group.length - 1) : 0.5;
      const angle = sStart + t * (sEnd - sStart);
      // Y = hop² × HOP_Y_SCALE（放物面） + プレフィックス数による微細な山
      const y     = hopY(hop) + Math.log10(node.prefixes + 2) * PFX_Y_JITTER;
      posMap[node.id] = new THREE.Vector3(
        r * Math.cos(angle),
        y,
        r * Math.sin(angle),
      );
    });
  }
  return posMap;
}

// ============================================================
// ノードレンダリング（カスタム発光シェーダー）
// ============================================================

const NODE_VERT = /* glsl */`
  attribute float aSize;
  attribute vec3  aColor;
  varying   vec3  vColor;
  void main() {
    vColor = aColor;
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    gl_PointSize = aSize * (360.0 / -mv.z);
    gl_Position  = projectionMatrix * mv;
  }
`;

const NODE_FRAG = /* glsl */`
  varying vec3 vColor;
  void main() {
    vec2  uv   = gl_PointCoord - 0.5;
    float r    = length(uv);
    if (r > 0.5) discard;
    float core = smoothstep(0.18, 0.0, r);
    float halo = 1.0 - smoothstep(0.0, 0.5, r);
    gl_FragColor = vec4(vColor * (1.0 + core * 2.2), halo * 0.94);
  }
`;

function buildNodes(scene, nodes, posMap, centerId) {
  const N      = nodes.length;
  const posArr = new Float32Array(N * 3);
  const colArr = new Float32Array(N * 3);
  const szArr  = new Float32Array(N);

  nodes.forEach((node, i) => {
    const p = posMap[node.id] ?? new THREE.Vector3();
    posArr[i * 3] = p.x; posArr[i * 3 + 1] = p.y; posArr[i * 3 + 2] = p.z;

    const isCenter = node.id === centerId;
    const col = isCenter
      ? new THREE.Color(0xffffff)
      : (REGION_COLORS[node.region] ?? REGION_COLORS.PRIVATE);
    colArr[i * 3] = col.r; colArr[i * 3 + 1] = col.g; colArr[i * 3 + 2] = col.b;

    szArr[i] = isCenter
      ? 32
      : Math.log10(node.prefixes + 2) * 5.5 + 2.5;
  });

  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(posArr, 3));
  geo.setAttribute('aColor',   new THREE.Float32BufferAttribute(colArr, 3));
  geo.setAttribute('aSize',    new THREE.Float32BufferAttribute(szArr, 1));

  scene.add(new THREE.Points(geo, new THREE.ShaderMaterial({
    vertexShader: NODE_VERT, fragmentShader: NODE_FRAG,
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
  })));
}

// ============================================================
// エッジ（隣接リング間のみ）
// ============================================================

function buildEdges(scene, edges, nodes, posMap) {
  const hopOf    = {};
  const regionOf = {};  // O(1) lookup（以前は edges ループ内で nodes.find() → O(n×m)）
  nodes.forEach(n => {
    hopOf[n.id]    = n.hop ?? 0;
    regionOf[n.id] = n.region ?? 'PRIVATE';
  });

  const positions = [];
  const colors    = [];

  for (const e of edges) {
    const s = posMap[e.source], t = posMap[e.target];
    if (!s || !t) continue;
    // ホップ差が 1 以内のエッジのみ描画（視覚的な繁雑さを抑制）
    if (Math.abs((hopOf[e.source] ?? 0) - (hopOf[e.target] ?? 0)) > 1) continue;

    positions.push(s.x, s.y, s.z, t.x, t.y, t.z);
    // エッジ色: 源ノード色を薄く
    const srcRegion = regionOf[e.source] ?? 'PRIVATE';
    const col = REGION_COLORS[srcRegion] ?? REGION_COLORS.PRIVATE;
    colors.push(col.r * 0.18, col.g * 0.18, col.b * 0.18,
                col.r * 0.08, col.g * 0.08, col.b * 0.08);
  }

  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geo.setAttribute('color',    new THREE.Float32BufferAttribute(colors, 3));

  scene.add(new THREE.LineSegments(geo, new THREE.LineBasicMaterial({
    vertexColors: true, transparent: true, opacity: 0.6,
    blending: THREE.AdditiveBlending, depthWrite: false,
  })));
}

// ============================================================
// 波紋システム（中心から広がる同心波）
// ============================================================

function createRippleSystem(scene, maxHops) {
  const maxR    = (maxHops + 0.4) * RING_UNIT;
  const SPEED   = 75;    // units/sec
  const INTERVAL = 1.8;  // sec

  const ripples = [];
  let timer = 0;

  function spawnRipple(initScale = 0.5) {
    const N   = 128;
    const pos = new Float32Array(N * 3);
    for (let i = 0; i < N; i++) {
      const a = (i / N) * Math.PI * 2;
      pos[i * 3] = Math.cos(a); pos[i * 3 + 1] = 0; pos[i * 3 + 2] = Math.sin(a);
    }
    const geo  = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
    const mat  = new THREE.LineBasicMaterial({
      color: 0x4488cc, transparent: true, opacity: 0.7,
      blending: THREE.AdditiveBlending, depthWrite: false,
    });
    const loop = new THREE.LineLoop(geo, mat);
    scene.add(loop);
    ripples.push({ loop, mat, scale: initScale });
  }

  // 最初から数波を分散配置
  for (let i = 0; i < 5; i++) spawnRipple((i / 5) * maxR);

  function update(dt) {
    timer += dt;
    if (timer >= INTERVAL) { spawnRipple(); timer = 0; }

    for (let i = ripples.length - 1; i >= 0; i--) {
      const rp = ripples[i];
      rp.scale += SPEED * dt;
      const fade = Math.max(0, 1 - rp.scale / maxR);
      rp.loop.scale.setScalar(rp.scale);
      rp.mat.opacity = 0.7 * fade;
      if (fade <= 0.01) {
        scene.remove(rp.loop);
        rp.loop.geometry.dispose();
        rp.mat.dispose();
        ripples.splice(i, 1);
      }
    }
  }

  return { update };
}

// ============================================================
// ポストプロセス（Bloom）
// ============================================================

function setupPostProcessing(renderer, scene, camera) {
  composer = new EffectComposer(renderer);
  composer.addPass(new RenderPass(scene, camera));
  const bloom = new UnrealBloomPass(
    new THREE.Vector2(innerWidth, innerHeight),
    0, 0.55, 0.08,   // デフォルト OFF（[B] でトグル）
  );
  composer.addPass(bloom);
  composer.addPass(new OutputPass());
  return { composer, bloom };
}

// ============================================================
// メイン
// ============================================================

async function main() {
  setStatus('グラフデータを読み込み中...', 'ring_graph.json');
  let graph;
  try {
    const res = await fetch('ring_graph.json');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    graph = await res.json();
  } catch {
    setStatus(
      'エラー: ring_graph.json が見つかりません',
      'まず  python scripts/fetch_rings.py  を実行してください',
    );
    return;
  }

  setStatus(
    '年輪レイアウトを計算中...',
    `AS${graph.center} を中心に ${graph.nodes.length} ノードを配置`,
  );
  await new Promise(r => setTimeout(r, 30));

  const posMap = computeLayout(graph.nodes, graph.center);

  setStatus('年輪を描画中...');
  const { scene, camera, renderer } = initScene();
  createStarField(scene);
  createGuides(scene, graph.max_hops ?? 6);
  buildNodes(scene, graph.nodes, posMap, graph.center);
  buildEdges(scene, graph.edges, graph.nodes, posMap);
  const ripples = createRippleSystem(scene, graph.max_hops ?? 6);
  const { bloom } = setupPostProcessing(renderer, scene, camera);

  // OrbitControls
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping   = true;
  controls.dampingFactor   = 0.05;
  controls.autoRotate      = true;
  controls.autoRotateSpeed = 0.25;
  controls.target.set(0, 400, 0);
  controls.minDistance = 80;
  controls.maxDistance = 3000;

  // キーボード
  window.addEventListener('keydown', e => {
    if (e.key === 'r' || e.key === 'R') {
      camera.position.set(0, 250, 1100);
      controls.target.set(0, 400, 0);
      controls.update();
    }
    if (e.key === 'b' || e.key === 'B') {
      bloom.strength = bloom.strength > 0.1 ? 0 : 1.7;  // 0 ↔ 1.7 トグル
    }
  });

  // 統計 UI を更新
  const byHop    = {};
  const byRegion = {};
  graph.nodes.forEach(n => {
    byHop[n.hop] = (byHop[n.hop] ?? 0) + 1;
    byRegion[n.region] = (byRegion[n.region] ?? 0) + 1;
  });

  const el = id => document.getElementById(id);
  el('stat-center').textContent  = `AS${graph.center}`;
  el('stat-nodes').textContent   = graph.nodes.length.toLocaleString();
  el('stat-hops').textContent    = graph.max_hops ?? 6;
  el('stat-hop1').textContent    = (byHop[1] ?? 0).toLocaleString();
  el('stat-hop2').textContent    = (byHop[2] ?? 0).toLocaleString();
  el('cnt-ripe').textContent     = (byRegion['RIPE']    ?? 0).toLocaleString();
  el('cnt-arin').textContent     = (byRegion['ARIN']    ?? 0).toLocaleString();
  el('cnt-apnic').textContent    = (byRegion['APNIC']   ?? 0).toLocaleString();
  el('cnt-lacnic').textContent   = (byRegion['LACNIC']  ?? 0).toLocaleString();
  el('cnt-afrinic').textContent  = (byRegion['AFRINIC'] ?? 0).toLocaleString();

  hideLoading();

  const clock = new THREE.Clock();
  (function animate() {
    requestAnimationFrame(animate);
    const dt = Math.min(clock.getDelta(), 0.05);
    controls.update();
    ripples.update(dt);
    composer.render();
  })();
}

main();
