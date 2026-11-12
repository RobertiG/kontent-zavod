// Сцена 1. Исходник на белом → сцена → плашки → заголовок → настоящий слайд → плитка в выдаче → взгляд издалека
import { natRect, stepper, sceneTrigger } from '../util.js';

const CAPS = ['исходник', 'сцена', 'плашки', 'заголовок', 'готовый слайд, настоящая работа', 'в выдаче', 'издалека'];

export default function card(section, { lite }) {
  const q = (s) => section.querySelector(s);
  const sticky = q('.card-sticky');
  const area = q('.card-area');
  const stage = q('.card-stage');
  const slide = q('.slide');
  const hero = q('.hero-copy');
  const steps = q('.card-steps');
  const items = [...steps.children];
  const feed = q('.feed');
  const tiles = [...feed.querySelectorAll('.tile:not(.tile-ours)')];
  const slot = q('.tile-slot');
  const ours = q('.tile-ours figcaption');
  const cap = q('.stage-cap');
  const capState = q('.cap-state');
  const marks = stage.querySelector('.marks');
  const credit = q('.card-credit');
  const L = {
    scene: q('.l-scene'), product: q('.l-product'), sel: q('.l-select rect'), grid: q('.l-grid'),
    head: q('.l-head b'), sub: q('.l-head span'), headBox: q('.l-head'), chipsBox: q('.l-chips'),
    chips: [...q('.l-chips').children], real: q('.l-real'),
  };

  const mm = gsap.matchMedia();
  mm.add({ wide: '(min-width: 1024px)', narrow: '(max-width: 1023px)' }, (c) => {
    const { wide } = c.conditions;
    // геометрия: где лист стоит сам, где он большой (на узких экранах) и где его плитка в выдаче
    let g;
    const measure = () => {
      const nat = natRect(stage, sticky);
      const s = natRect(slot, sticky);
      let big = nat;
      if (!wide) {
        const top = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--hdr')) + 14;
        const bottomSpace = sticky.clientHeight - steps.offsetTop + 10;
        const h = sticky.clientHeight - top - bottomSpace;
        const w = Math.min(area.clientWidth, h * 0.75);
        big = { x: (sticky.clientWidth - w) / 2, y: top, w, h: w / 0.75 };
      }
      g = {
        big: { x: big.x - nat.x, y: big.y - nat.y, s: big.w / nat.w },
        slot: { x: s.x - nat.x, y: s.y - nat.y, s: s.w / nat.w },
      };
    };
    measure();
    ScrollTrigger.addEventListener('refreshInit', measure);

    const tl = gsap.timeline({ defaults: { ease: 'none', duration: 1 } });
    const at = (t, d) => ({ position: t, d });

    // 0–8: первый экран уходит, появляются шаги
    tl.to(hero, { y: wide ? -40 : () => -hero.offsetHeight - 40, autoAlpha: 0, duration: 7, ease: 'power1.in' }, 0)
      .to(steps, { autoAlpha: 1, duration: 4 }, wide ? 4 : 6);
    if (!wide) tl.to(stage, { x: () => g.big.x, y: () => g.big.y, scale: () => g.big.s, duration: 10, ease: 'power2.inOut' }, 1);

    // 6–16: товар отделяем от фона — бежит контур выделения
    tl.to(L.sel, { strokeDashoffset: 0, duration: 8, ease: 'power1.inOut' }, 6);

    // 16–30: белый уходит, проступает сцена; товар сцены сменяет вырезку
    tl.to(L.sel, { opacity: 0, duration: 3 }, 16)
      .fromTo(L.scene, { opacity: 0, scale: 1.08 }, { opacity: 1, scale: 1, duration: 10, ease: 'power2.out' }, 16)
      .to(L.product, { opacity: 0, duration: 6 }, 21);

    // 30–46: сетка безопасных зон, плашки прилетают и встают по ней
    tl.to(L.grid, { opacity: 1, duration: 3 }, 30)
      .fromTo(L.chips[0], { xPercent: -70, yPercent: 160, rotation: -9, opacity: 0 }, { xPercent: 0, yPercent: 0, rotation: 0, opacity: 1, duration: 8, ease: 'back.out(1.6)' }, 33)
      .fromTo(L.chips[1], { xPercent: 70, yPercent: 160, rotation: 9, opacity: 0 }, { xPercent: 0, yPercent: 0, rotation: 0, opacity: 1, duration: 8, ease: 'back.out(1.6)' }, 36)
      .set(L.chipsBox, { opacity: 1 }, 33)
      .to(L.grid, { opacity: 0, duration: 3 }, 44);

    // 46–58: заголовок встаёт крупно и ужимается в свою зону
    tl.set(L.headBox, { opacity: 1 }, 46)
      .fromTo(L.head, { scale: 1.9, opacity: 0, transformOrigin: '0% 0%' }, { scale: 1, opacity: 1, duration: 9, ease: 'power3.out' }, 46)
      .fromTo(L.sub, { opacity: 0, y: 12 }, { opacity: 1, y: 0, duration: 4 }, 53);

    // 58–64: совпадает с настоящим слайдом
    tl.to(L.real, { opacity: 1, duration: 5 }, 58)
      .to(credit, { opacity: 1, duration: 3 }, 59);

    // 64–86: камера отъезжает — слайд встаёт плиткой в выдачу
    tl.set(feed, { visibility: 'visible' }, 63)
      .to([marks, cap], { opacity: 0, duration: 3 }, 64)
      .to(slide, { borderRadius: () => 8 / g.slot.s, duration: 8 }, 66)
      .to(stage, { x: () => g.slot.x, y: () => g.slot.y, scale: () => g.slot.s, duration: 14, ease: 'power2.inOut' }, 66)
      .fromTo(tiles, { opacity: 0, y: 40 }, { opacity: 1, y: 0, duration: 8, stagger: 0.9, ease: 'power2.out' }, 68)
      .to(ours, { opacity: 1, duration: 3 }, 79)
      .to(q('.feed-note'), { opacity: 1, duration: 3 }, 81)
      .to(stage, { boxShadow: '0 0 0 0 rgba(0,0,0,0)', duration: 6 }, 70);

    // 86–100: взгляд издалека — всё размывается, наша плитка читается
    if (wide && !lite) {
      tl.to(area, { filter: 'blur(5px)', duration: 5 }, 87)
        .to(area, { filter: 'blur(0px)', duration: 4 }, 96);
    } else {
      tl.to({}, { duration: 14 }, 86);
    }

    const ranges = [[0, 16], [16, 30], [30, 46], [46, 64], [64, 86], [86, 101]];
    const capOf = [0, 1, 2, 3, 5, 6];
    const step = stepper(items, ranges, (i) => {
      capState.textContent = i === 3 && tl.progress() > 0.58 ? CAPS[4] : CAPS[capOf[i]];
    });
    const st = sceneTrigger(section, tl, (p) => {
      step(p);
      if (p > 58 && p < 64) capState.textContent = CAPS[4];
    });
    return () => { ScrollTrigger.removeEventListener('refreshInit', measure); st.kill(); };
  });
}
