/* ==========================================================================
   放空小站 3D 动效与物理交互引擎（零外部依赖 / Canvas 3D 投影 + CSS 3D 倾斜）
   - 3 种可切换 3D 模型：crystal（能量晶核）/ planet（放空星球）/ coffee（悬浮咖啡）
   - 拖拽 360° 惯性旋转、鼠标视差跟随、点击 3D 冲击波 + 粒子脉冲 + 解压浮字
   - 放空中实时 3D 进度光环 + 4 秒舒缓呼吸节律；已回来 / 解锁成就 3D 礼花爆发
   - 仅在放空小站打开时运行 rAF，关闭即停，后台 0 CPU/GPU 占用
   ========================================================================== */
(function () {
  const TAU = Math.PI * 2;
  const MODELS = [
    { id: 'crystal', label: '💎 晶核' },
    { id: 'planet', label: '🪐 星球' },
    { id: 'coffee', label: '☕ 咖啡' },
  ];
  const MOODS_IDLE = ['深呼吸 ✨', '摸鱼蓄力 ⚡', '脑子散热中 ❄️', '灵感 +1 💡', '去走两步 🚶', '歇会儿不亏 ☕'];
  const MOODS_ACTIVE = ['正在回血 💚', '电量 +5% 🔋', '松弛感拉满 🌿', '大脑清缓存 ✨', '眺望远方 🌤️', '神清气爽 🍃'];
  const MOODS_OVER = ['该回来搬砖啦 🔔', '续杯时间到 ☕', '精力满格啦 ⚡', '回来打卡 ✅'];

  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const lerp = (a, b, t) => a + (b - a) * t;
  const isDark = () => document.documentElement.dataset.theme === 'dark';

  // ---------------------------------------------------------------- 3D 数学
  function rot3(p, rx, ry, rz) {
    let x = p[0], y = p[1], z = p[2];
    if (rz) {
      const cz = Math.cos(rz), sz = Math.sin(rz);
      const nx = x * cz - y * sz, ny = x * sz + y * cz;
      x = nx; y = ny;
    }
    const cy = Math.cos(ry), sy = Math.sin(ry);
    const x1 = x * cy + z * sy;
    const z1 = -x * sy + z * cy;
    const cx = Math.cos(rx), sx = Math.sin(rx);
    const y2 = y * cx - z1 * sx;
    const z2 = y * sx + z1 * cx;
    return [x1, y2, z2];
  }

  function proj(p, cx, cy, scale, fov) {
    const d = fov / (fov + p[2]);
    return [cx + p[0] * scale * d, cy + p[1] * scale * d, d, p[2]];
  }

  // ---------------------------------------------------------------- 几何生成
  // 1) 二十面体（晶核）
  const PHI = (1 + Math.sqrt(5)) / 2;
  const ICO_VERTS = [
    [-1, PHI, 0], [1, PHI, 0], [-1, -PHI, 0], [1, -PHI, 0],
    [0, -1, PHI], [0, 1, PHI], [0, -1, -PHI], [0, 1, -PHI],
    [PHI, 0, -1], [PHI, 0, 1], [-PHI, 0, -1], [-PHI, 0, 1],
  ].map((v) => {
    const l = Math.hypot(v[0], v[1], v[2]);
    return [v[0] / l, v[1] / l, v[2] / l];
  });
  const ICO_FACES = [
    [0, 11, 5], [0, 5, 1], [0, 1, 7], [0, 7, 10], [0, 10, 11],
    [1, 5, 9], [5, 11, 4], [11, 10, 2], [10, 7, 6], [7, 1, 8],
    [3, 9, 4], [3, 4, 2], [3, 2, 6], [3, 6, 8], [3, 8, 9],
    [4, 9, 5], [2, 4, 11], [6, 2, 10], [8, 6, 7], [9, 8, 1],
  ];

  // 2) 放空星球（斐波那契球面点阵 + 经纬线）
  const PLANET_DOTS = (() => {
    const pts = [];
    const n = 130;
    const ga = Math.PI * (3 - Math.sqrt(5));
    for (let i = 0; i < n; i++) {
      const y = 1 - (i / (n - 1)) * 2;
      const r = Math.sqrt(Math.max(0, 1 - y * y));
      const th = ga * i;
      pts.push([Math.cos(th) * r * 0.92, y * 0.92, Math.sin(th) * r * 0.92]);
    }
    return pts;
  })();

  // 3) 悬浮咖啡杯（圆台杯身 + 把手 + 碟子）
  function buildCoffeeGeom() {
    const rings = [
      { y: -0.62, r: 0.72 }, // 杯口
      { y: -0.10, r: 0.66 },
      { y: 0.42, r: 0.54 },
      { y: 0.64, r: 0.44 },  // 杯底
    ];
    const segs = 16;
    const verts = [];
    for (const rg of rings) {
      for (let i = 0; i < segs; i++) {
        const a = (i / segs) * TAU;
        verts.push([Math.cos(a) * rg.r, rg.y, Math.sin(a) * rg.r]);
      }
    }
    const quads = [];
    for (let r = 0; r < rings.length - 1; r++) {
      for (let i = 0; i < segs; i++) {
        const ni = (i + 1) % segs;
        const a = r * segs + i, b = r * segs + ni;
        const c = (r + 1) * segs + ni, d = (r + 1) * segs + i;
        quads.push([a, b, c, d]);
      }
    }
    // 把手弧线
    const handle = [];
    for (let i = 0; i <= 12; i++) {
      const t = -0.82 + (i / 12) * 1.64;
      const hx = 0.63 + Math.cos(t) * 0.34;
      const hy = -0.04 + Math.sin(t) * 0.42;
      handle.push([hx, hy, 0]);
    }
    // 咖啡液面
    const liquid = [];
    for (let i = 0; i < segs; i++) {
      const a = (i / segs) * TAU;
      liquid.push([Math.cos(a) * 0.63, -0.52, Math.sin(a) * 0.63]);
    }
    // 底部圆碟
    const saucer = [];
    for (let i = 0; i < segs; i++) {
      const a = (i / segs) * TAU;
      saucer.push([Math.cos(a) * 0.96, 0.74, Math.sin(a) * 0.96]);
    }
    return { verts, quads, handle, liquid, saucer, segs };
  }
  const COFFEE = buildCoffeeGeom();

  // ---------------------------------------------------------------- 主题配色
  function getPalette(mode, dark) {
    if (mode === 'over') {
      return dark
        ? { c1: [255, 118, 122], c2: [255, 184, 77], glow: 'rgba(255,108,112,.38)', ring: 'rgba(255,170,92,.85)', bgGlow: 'rgba(255,95,105,.18)' }
        : { c1: [229, 72, 77], c2: [245, 158, 11], glow: 'rgba(229,72,77,.28)', ring: 'rgba(235,112,82,.85)', bgGlow: 'rgba(229,72,77,.12)' };
    }
    if (mode === 'active') {
      return dark
        ? { c1: [56, 224, 146], c2: [56, 189, 248], glow: 'rgba(52,211,153,.42)', ring: 'rgba(84,238,186,.9)', bgGlow: 'rgba(18,185,129,.2)' }
        : { c1: [16, 172, 108], c2: [14, 165, 233], glow: 'rgba(16,172,108,.3)', ring: 'rgba(16,185,129,.9)', bgGlow: 'rgba(16,172,108,.13)' };
    }
    return dark
      ? { c1: [106, 156, 255], c2: [168, 130, 255], glow: 'rgba(106,156,255,.42)', ring: 'rgba(130,174,255,.88)', bgGlow: 'rgba(99,102,241,.2)' }
      : { c1: [47, 107, 255], c2: [124, 92, 255], glow: 'rgba(47,107,255,.28)', ring: 'rgba(79,124,255,.85)', bgGlow: 'rgba(47,107,255,.12)' };
  }

  function rgba(rgb, a) {
    return `rgba(${Math.round(rgb[0])},${Math.round(rgb[1])},${Math.round(rgb[2])},${a.toFixed(3)})`;
  }

  // ---------------------------------------------------------------- 3D 舞台引擎
  const stage = {
    canvas: null,
    ctx: null,
    w: 210,
    h: 170,
    running: false,
    rafId: 0,
    lastT: 0,
    t: 0,
    model: localStorage.getItem('bp3dModel') || 'crystal',
    mode: 'idle', // idle | active | over
    progress: 0,  // 0..1+
    rx: 0.32,
    ry: 0.4,
    vx: 0,
    vy: 0.55,
    targetTiltX: 0,
    targetTiltY: 0,
    dragging: false,
    dragMoved: 0,
    lastPtrX: 0,
    lastPtrY: 0,
    pulse: 0,
    waves: [],
    sparks: [],
    floats: [],
    ambient: [],
    col1: [47, 107, 255],
    col2: [124, 92, 255],
  };

  if (!MODELS.some((m) => m.id === stage.model)) stage.model = 'crystal';

  // 初始化环境漂浮粒子
  for (let i = 0; i < 34; i++) {
    const ang = Math.random() * TAU;
    const rad = 1.2 + Math.random() * 1.1;
    stage.ambient.push({
      x: Math.cos(ang) * rad,
      y: (Math.random() - 0.5) * 2.2,
      z: Math.sin(ang) * rad,
      s: 1.1 + Math.random() * 1.8,
      sp: 0.25 + Math.random() * 0.5,
      ph: Math.random() * TAU,
    });
  }

  function resizeStage() {
    if (!stage.canvas) return;
    const rect = stage.canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    stage.w = rect.width;
    stage.h = rect.height;
    const pw = Math.round(rect.width * dpr);
    const ph = Math.round(rect.height * dpr);
    if (stage.canvas.width !== pw || stage.canvas.height !== ph) {
      stage.canvas.width = pw;
      stage.canvas.height = ph;
    }
    stage.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function triggerPulse(strength = 1.0, moodText = null) {
    stage.pulse = Math.min(1.6, stage.pulse + 0.85 * strength);
    stage.vy += (Math.random() > 0.5 ? 1 : -1) * (1.4 + 0.9 * strength);
    stage.vx += (Math.random() - 0.5) * 0.9 * strength;
    stage.waves.push({ r: 0.45, maxR: 2.35, a: 0.95 });
    const cnt = Math.round(14 * strength);
    for (let i = 0; i < cnt; i++) {
      const th = Math.random() * TAU;
      const ph = Math.acos(2 * Math.random() - 1);
      const sp = 1.4 + Math.random() * 2.4;
      stage.sparks.push({
        x: 0, y: 0, z: 0,
        vx: Math.sin(ph) * Math.cos(th) * sp,
        vy: Math.cos(ph) * sp,
        vz: Math.sin(ph) * Math.sin(th) * sp,
        life: 0.7 + Math.random() * 0.55,
        maxLife: 1.25,
        size: 1.8 + Math.random() * 2.2,
      });
    }
    if (moodText !== false) {
      const pool = stage.mode === 'over' ? MOODS_OVER : stage.mode === 'active' ? MOODS_ACTIVE : MOODS_IDLE;
      const txt = typeof moodText === 'string' ? moodText : pool[Math.floor(Math.random() * pool.length)];
      stage.floats.push({
        txt,
        x: stage.w * 0.5 + (Math.random() - 0.5) * 56,
        y: stage.h * 0.44 + (Math.random() - 0.5) * 20,
        vy: -28 - Math.random() * 14,
        life: 1.35,
        maxLife: 1.35,
      });
    }
  }

  function drawRing3D(ctx, cx, cy, scale, fov, radius, tiltX, tiltY, rotSpin, segs, stroke, width, progress = 1) {
    ctx.beginPath();
    let started = false;
    const steps = Math.max(4, Math.floor(segs * clamp(progress, 0, 1)));
    for (let i = 0; i <= steps; i++) {
      const a = (i / segs) * TAU - Math.PI / 2 + rotSpin;
      const p0 = [Math.cos(a) * radius, Math.sin(a) * radius, 0];
      const p1 = rot3(p0, tiltX, tiltY, 0);
      const pp = proj(p1, cx, cy, scale, fov);
      if (!started) { ctx.moveTo(pp[0], pp[1]); started = true; }
      else ctx.lineTo(pp[0], pp[1]);
    }
    ctx.strokeStyle = stroke;
    ctx.lineWidth = width;
    ctx.lineCap = 'round';
    ctx.stroke();
  }

  function renderCrystal(ctx, cx, cy, scale, fov, rx, ry, breath, c1, c2, dark) {
    const rScale = scale * (0.94 + breath * 0.06 + stage.pulse * 0.1);
    const tVerts = ICO_VERTS.map((v) => rot3(v, rx, ry, 0));
    const pVerts = tVerts.map((v) => proj(v, cx, cy, rScale, fov));

    // 双陀螺轨道环
    drawRing3D(ctx, cx, cy, scale, fov, 1.34, rx * 0.6 + 1.05, ry * 0.7, stage.t * 0.9, 54, rgba(c1, dark ? 0.42 : 0.34), 1.4, 1);
    drawRing3D(ctx, cx, cy, scale, fov, 1.48, -rx * 0.5 + 0.5, ry * 0.5 + 1.1, -stage.t * 0.65, 54, rgba(c2, dark ? 0.35 : 0.28), 1.2, 1);

    // 面片按深度排序
    const faces = ICO_FACES.map((f) => {
      const a = tVerts[f[0]], b = tVerts[f[1]], c = tVerts[f[2]];
      const ux = b[0] - a[0], uy = b[1] - a[1], uz = b[2] - a[2];
      const vx = c[0] - a[0], vy = c[1] - a[1], vz = c[2] - a[2];
      const nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
      const nl = Math.hypot(nx, ny, nz) || 1;
      const zAvg = (a[2] + b[2] + c[2]) / 3;
      // 光源方向 (-0.45, -0.65, -0.6)
      const light = clamp((-nx * 0.45 - ny * 0.65 - nz * 0.62) / nl, 0, 1);
      return { f, zAvg, nz: nz / nl, light };
    }).sort((a, b) => b.zAvg - a.zAvg);

    for (const fc of faces) {
      const pa = pVerts[fc.f[0]], pb = pVerts[fc.f[1]], pc = pVerts[fc.f[2]];
      ctx.beginPath();
      ctx.moveTo(pa[0], pa[1]);
      ctx.lineTo(pb[0], pb[1]);
      ctx.lineTo(pc[0], pc[1]);
      ctx.closePath();

      const mix = clamp(fc.light * 0.85 + 0.15, 0, 1);
      const col = [
        lerp(c2[0], c1[0], mix) + fc.light * (dark ? 45 : 35),
        lerp(c2[1], c1[1], mix) + fc.light * (dark ? 45 : 35),
        lerp(c2[2], c1[2], mix) + fc.light * (dark ? 45 : 35),
      ];
      const front = fc.nz < 0.08;
      ctx.fillStyle = rgba(col, front ? (dark ? 0.22 + fc.light * 0.34 : 0.18 + fc.light * 0.3) : 0.07);
      ctx.fill();
      ctx.strokeStyle = rgba(col, front ? (dark ? 0.72 : 0.64) : 0.18);
      ctx.lineWidth = front ? 1.15 : 0.7;
      ctx.stroke();
    }

    // 顶点高光星点
    for (let i = 0; i < pVerts.length; i++) {
      if (tVerts[i][2] < 0.25) {
        const p = pVerts[i];
        ctx.beginPath();
        ctx.arc(p[0], p[1], 1.7 + (0.3 - tVerts[i][2]) * 1.4, 0, TAU);
        ctx.fillStyle = dark ? 'rgba(255,255,255,.85)' : rgba(c1, 0.82);
        ctx.fill();
      }
    }
  }

  function renderPlanet(ctx, cx, cy, scale, fov, rx, ry, breath, c1, c2, dark) {
    const rScale = scale * (0.95 + breath * 0.04 + stage.pulse * 0.08);
    // 球体柔和内核渐变
    const grad = ctx.createRadialGradient(cx - rScale * 0.28, cy - rScale * 0.28, rScale * 0.1, cx, cy, rScale * 0.96);
    grad.addColorStop(0, rgba(c1, dark ? 0.36 : 0.28));
    grad.addColorStop(0.65, rgba(c2, dark ? 0.18 : 0.14));
    grad.addColorStop(1, rgba(c2, 0.02));
    ctx.beginPath();
    ctx.arc(cx, cy, rScale * 0.92, 0, TAU);
    ctx.fillStyle = grad;
    ctx.fill();

    // 土星环（后半 + 前半）
    const ringTiltX = 1.18 + rx * 0.25;
    const ringTiltY = 0.28 + Math.sin(stage.t * 0.4) * 0.08;
    const backPts = [], frontPts = [];
    const segs = 64;
    for (let i = 0; i <= segs; i++) {
      const a = (i / segs) * TAU;
      const p1 = rot3([Math.cos(a) * 1.52, Math.sin(a) * 1.52, 0], ringTiltX, ringTiltY, 0);
      const pp = proj(p1, cx, cy, scale, fov);
      (p1[2] > 0 ? backPts : frontPts).push(pp);
    }
    const drawSegs = (arr, alpha) => {
      if (arr.length < 2) return;
      ctx.beginPath();
      for (let i = 0; i < arr.length; i++) {
        if (i === 0 || Math.hypot(arr[i][0] - arr[i - 1][0], arr[i][1] - arr[i - 1][1]) > 40) {
          ctx.moveTo(arr[i][0], arr[i][1]);
        } else {
          ctx.lineTo(arr[i][0], arr[i][1]);
        }
      }
      ctx.strokeStyle = rgba(c2, alpha);
      ctx.lineWidth = 3.2;
      ctx.lineCap = 'round';
      ctx.stroke();
    };
    drawSegs(backPts, dark ? 0.28 : 0.22);

    // 球面点阵
    const dots = PLANET_DOTS.map((pt) => {
      const r = rot3(pt, rx, ry, 0);
      const p = proj(r, cx, cy, rScale, fov);
      return { p, z: r[2], y: r[1] };
    }).sort((a, b) => b.z - a.z);

    for (const d of dots) {
      const f = clamp((1 - d.z) * 0.5, 0.08, 1);
      const col = [lerp(c2[0], c1[0], (d.y + 1) * 0.5), lerp(c2[1], c1[1], (d.y + 1) * 0.5), lerp(c2[2], c1[2], (d.y + 1) * 0.5)];
      ctx.beginPath();
      ctx.arc(d.p[0], d.p[1], 1.1 + f * 1.9, 0, TAU);
      ctx.fillStyle = rgba(col, 0.14 + f * (dark ? 0.82 : 0.72));
      ctx.fill();
    }

    drawSegs(frontPts, dark ? 0.78 : 0.65);

    // 绕行小卫星
    const ma = stage.t * 1.25;
    const mp = rot3([Math.cos(ma) * 1.52, Math.sin(ma) * 1.52, 0], ringTiltX, ringTiltY, 0);
    const mpp = proj(mp, cx, cy, scale, fov);
    ctx.beginPath();
    ctx.arc(mpp[0], mpp[1], 4.2 * mpp[2], 0, TAU);
    ctx.fillStyle = dark ? '#fff' : rgba(c1, 0.95);
    ctx.shadowColor = rgba(c1, 0.8);
    ctx.shadowBlur = 10;
    ctx.fill();
    ctx.shadowBlur = 0;
  }

  function renderCoffee(ctx, cx, cy, scale, fov, rx, ry, breath, c1, c2, dark) {
    const hoverY = Math.sin(stage.t * 2.1) * 0.08;
    const rScale = scale * (0.94 + stage.pulse * 0.08);
    const clampRx = clamp(rx, -0.55, 0.75);
    const tf = (v) => rot3([v[0], v[1] + hoverY, v[2]], clampRx, ry, 0);

    // 碟子
    const sPts = COFFEE.saucer.map((v) => proj(tf(v), cx, cy, rScale, fov));
    ctx.beginPath();
    sPts.forEach((p, i) => (i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])));
    ctx.closePath();
    ctx.fillStyle = rgba(c2, dark ? 0.16 : 0.12);
    ctx.fill();
    ctx.strokeStyle = rgba(c2, dark ? 0.52 : 0.45);
    ctx.lineWidth = 1.3;
    ctx.stroke();

    // 杯身面片
    const tv = COFFEE.verts.map(tf);
    const pv = tv.map((v) => proj(v, cx, cy, rScale, fov));
    const quads = COFFEE.quads.map((q) => {
      const z = (tv[q[0]][2] + tv[q[1]][2] + tv[q[2]][2] + tv[q[3]][2]) * 0.25;
      const a = tv[q[0]], b = tv[q[1]], c = tv[q[2]];
      const nx = (b[1] - a[1]) * (c[2] - a[2]) - (b[2] - a[2]) * (c[1] - a[1]);
      const nz = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
      const light = clamp((-nx * 0.55 - nz * 0.65) * 1.4 + 0.35, 0.1, 1);
      return { q, z, light };
    }).sort((a, b) => b.z - a.z);

    for (const qd of quads) {
      const q = qd.q;
      ctx.beginPath();
      ctx.moveTo(pv[q[0]][0], pv[q[0]][1]);
      ctx.lineTo(pv[q[1]][0], pv[q[1]][1]);
      ctx.lineTo(pv[q[2]][0], pv[q[2]][1]);
      ctx.lineTo(pv[q[3]][0], pv[q[3]][1]);
      ctx.closePath();
      const col = [lerp(c2[0], c1[0], qd.light), lerp(c2[1], c1[1], qd.light), lerp(c2[2], c1[2], qd.light)];
      ctx.fillStyle = rgba(col, dark ? 0.14 + qd.light * 0.28 : 0.12 + qd.light * 0.24);
      ctx.fill();
      ctx.strokeStyle = rgba(col, dark ? 0.52 : 0.44);
      ctx.lineWidth = 0.9;
      ctx.stroke();
    }

    // 咖啡液面
    const lPts = COFFEE.liquid.map((v) => proj(tf(v), cx, cy, rScale, fov));
    ctx.beginPath();
    lPts.forEach((p, i) => (i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])));
    ctx.closePath();
    ctx.fillStyle = dark ? 'rgba(130, 82, 48, .65)' : 'rgba(122, 74, 38, .52)';
    ctx.fill();
    ctx.strokeStyle = rgba(c1, 0.7);
    ctx.lineWidth = 1.2;
    ctx.stroke();

    // 把手
    const hPts = COFFEE.handle.map((v) => proj(tf(v), cx, cy, rScale, fov));
    ctx.beginPath();
    hPts.forEach((p, i) => (i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])));
    ctx.strokeStyle = rgba(c1, dark ? 0.88 : 0.82);
    ctx.lineWidth = 3.6;
    ctx.lineCap = 'round';
    ctx.stroke();

    // 3 缕 3D 螺旋上升热气
    for (let s = 0; s < 3; s++) {
      ctx.beginPath();
      const baseX = (s - 1) * 0.24;
      const baseZ = (s === 1 ? -0.15 : 0.14);
      for (let i = 0; i <= 14; i++) {
        const u = i / 14;
        const ph = stage.t * 2.6 + s * 2.1 - u * 4.2;
        const sx = baseX + Math.sin(ph) * (0.08 + u * 0.1);
        const sy = -0.68 - u * (0.78 + breath * 0.12);
        const sz = baseZ + Math.cos(ph) * 0.08;
        const pp = proj(tf([sx, sy, sz]), cx, cy, rScale, fov);
        if (i === 0) ctx.moveTo(pp[0], pp[1]);
        else ctx.lineTo(pp[0], pp[1]);
      }
      ctx.strokeStyle = rgba(c1, dark ? 0.55 - s * 0.08 : 0.48 - s * 0.08);
      ctx.lineWidth = 1.9;
      ctx.lineCap = 'round';
      ctx.stroke();
    }
  }

  function frame(ts) {
    if (!stage.running) return;
    stage.rafId = requestAnimationFrame(frame);
    const dt = stage.lastT ? Math.min(0.05, (ts - stage.lastT) / 1000) : 0.016;
    stage.lastT = ts;
    stage.t += dt;

    const ctx = stage.ctx;
    if (!ctx) return;
    resizeStage();
    const w = stage.w, h = stage.h;
    const cx = w * 0.5, cy = h * 0.5;
    const scale = Math.min(w, h) * 0.3;
    const fov = 5.2;
    const dark = isDark();

    // 平滑插值配色
    const pal = getPalette(stage.mode, dark);
    for (let i = 0; i < 3; i++) {
      stage.col1[i] = lerp(stage.col1[i], pal.c1[i], dt * 5);
      stage.col2[i] = lerp(stage.col2[i], pal.c2[i], dt * 5);
    }

    // 物理旋转与惯性衰减
    if (!stage.dragging) {
      const baseSpin = stage.mode === 'active' ? 0.42 : stage.mode === 'over' ? 0.85 : 0.55;
      stage.vy = lerp(stage.vy, baseSpin, dt * 2.2);
      stage.vx = lerp(stage.vx, 0, dt * 3.0);
      stage.ry += stage.vy * dt;
      stage.rx = lerp(stage.rx + stage.vx * dt, 0.28 + stage.targetTiltX * 0.35, dt * 2.5);
    }
    stage.pulse = Math.max(0, stage.pulse - dt * 2.2);

    ctx.clearRect(0, 0, w, h);

    // 背景体积光晕
    const breath = (Math.sin(stage.t * (stage.mode === 'active' ? 1.57 : 2.1)) + 1) * 0.5;
    const glowR = scale * (1.75 + breath * 0.22 + stage.pulse * 0.45);
    const bg = ctx.createRadialGradient(cx, cy, scale * 0.1, cx, cy, glowR);
    bg.addColorStop(0, rgba(stage.col1, (dark ? 0.3 : 0.2) + stage.pulse * 0.14));
    bg.addColorStop(0.55, rgba(stage.col2, dark ? 0.11 : 0.07));
    bg.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, w, h);

    // 3D 环境漂浮微粒（放空中缓慢上升如星火/热气）
    for (const pt of stage.ambient) {
      const py = stage.mode === 'active'
        ? ((pt.y - stage.t * pt.sp * 0.35 + 1.2) % 2.4 + 2.4) % 2.4 - 1.2
        : pt.y + Math.sin(stage.t * pt.sp + pt.ph) * 0.12;
      const r = rot3([pt.x, py, pt.z], stage.rx * 0.3, stage.ry * 0.45 + stage.t * 0.15, 0);
      const p = proj(r, cx, cy, scale, fov);
      const a = clamp((1.5 - r[2]) * 0.22, 0.05, 0.55);
      ctx.beginPath();
      ctx.arc(p[0], p[1], pt.s * p[2], 0, TAU);
      ctx.fillStyle = rgba(stage.col1, a);
      ctx.fill();
    }

    // 放空中：外圈 3D 实时进度光环
    if (stage.mode === 'active' || stage.mode === 'over') {
      drawRing3D(ctx, cx, cy, scale, fov, 1.66, 0.22 + stage.targetTiltX * 0.15, stage.targetTiltY * 0.15, 0, 72,
        dark ? 'rgba(255,255,255,.12)' : 'rgba(15,23,42,.1)', 3.2, 1);
      drawRing3D(ctx, cx, cy, scale, fov, 1.66, 0.22 + stage.targetTiltX * 0.15, stage.targetTiltY * 0.15, 0, 72,
        pal.ring, 3.6, clamp(stage.progress, 0.03, 1));
    }

    // 绘制核心 3D 模型
    const ryEff = stage.ry + stage.targetTiltY * 0.35;
    if (stage.model === 'planet') {
      renderPlanet(ctx, cx, cy, scale, fov, stage.rx, ryEff, breath, stage.col1, stage.col2, dark);
    } else if (stage.model === 'coffee') {
      renderCoffee(ctx, cx, cy, scale, fov, stage.rx, ryEff, breath, stage.col1, stage.col2, dark);
    } else {
      renderCrystal(ctx, cx, cy, scale, fov, stage.rx, ryEff, breath, stage.col1, stage.col2, dark);
    }

    // 点击/状态切换 3D 冲击波环
    for (let i = stage.waves.length - 1; i >= 0; i--) {
      const wv = stage.waves[i];
      wv.r += dt * 3.1;
      wv.a = Math.max(0, 1 - (wv.r - 0.45) / (wv.maxR - 0.45));
      if (wv.a <= 0.01) { stage.waves.splice(i, 1); continue; }
      drawRing3D(ctx, cx, cy, scale, fov, wv.r, stage.rx * 0.5, stage.ry * 0.5, 0, 48,
        rgba(stage.col1, wv.a * 0.75), 2.4 * wv.a + 0.5, 1);
    }

    // 3D 能量火花粒子
    for (let i = stage.sparks.length - 1; i >= 0; i--) {
      const sp = stage.sparks[i];
      sp.life -= dt;
      if (sp.life <= 0) { stage.sparks.splice(i, 1); continue; }
      sp.x += sp.vx * dt;
      sp.y += sp.vy * dt;
      sp.z += sp.vz * dt;
      sp.vx *= 0.94; sp.vy *= 0.94; sp.vz *= 0.94;
      const p = proj([sp.x, sp.y, sp.z], cx, cy, scale, fov);
      const a = clamp(sp.life / sp.maxLife, 0, 1);
      ctx.beginPath();
      ctx.arc(p[0], p[1], sp.size * p[2] * a, 0, TAU);
      ctx.fillStyle = rgba(stage.col1, a * 0.95);
      ctx.fill();
    }

    // 解压浮字气泡
    if (stage.floats.length) {
      ctx.font = '600 12px "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif';
      ctx.textAlign = 'center';
      for (let i = stage.floats.length - 1; i >= 0; i--) {
        const fl = stage.floats[i];
        fl.life -= dt;
        if (fl.life <= 0) { stage.floats.splice(i, 1); continue; }
        fl.y += fl.vy * dt;
        fl.vy *= 0.96;
        const a = clamp(fl.life / (fl.maxLife * 0.55), 0, 1);
        ctx.fillStyle = dark ? `rgba(236,244,255,${a.toFixed(2)})` : `rgba(24,35,64,${a.toFixed(2)})`;
        ctx.fillText(fl.txt, fl.x, fl.y);
      }
    }
  }

  function bindStageEvents(canvas) {
    if (canvas.dataset.bound3d) return;
    canvas.dataset.bound3d = '1';

    canvas.addEventListener('pointerdown', (e) => {
      stage.dragging = true;
      stage.dragMoved = 0;
      stage.lastPtrX = e.clientX;
      stage.lastPtrY = e.clientY;
      canvas.setPointerCapture && canvas.setPointerCapture(e.pointerId);
      canvas.classList.add('grabbing');
    });

    canvas.addEventListener('pointermove', (e) => {
      const rect = canvas.getBoundingClientRect();
      if (stage.dragging) {
        const dx = e.clientX - stage.lastPtrX;
        const dy = e.clientY - stage.lastPtrY;
        stage.lastPtrX = e.clientX;
        stage.lastPtrY = e.clientY;
        stage.dragMoved += Math.abs(dx) + Math.abs(dy);
        stage.ry += dx * 0.014;
        stage.rx = clamp(stage.rx + dy * 0.012, -1.25, 1.25);
        stage.vy = dx * 0.18;
        stage.vx = dy * 0.14;
      } else if (rect.width && rect.height) {
        stage.targetTiltY = ((e.clientX - rect.left) / rect.width - 0.5) * 2;
        stage.targetTiltX = ((e.clientY - rect.top) / rect.height - 0.5) * 2;
      }
    });

    const endDrag = () => {
      if (!stage.dragging) return;
      stage.dragging = false;
      canvas.classList.remove('grabbing');
      if (stage.dragMoved < 6) triggerPulse(1.0);
    };
    canvas.addEventListener('pointerup', endDrag);
    canvas.addEventListener('pointercancel', endDrag);
    canvas.addEventListener('pointerleave', () => {
      stage.targetTiltX = 0;
      stage.targetTiltY = 0;
    });
  }

  // ---------------------------------------------------------------- 3D 礼花庆典层
  const fx = {
    canvas: null,
    ctx: null,
    running: false,
    rafId: 0,
    lastT: 0,
    pieces: [],
  };
  const CONFETTI_COLS = ['#2f6bff', '#12a150', '#f59e0b', '#ec4899', '#8b5cf6', '#06b6d4', '#facc15'];

  function burstConfetti(count = 56, originX = 0.5, originY = 0.32, goldOnly = false) {
    const c = fx.canvas || document.getElementById('bpFxCanvas');
    if (!c) return;
    fx.canvas = c;
    fx.ctx = c.getContext('2d');
    const rect = c.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    c.width = Math.round(rect.width * dpr);
    c.height = Math.round(rect.height * dpr);
    fx.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const ox = rect.width * originX;
    const oy = rect.height * originY;
    const palette = goldOnly ? ['#f59e0b', '#facc15', '#fde047', '#fb923c', '#ffffff'] : CONFETTI_COLS;
    for (let i = 0; i < count; i++) {
      const ang = Math.random() * TAU;
      const spd = 110 + Math.random() * 290;
      fx.pieces.push({
        x: ox + (Math.random() - 0.5) * 24,
        y: oy + (Math.random() - 0.5) * 16,
        vx: Math.cos(ang) * spd,
        vy: Math.sin(ang) * spd - 140,
        rx: Math.random() * TAU,
        ry: Math.random() * TAU,
        rz: Math.random() * TAU,
        vrx: (Math.random() - 0.5) * 14,
        vry: (Math.random() - 0.5) * 14,
        vrz: (Math.random() - 0.5) * 10,
        w: 6 + Math.random() * 5,
        h: 4 + Math.random() * 4,
        col: palette[i % palette.length],
        life: 1.5 + Math.random() * 0.9,
        maxLife: 2.4,
      });
    }
    if (!fx.running) {
      fx.running = true;
      fx.lastT = 0;
      fx.rafId = requestAnimationFrame(fxFrame);
    }
  }

  function fxFrame(ts) {
    if (!fx.running || !fx.ctx) return;
    const dt = fx.lastT ? Math.min(0.05, (ts - fx.lastT) / 1000) : 0.016;
    fx.lastT = ts;
    const rect = fx.canvas.getBoundingClientRect();
    fx.ctx.clearRect(0, 0, rect.width, rect.height);

    for (let i = fx.pieces.length - 1; i >= 0; i--) {
      const p = fx.pieces[i];
      p.life -= dt;
      if (p.life <= 0) { fx.pieces.splice(i, 1); continue; }
      p.vy += 420 * dt;
      p.vx *= 0.985;
      p.x += p.vx * dt;
      p.y += p.vy * dt;
      p.rx += p.vrx * dt;
      p.ry += p.vry * dt;
      p.rz += p.vrz * dt;

      const sx = Math.cos(p.ry);
      const sy = Math.cos(p.rx);
      const alpha = clamp(p.life / 0.55, 0, 1);
      fx.ctx.save();
      fx.ctx.translate(p.x, p.y);
      fx.ctx.rotate(p.rz);
      fx.ctx.scale(Math.max(0.18, Math.abs(sx)), Math.max(0.18, Math.abs(sy)));
      fx.ctx.globalAlpha = alpha;
      fx.ctx.fillStyle = p.col;
      fx.ctx.fillRect(-p.w * 0.5, -p.h * 0.5, p.w, p.h);
      fx.ctx.restore();
    }

    if (fx.pieces.length) {
      fx.rafId = requestAnimationFrame(fxFrame);
    } else {
      fx.running = false;
      fx.ctx.clearRect(0, 0, rect.width, rect.height);
    }
  }

  // ---------------------------------------------------------------- CSS 3D 卡片倾斜 + 镜面高光
  function bindTiltCards(root) {
    if (!root) return;
    root.querySelectorAll('[data-tilt]').forEach((el) => {
      if (el.dataset.tiltBound) return;
      el.dataset.tiltBound = '1';
      const maxDeg = Number(el.dataset.tilt) || 9;
      el.addEventListener('pointermove', (e) => {
        const r = el.getBoundingClientRect();
        if (!r.width || !r.height) return;
        const px = clamp((e.clientX - r.left) / r.width, 0, 1);
        const py = clamp((e.clientY - r.top) / r.height, 0, 1);
        const ry = (px - 0.5) * 2 * maxDeg;
        const rx = (0.5 - py) * 2 * maxDeg;
        el.style.setProperty('--rx', rx.toFixed(2) + 'deg');
        el.style.setProperty('--ry', ry.toFixed(2) + 'deg');
        el.style.setProperty('--mx', (px * 100).toFixed(1) + '%');
        el.style.setProperty('--my', (py * 100).toFixed(1) + '%');
        el.classList.add('tilting');
      });
      el.addEventListener('pointerleave', () => {
        el.style.setProperty('--rx', '0deg');
        el.style.setProperty('--ry', '0deg');
        el.classList.remove('tilting');
      });
    });
  }

  // ---------------------------------------------------------------- 公开 API
  window.Break3D = {
    MODELS,
    getModel() { return stage.model; },
    setModel(id) {
      if (!MODELS.some((m) => m.id === id)) return;
      stage.model = id;
      localStorage.setItem('bp3dModel', id);
      triggerPulse(0.85, MODELS.find((m) => m.id === id).label);
    },
    start() {
      const c = document.getElementById('bp3dCanvas');
      if (!c) return;
      stage.canvas = c;
      stage.ctx = c.getContext('2d');
      bindStageEvents(c);
      resizeStage();
      if (!stage.running) {
        stage.running = true;
        stage.lastT = 0;
        stage.rafId = requestAnimationFrame(frame);
      }
    },
    stop() {
      stage.running = false;
      if (stage.rafId) cancelAnimationFrame(stage.rafId);
      stage.rafId = 0;
    },
    sync(mode, progress) {
      const prev = stage.mode;
      stage.mode = mode || 'idle';
      stage.progress = Number.isFinite(progress) ? progress : 0;
      if (prev !== stage.mode && stage.running) {
        if (stage.mode === 'active') triggerPulse(1.25, '放空开始 ☕');
        else if (stage.mode === 'over') triggerPulse(1.1, '超过建议时长啦 🔔');
        else if (prev === 'active' || prev === 'over') triggerPulse(1.2, '能量满格 ✨');
      }
    },
    pulse(strength, text) {
      if (stage.running) triggerPulse(strength, text);
    },
    celebrate(goldOnly = false, originX = 0.5, originY = 0.3) {
      burstConfetti(goldOnly ? 68 : 54, originX, originY, goldOnly);
    },
    bindTilts(root) {
      bindTiltCards(root);
    },
  };
})();
