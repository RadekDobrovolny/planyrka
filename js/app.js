/*
 * Průběh fejetonu: úvodní Play → scény (obrázek + zvuk + titulky) → poslední obrázek.
 * Obsah scén je v data/scenes.js.
 */
(function () {
  'use strict';

  const scenes = window.SCENES || [];
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // Titulky: jak dlouho před prvním slovem se věta ukáže / jak dlouho po posledním zůstane.
  const CUE_LEAD = 0.6;
  const CUE_HOLD = 1.6;
  // Posun všech časů slov (s) – kladné číslo = slova se rozsvěcují později.
  const WORD_OFFSET = 0;
  // Prodleva mezi začátkem objevování obrázku a startem zvuku.
  const AUDIO_DELAY = 1.3;

  // Ladění přes adresu:  ?nahled – scény bez nahrávky se ukážou s názvem na PREVIEW_HOLD s a jede se dál
  //                      ?od=5   – začít od 5. scény
  const params = new URLSearchParams(window.location.search);
  const PREVIEW = params.has('nahled');
  const PREVIEW_HOLD = 5;
  const START = Math.min(Math.max((parseInt(params.get('od'), 10) || 1) - 1, 0), Math.max(scenes.length - 1, 0));

  const introEl = document.getElementById('intro');
  const playEl = document.getElementById('play');
  const sceneEl = document.getElementById('scene');
  const figureEl = sceneEl.querySelector('.scene__figure');
  const imageEl = document.getElementById('scene-image');
  const subsEl = document.getElementById('subtitles');
  const creditsEl = document.getElementById('credits');
  const progressEl = document.getElementById('progress');
  const pauseEl = document.getElementById('pause');

  const ONE_LETTER = /^[aiouvkszAIOUVKSZ]$/;

  // drobné ikonky z teček (jako úvodní tlačítko): trojúhelník Play a dvě svislé řady pro pauzu
  const dotsSvg = (pts) => `<svg viewBox="0 0 14 14" aria-hidden="true">${
    pts.map(([x, y]) => `<circle cx="${x}" cy="${y}" r="1.05"/>`).join('')}</svg>`;
  const PLAY_ICON = dotsSvg([0, 1, 2, 3].flatMap((col) =>
    Array.from({ length: 4 - col }, (_, row) => [2 + col * 3, 2 + col * 1.5 + row * 3])));
  const PAUSE_ICON = dotsSvg([4.5, 9.5].flatMap((x) => [2.5, 5.5, 8.5, 11.5].map((y) => [x, y])));

  const wait = (s) => new Promise((resolve) => gsap.delayedCall(s, resolve));

  // ---------- načítání ----------

  const imageCache = new Map();
  function loadImage(src) {
    if (!imageCache.has(src)) {
      imageCache.set(src, new Promise((resolve) => {
        const img = new Image();
        img.onload = () => {
          img.decode?.().catch(() => {});   // dekódovat dopředu, ale nečekat na to
          resolve(img);
        };
        img.onerror = () => resolve(img);
        img.src = src;
      }));
    }
    return imageCache.get(src);
  }

  const audios = scenes.map((scene, i) => {
    if (!scene.audio) return null;
    const a = new Audio();
    a.preload = i === START ? 'auto' : 'metadata';
    a.src = scene.audio;
    return a;
  });

  if (scenes[START] && scenes[START].image) loadImage(scenes[START].image);

  // Písmo i s českými znaky načíst hned – jinak se diakritika na chvíli ukáže náhradním fontem.
  if (document.fonts) {
    document.fonts.load('600 1em "Cormorant Garamond"', 'Příliš žluťoučký kůň úpěl ďábelské ódy').catch(() => {});
  }

  // Prohlížeče (hlavně Safari) pustí zvuk jen z uživatelského gesta.
  // Při stisku Play ho proto potichu „nastartujeme“ a hned zastavíme.
  function primeAudio(a) {
    if (!a) return;
    a.muted = true;
    const p = a.play();
    if (!p) return;
    p.then(() => {
      if (!a._wanted) {
        a.pause();
        a.currentTime = 0;
      }
      a.muted = false;
    }).catch(() => { a.muted = false; });
  }

  function startAudio(a) {
    a._wanted = true;
    a.muted = false;
    if (!a.paused) return;            // priming ještě běží – necháme hrát
    a.currentTime = 0;
    a.play().catch(() => {
      // pojistka: kdyby prohlížeč zvuk přesto zablokoval, pustí ho další klepnutí
      document.addEventListener('pointerdown', () => a.play(), { once: true });
    });
  }

  // ---------- titulky ----------

  function createSubtitles(cues) {
    const prepared = cues.map((words) => ({
      words,
      start: words[0][1] + WORD_OFFSET,
      end: words[words.length - 1][2] + WORD_OFFSET,
    }));

    let target = -1;     // titulek, který má být vidět
    let rendered = -1;   // titulek, který je právě v DOM
    let spans = [];
    let tl = null;

    function cueAt(t) {
      for (let i = prepared.length - 1; i >= 0; i--) {
        const c = prepared[i];
        if (t >= c.start - CUE_LEAD) {
          const next = prepared[i + 1];
          const until = Math.min(next ? next.start - CUE_LEAD : Infinity, c.end + CUE_HOLD);
          return t < until ? i : -1;
        }
      }
      return -1;
    }

    function render(i) {
      rendered = i;
      subsEl.textContent = '';
      spans = [];
      if (i < 0) return;
      const text = document.createElement('span');
      text.className = 'subtitles__text';
      prepared[i].words.forEach(([word], j, all) => {
        // jednopísmenné předložky a spojky nezůstanou na konci řádku
        if (j > 0) text.append(ONE_LETTER.test(all[j - 1][0]) ? '\u00a0' : ' ');
        const span = document.createElement('span');
        span.className = 'word';
        span.textContent = word;
        text.append(span);
        spans.push(span);
      });
      subsEl.append(text);
    }

    function show(i) {
      target = i;
      if (tl) tl.kill();
      tl = gsap.timeline();
      if (rendered >= 0) {
        tl.to(subsEl, { opacity: 0, y: -6, filter: 'blur(3px)', duration: 0.3, ease: 'power1.in' });
      }
      tl.call(() => render(i));
      if (i >= 0) {
        tl.fromTo(subsEl,
          { opacity: 0, y: 8, filter: 'blur(4px)' },
          { opacity: 1, y: 0, filter: 'blur(0px)', duration: 0.5, ease: 'power2.out' });
      }
    }

    function update(t) {
      const i = cueAt(t);
      if (i !== target) show(i);
      if (rendered < 0) return;
      const words = prepared[rendered].words;
      for (let j = 0; j < spans.length; j++) {
        const s = words[j][1] + WORD_OFFSET;
        const e = words[j][2] + WORD_OFFSET;
        const lit = t >= s - 0.04;
        const now = lit && t < e + 0.12;
        spans[j].classList.toggle('is-lit', lit);
        spans[j].classList.toggle('is-now', now);
      }
    }

    function hide() {
      if (tl) tl.kill();
      target = -1;
      return gsap.to(subsEl, { opacity: 0, filter: 'blur(3px)', duration: 0.7, ease: 'power1.in' })
        .then(() => render(-1));
    }

    return { update, hide };
  }

  // V náhledu se místo titulků ukáže název scény.
  function showCaption(text) {
    subsEl.textContent = '';
    const span = document.createElement('span');
    span.className = 'subtitles__text';
    span.textContent = text;
    subsEl.append(span);
    gsap.fromTo(subsEl,
      { opacity: 0, y: 8, filter: 'blur(4px)' },
      { opacity: 1, y: 0, filter: 'blur(0px)', duration: 0.6, ease: 'power2.out' });
  }

  function hideCaption() {
    return gsap.to(subsEl, { opacity: 0, filter: 'blur(3px)', duration: 0.5, ease: 'power1.in' });
  }

  // ---------- stavový řádek ----------

  const storyLength = scenes.filter((s) => !s.credits).length;
  const dots = Array.from({ length: storyLength }, () => {
    const dot = document.createElement('span');
    dot.className = 'progress__dot';
    progressEl.append(dot);
    return dot;
  });

  function setProgress(index) {
    dots.forEach((dot, i) => {
      dot.classList.toggle('is-done', i < index);
      dot.classList.toggle('is-current', i === index);
    });
    const n = Math.min(index + 1, storyLength);
    progressEl.setAttribute('aria-label', index >= storyLength ? 'Konec' : `Scéna ${n} z ${storyLength}`);
  }

  // ---------- pauza ----------

  let paused = false;
  let currentAudio = null;      // právě hrající vyprávění
  let resumeAudio = false;

  function renderPause() {
    pauseEl.innerHTML = paused ? `${PLAY_ICON}<span>Pokračovat</span>` : `${PAUSE_ICON}<span>Pauza</span>`;
    pauseEl.setAttribute('aria-label', paused ? 'Pokračovat' : 'Pauza');
  }

  // zastaví zvuk i všechny animace (GSAP) – přechod nebo pohupování se zastaví, kde právě je
  function setPaused(on) {
    if (on === paused) return;
    paused = on;
    document.body.classList.toggle('is-paused', on);
    if (on) {
      gsap.globalTimeline.pause();
      resumeAudio = !!currentAudio && !currentAudio.paused;
      if (resumeAudio) currentAudio.pause();
    } else {
      gsap.globalTimeline.resume();
      if (resumeAudio && currentAudio) currentAudio.play().catch(() => {});
      resumeAudio = false;
    }
    renderPause();
  }

  function showPauseButton(on) {
    if (!on) setPaused(false);
    pauseEl.classList.toggle('is-visible', on);
    pauseEl.disabled = !on;
  }

  pauseEl.addEventListener('click', () => setPaused(!paused));
  document.addEventListener('keydown', (e) => {
    if (e.code !== 'Space' || pauseEl.disabled) return;
    if (e.target !== document.body) return;          // na tlačítku mezerník „klikne“ sám
    e.preventDefault();
    setPaused(!paused);
  });
  renderPause();

  // ---------- závěrečné titulky ----------

  function showCredits(scene) {
    const { nazev, radky = [] } = scene.credits;
    const lines = [];
    const word = (text, cls) => {
      const span = document.createElement('span');
      span.className = cls ? `word ${cls}` : 'word';
      // jednopísmenné předložky a spojky nezůstanou na konci řádku
      span.textContent = text.replace(/(^|\s)([aiouvkszAIOUVKSZ])\s/g, '$1$2 ');
      return span;
    };
    creditsEl.textContent = '';
    if (nazev) {
      const h = document.createElement('h2');
      h.className = 'credits__title';
      const w = word(nazev);
      h.append(w);
      creditsEl.append(h);
      lines.push([w]);
    }
    for (const [label, text] of radky) {
      const p = document.createElement('p');
      p.className = 'credits__line';
      const parts = [];
      if (label) parts.push(word(label, 'credits__label'));
      parts.push(word(text));
      p.append(...parts);
      creditsEl.append(p);
      lines.push(parts);
    }
    showPauseButton(false);
    sceneEl.hidden = true;
    creditsEl.hidden = false;
    gsap.fromTo(creditsEl, { opacity: 0, filter: 'blur(6px)' },
      { opacity: 1, filter: 'blur(0px)', duration: 1.6, ease: 'power2.out' });
    // řádek po řádku se rozsvítí jako slova v titulcích
    lines.forEach((parts, i) => gsap.delayedCall(1.5 + i * 1.1, () => {
      parts.forEach((el) => el.classList.add('is-lit', 'is-now'));
      gsap.delayedCall(0.7, () => parts.forEach((el) => el.classList.remove('is-now')));
    }));

    // nakonec tlačítko „Přehrát znovu“
    const replay = document.createElement('button');
    replay.type = 'button';
    replay.className = 'glow-button replay';
    replay.innerHTML = `${PLAY_ICON}<span>Přehrát znovu</span>`;
    replay.addEventListener('click', restart, { once: true });
    creditsEl.append(replay);
    gsap.fromTo(replay, { opacity: 0, y: 8 }, { opacity: 1, y: 0, duration: 1.4, ease: 'power2.out', delay: 1.5 + lines.length * 1.1 + 0.6 });
  }

  function restart() {
    // zvuk první scény „odemknout“ hned v kliknutí (Safari)
    audios.forEach((a) => {
      if (!a) return;
      a.pause();
      a._wanted = false;
      a.currentTime = 0;
    });
    primeAudio(audios[0]);
    setProgress(-1);
    showPauseButton(true);
    gsap.to(creditsEl, {
      opacity: 0,
      filter: 'blur(6px)',
      duration: 0.9,
      ease: 'power1.in',
      onComplete() {
        creditsEl.hidden = true;
        creditsEl.textContent = '';
        subsEl.textContent = '';
        sceneEl.hidden = false;
        runScene(0);
      },
    });
  }

  // ---------- scény ----------

  let driftTweens = [];

  function startDrift() {
    if (reducedMotion) return;
    driftTweens = [
      gsap.to(figureEl, { x: 12, rotation: 0.9, duration: 5.2, ease: 'sine.inOut', yoyo: true, repeat: -1 }),
      gsap.to(figureEl, { y: -7, duration: 3.7, ease: 'sine.inOut', yoyo: true, repeat: -1 }),
    ];
  }

  function stopDrift() {
    driftTweens.forEach((t) => t.kill());
    driftTweens = [];
    gsap.to(figureEl, { x: 0, y: 0, rotation: 0, duration: 1.2, ease: 'sine.out' });
  }

  function enterImage(scene) {
    // obrázky s větrem přilétají zleva, ostatní se jen vynoří z rozmazání
    const fromX = scene.drift && !reducedMotion ? -40 : 0;
    return gsap.fromTo(imageEl,
      { opacity: 0, x: fromX, scale: 0.97, filter: 'blur(14px)' },
      { opacity: 1, x: 0, scale: 1, filter: 'blur(0px)', duration: reducedMotion ? 1.2 : 2.6, ease: 'power2.out' });
  }

  function exitImage() {
    return gsap.to(imageEl, {
      opacity: 0,
      x: reducedMotion ? 0 : 70,
      filter: 'blur(10px)',
      duration: reducedMotion ? 0.9 : 1.8,
      ease: 'power2.in',
    });
  }

  function playNarration(index) {
    const audio = audios[index];
    const subs = createSubtitles(scenes[index].cues || []);
    const onTick = () => subs.update(audio.currentTime);

    return new Promise((resolve) => {
      currentAudio = audio;
      audio.addEventListener('ended', () => {
        currentAudio = null;
        gsap.ticker.remove(onTick);
        subs.hide();
        resolve();
      }, { once: true });
      gsap.ticker.add(onTick);
      startAudio(audio);
    });
  }

  async function runScene(index) {
    const scene = scenes[index];
    if (!scene) return;
    if (scene.credits) {
      setProgress(storyLength);
      showCredits(scene);
      return;
    }
    setProgress(scenes.slice(0, index).filter((s) => !s.credits).length);

    const img = await loadImage(scene.image);
    imageEl.src = img.src;
    imageEl.alt = scene.alt || '';

    // připravit další scénu, zatímco běží tahle
    const next = scenes[index + 1];
    if (next && next.image) loadImage(next.image);
    if (audios[index + 1]) audios[index + 1].preload = 'auto';

    enterImage(scene);
    if (scene.drift) startDrift();

    if (audios[index]) {
      await wait(AUDIO_DELAY);
      await playNarration(index);
    } else {
      if (!PREVIEW) return;            // scéna bez zvuku = konec (zatím)
      await wait(0.8);
      showCaption(scene.title || '');
      if (!next) return;
      await wait(PREVIEW_HOLD);
      await hideCaption();
    }

    await wait(0.5);
    if (scene.drift) stopDrift();
    await exitImage();
    await wait(0.3);
    runScene(index + 1);
  }

  // ---------- start ----------

  window.createDotPlayButton(playEl, {
    reducedMotion,
    onPress() {
      primeAudio(audios[START]);
      progressEl.classList.add('is-visible');
      showPauseButton(true);
      gsap.delayedCall(0.75, () => {
        sceneEl.hidden = false;
        runScene(START);
      });
    },
    onGone() {
      introEl.remove();
    },
  });
})();
