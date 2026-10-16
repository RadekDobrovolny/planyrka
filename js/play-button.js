/*
 * Tlačítko Play složené z mikroteček.
 *
 * Dvě plátna nad sebou: spodní kreslí bílé tečky (vždy stejné), horní tytéž tečky
 * s proměnnou průhledností a v CSS má drop-shadow – pulz tedy přidává a ubírá jen záři.
 * Všechny tečky dýchají naráz; pulz i rozfouknutí po stisku řídí GSAP.
 */
(function () {
  'use strict';

  const TAU = Math.PI * 2;
  const clamp01 = (v) => (v < 0 ? 0 : v > 1 ? 1 : v);
  const smooth = (a, b, v) => {
    const t = clamp01((v - a) / (b - a));
    return t * t * (3 - 2 * t);
  };

  // Trojúhelník v jednotkovém čtverci; těžiště leží ve středu (opticky vycentrováno).
  const TRIANGLE = [[0.27, 0.12], [0.27, 0.88], [0.96, 0.5]];

  const DOT_COLOR = 'rgb(255, 251, 244)';
  const DOT_RADIUS = 0.2;     // poloměr tečky jako díl rozteče mřížky
  const PULSE_PEAK = 0.9;     // síla záře v nádechu (0–1)

  // Vzdálenost bodu od nejbližší hrany trojúhelníku (kladná uvnitř).
  function insetDistance(x, y, pts) {
    const [a, b, c] = pts;
    const orient = Math.sign((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]));
    let min = Infinity;
    for (let i = 0; i < 3; i++) {
      const p = pts[i];
      const q = pts[(i + 1) % 3];
      const ex = q[0] - p[0];
      const ey = q[1] - p[1];
      const d = (orient * (ex * (y - p[1]) - ey * (x - p[0]))) / Math.hypot(ex, ey);
      if (d < min) min = d;
    }
    return min;
  }

  function createDotPlayButton(button, { onPress, onGone, reducedMotion = false } = {}) {
    const base = button.querySelector('.play__dots');
    const light = button.querySelector('.play__light');
    const bctx = base.getContext('2d');
    const lctx = light.getContext('2d');

    const state = { breath: 0, gain: 1, flash: 0, burst: 0 };
    let dots = [];
    let size = 0;
    let canvasSize = 0;
    let pad = 0;
    let dpr = 1;
    let pressed = false;

    function layout() {
      size = button.offsetWidth;
      canvasSize = base.offsetWidth;
      pad = (canvasSize - size) / 2;
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      for (const c of [base, light]) {
        c.width = Math.round(canvasSize * dpr);
        c.height = Math.round(canvasSize * dpr);
      }
      buildDots();
    }

    function buildDots() {
      const pts = TRIANGLE.map(([x, y]) => [x * size, y * size]);
      const step = size / 30;                 // rozteč šestiúhelníkové mřížky
      const rowH = (step * Math.sqrt(3)) / 2;
      const r = step * DOT_RADIUS;
      const rand = mulberry32(7);             // stálé „náhodné“ směry rozfouknutí

      dots = [];
      for (let row = 0; row * rowH <= size; row++) {
        const y = row * rowH;
        for (let x = (row % 2) * (step / 2); x <= size; x += step) {
          if (insetDistance(x, y, pts) < step * 0.3) continue;
          dots.push({
            x, y,
            ph: rand() * TAU,
            k: rand(),
            // směr rozfoukání po stisku – převážně doprava, jako vítr
            vx: 0.45 + rand() * 0.95,
            vy: (rand() - 0.5) * 0.7 - 0.1,
          });
        }
      }
    }

    function draw() {
      const s = state;
      bctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      lctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      bctx.clearRect(0, 0, canvasSize, canvasSize);
      lctx.clearRect(0, 0, canvasSize, canvasSize);
      bctx.fillStyle = DOT_COLOR;
      lctx.fillStyle = DOT_COLOR;

      const burstEase = Math.pow(s.burst, 1.4);
      const glow = clamp01(Math.max(s.breath * PULSE_PEAK * s.gain, s.flash));   // stejná pro všechny tečky
      const r = (size / 30) * DOT_RADIUS;

      for (const d of dots) {
        let x = d.x + pad;
        let y = d.y + pad;
        let alpha = 1;

        if (s.burst > 0) {
          const reach = size * (0.5 + d.k * 0.9);
          x += burstEase * reach * d.vx + Math.sin(s.burst * 3.2 + d.ph) * 7 * s.burst;
          y += burstEase * reach * d.vy + Math.cos(s.burst * 2.6 + d.ph) * 6 * s.burst;
          alpha = 1 - smooth(0.1 + d.k * 0.35, 0.7 + d.k * 0.3, s.burst);
          if (alpha <= 0.002) continue;
        }

        bctx.globalAlpha = alpha;
        bctx.beginPath();
        bctx.arc(x, y, r, 0, TAU);
        bctx.fill();

        const la = glow * alpha;
        if (la > 0.01) {
          lctx.globalAlpha = la;
          lctx.beginPath();
          lctx.arc(x, y, r, 0, TAU);
          lctx.fill();
        }
      }

      const halo = Math.max(s.breath * 0.6 * s.gain, s.flash) * (1 - s.burst);
      button.style.setProperty('--halo', halo.toFixed(3));
    }


    // --- pulzování jako klidný dech: nádech, delší výdech, krátké zastavení ---
    const INHALE = 2.15;
    const EXHALE = 2.85;
    const REST = 0.25;
    const pulse = gsap.timeline({ repeat: -1, repeatDelay: REST });
    pulse
      .fromTo(state, { breath: 0 }, { breath: 1, duration: INHALE, ease: 'sine.inOut' })
      .to(state, { breath: 0, duration: EXHALE, ease: 'sine.inOut' });
    if (reducedMotion) pulse.timeScale(0.6);

    // --- interakce ---
    const baseSpeed = reducedMotion ? 0.6 : 1;
    function hover(on) {
      if (pressed) return;
      gsap.to(state, { gain: on ? 1.3 : 1, duration: 0.8, ease: 'sine.inOut', overwrite: 'auto' });
      gsap.to(pulse, { timeScale: on ? baseSpeed * 1.25 : baseSpeed, duration: 1.2, overwrite: 'auto' });
    }
    button.addEventListener('pointerenter', () => hover(true));
    button.addEventListener('pointerleave', () => hover(false));
    button.addEventListener('focus', () => hover(true));
    button.addEventListener('blur', () => hover(false));

    button.addEventListener('click', () => {
      if (pressed) return;
      pressed = true;
      button.disabled = true;
      if (onPress) onPress();           // synchronně – kvůli odemčení zvuku v gestu

      pulse.pause();
      gsap.killTweensOf(state);
      gsap.timeline({ onComplete: destroy })
        .to(state, { flash: 1, gain: 1.4, duration: 0.3, ease: 'power2.out' })
        .to(state, { burst: 1, duration: reducedMotion ? 0.7 : 1.7, ease: 'power1.out' }, 0.2);
    });

    function destroy() {
      gsap.ticker.remove(onTick);
      window.removeEventListener('resize', layout);
      pulse.kill();
      if (onGone) onGone();
    }

    const onTick = () => draw();

    layout();
    window.addEventListener('resize', layout);
    gsap.ticker.add(onTick);

    return { destroy };
  }

  // Malý deterministický generátor, aby tečky vypadaly pokaždé stejně.
  function mulberry32(seed) {
    return function () {
      seed |= 0;
      seed = (seed + 0x6d2b79f5) | 0;
      let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  window.createDotPlayButton = createDotPlayButton;
})();
