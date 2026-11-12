// Сцена 7. Название и описание печатаются в такт прокрутке; напечатанный запрос подсвечивается
import { sceneTrigger } from '../util.js';

// Части текста: [текст, номер запроса или -1]
const TITLE = [['Вакуумный упаковщик', 0], [' QH-17, ', -1], ['вакууматор для продуктов', 1], [' ', -1], ['для дома', 2], [' ', -1], ['с пакетами', 3]];
const DESC = [['Откачивает воздух и запаивает пакет одной кнопкой. Два режима: для сухих и влажных продуктов. В комплекте 20 пакетов и шланг для контейнеров. Работает и как ', -1], ['запайщик пакетов', 4], [' без откачки.', -1]];

const len = (parts) => parts.reduce((a, [t]) => a + t.length, 0);

function render(el, parts, n) {
  let out = '', left = n;
  const hits = [];
  for (const [t, qi] of parts) {
    if (left <= 0) break;
    const piece = t.slice(0, left);
    left -= piece.length;
    if (qi >= 0) {
      const done = piece.length === t.length;
      if (done) hits.push(qi);
      out += done ? `<mark>${piece}</mark>` : piece;
    } else out += piece;
  }
  if (el.__html !== out) { el.innerHTML = out; el.__html = out; }
  return hits;
}

export default function seo(section) {
  const title = section.querySelector('.seo-title .typed');
  const desc = section.querySelector('.seo-desc .typed');
  const queries = [...section.querySelectorAll('.queries li')];
  const rows = [...section.querySelectorAll('.seo-specs tr')];
  const LT = len(TITLE), LD = len(DESC);
  const state = { p: 0 };
  const draw = () => {
    const p = state.p;
    const nt = Math.round(LT * Math.min(1, p / 0.36));
    const nd = Math.round(LD * Math.min(1, Math.max(0, (p - 0.4) / 0.38)));
    const hits = new Set([...render(title, TITLE, nt), ...render(desc, DESC, nd)]);
    queries.forEach((li, i) => li.classList.toggle('is-hit', hits.has(i)));
    rows.forEach((r, i) => r.classList.toggle('is-on', p > 0.8 + i * 0.03));
  };
  const tl = gsap.timeline().to(state, { p: 1, duration: 1, ease: 'none', onUpdate: draw });
  draw();
  const mm = gsap.matchMedia();
  mm.add({ wide: '(min-width: 1024px)', narrow: '(max-width: 1023px)' }, (c) => {
    const st = c.conditions.wide
      ? sceneTrigger(section, tl, null, { end: 'bottom bottom' })
      : ScrollTrigger.create({ trigger: section.querySelector('.seo-sheet'), start: 'top 85%', end: 'bottom 45%', scrub: 0.6, animation: tl });
    return () => st.kill();
  });
}
