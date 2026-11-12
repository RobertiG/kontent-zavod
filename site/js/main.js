// Контент завод — запуск страницы: режим показа, сцены, видео, шкала разделов, работы
const doc = document.documentElement;
clearTimeout(window.__kzFallback);

const hasGsap = !!(window.gsap && window.ScrollTrigger);
if (!hasGsap) doc.classList.remove('motion');
const motion = doc.classList.contains('motion');
const lite = doc.classList.contains('lite');
const ctx = { motion, lite };

initVideos();
initWorks();
initRail();

if (motion) {
  gsap.registerPlugin(ScrollTrigger);
  ScrollTrigger.config({ ignoreMobileResize: true });
  if (!lite && matchMedia('(pointer: fine)').matches) initLenis();
  initScenes();
  let t;
  const refresh = () => { clearTimeout(t); t = setTimeout(() => ScrollTrigger.refresh(), 120); };
  document.fonts?.ready.then(refresh);
  addEventListener('load', refresh);
}

// ---------- Сцены: модуль грузится за полтора экрана до появления ----------
function initScenes() {
  const loaders = {
    card: () => import('./scenes/card.js'),
    cover: () => import('./scenes/cover.js'),
    sources: () => import('./scenes/sources.js'),
    box: () => import('./scenes/box.js'),
    manual: () => import('./scenes/manual.js'),
    seo: () => import('./scenes/seo.js'),
    rich: () => import('./scenes/rich.js'),
    packs: () => import('./scenes/packs.js'),
    order: () => import('./scenes/order.js'),
  };
  const start = (el) => {
    if (el.dataset.ready) return;
    el.dataset.ready = '1';
    const load = loaders[el.dataset.scene];
    if (!load) return;
    load().then((m) => { m.default(el, ctx); ScrollTrigger.refresh(); })
      .catch((e) => { console.error('Сцена не запустилась:', el.dataset.scene, e); });
  };
  const io = new IntersectionObserver((entries) => {
    entries.forEach((e) => { if (e.isIntersecting) { io.unobserve(e.target); start(e.target); } });
  }, { rootMargin: '150% 0px 150% 0px' });
  document.querySelectorAll('[data-scene]').forEach((el) => {
    if (el.dataset.scene === 'card') start(el); else io.observe(el);
  });
}

// ---------- Плавная прокрутка: только ПК с мышью ----------
function initLenis() {
  const s = document.createElement('script');
  s.src = 'vendor/lenis.min.js';
  s.onload = () => {
    const lenis = new window.Lenis({ autoRaf: false, lerp: 0.11, anchors: { offset: 56 }, allowNestedScroll: true });
    lenis.on('scroll', ScrollTrigger.update);
    gsap.ticker.add((time) => lenis.raf(time * 1000));
    gsap.ticker.lagSmoothing(0);
    window.__lenis = lenis;
  };
  document.head.appendChild(s);
}

// ---------- Видео: играют, только пока видны; пауза по кнопке ----------
function initVideos() {
  const autoplay = motion && !lite;
  const io = new IntersectionObserver((entries) => {
    entries.forEach(({ target: v, isIntersecting }) => {
      if (isIntersecting && v.dataset.user !== 'paused') v.play().catch(() => markPaused(v, true));
      else if (!isIntersecting) v.pause();
    });
  }, { threshold: 0.5 });
  const markPaused = (v, paused) => {
    const b = v.parentElement.querySelector('.vid-toggle');
    if (!b) return;
    b.classList.toggle('is-paused', paused);
    b.setAttribute('aria-label', paused ? 'Смотреть' : 'Пауза');
  };
  document.querySelectorAll('video.vid').forEach((v) => {
    v.addEventListener('play', () => markPaused(v, false));
    v.addEventListener('pause', () => markPaused(v, true));
    const toggle = v.parentElement.querySelector('.vid-toggle');
    const play = v.parentElement.querySelector('.vid-play');
    if (v.hasAttribute('data-autoplay')) {
      markPaused(v, true);
      if (autoplay) io.observe(v);
    }
    toggle?.addEventListener('click', () => {
      if (v.paused) { v.dataset.user = ''; v.play(); } else { v.dataset.user = 'paused'; v.pause(); }
    });
    play?.addEventListener('click', () => {
      v.controls = true; play.hidden = true; v.play();
    });
  });
}

// ---------- Шкала разделов слева ----------
function initRail() {
  const links = [...document.querySelectorAll('.rail a')];
  if (!links.length) return;
  const ids = links.map((a) => a.dataset.rail);
  const io = new IntersectionObserver((entries) => {
    entries.forEach((e) => {
      if (!e.isIntersecting) return;
      const i = ids.indexOf(e.target.id);
      links.forEach((a, k) => {
        a.classList.toggle('is-here', k === i);
        a.classList.toggle('is-past', k < i);
        if (k === i) a.setAttribute('aria-current', 'true'); else a.removeAttribute('aria-current');
      });
    });
  }, { rootMargin: '-50% 0px -50% 0px' });
  ids.forEach((id) => { const el = document.getElementById(id); if (el) io.observe(el); });
}

// ---------- Работы: переключатель товаров ----------
function initWorks() {
  const tabs = [...document.querySelectorAll('.works-tabs [role="tab"]')];
  const strip = document.querySelector('.works-strip');
  if (!tabs.length || !strip) return;
  const names = { vacuum: 'вакуумного упаковщика', sharpener: 'точилки', knife: 'шеф-ножа', airfryer: 'аэрогриля', humidifier: 'увлажнителя', speakers: 'колонок', scissors: 'ножниц', soundbar: 'саундбара' };
  const select = (tab, focus) => {
    tabs.forEach((t) => {
      const on = t === tab;
      t.setAttribute('aria-selected', on);
      t.tabIndex = on ? 0 : -1;
    });
    if (focus) tab.focus();
    const set = tab.dataset.set, n = +tab.dataset.n;
    strip.innerHTML = Array.from({ length: n }, (_, i) =>
      `<li><img src="assets/img/works/${set}-${i + 1}-600.webp" alt="Слайд ${i + 1} карусели ${names[set]}" width="600" height="800" decoding="async"${i > 3 ? ' loading="lazy"' : ''}></li>`).join('');
    strip.scrollLeft = 0;
    if (motion && window.gsap) {
      gsap.fromTo(strip.children, { x: 70, rotation: 3, opacity: 0 }, { x: 0, rotation: 0, opacity: 1, duration: 0.55, ease: 'power3.out', stagger: 0.05, clearProps: 'transform,opacity' });
    }
  };
  tabs.forEach((t, i) => {
    t.addEventListener('click', () => select(t));
    t.addEventListener('keydown', (e) => {
      const d = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
      if (!d) return;
      e.preventDefault();
      select(tabs[(i + d + tabs.length) % tabs.length], true);
    });
  });
  document.querySelectorAll('.works-nav [data-dir]').forEach((b) => b.addEventListener('click', () => {
    const item = strip.firstElementChild;
    const step = item ? item.getBoundingClientRect().width + 14 : 300;
    strip.scrollBy({ left: step * +b.dataset.dir * 2, behavior: motion ? 'smooth' : 'auto' });
  }));
}
