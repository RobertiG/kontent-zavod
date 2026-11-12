// Сцена 5. Саундбар «поворачивается» по кадрам в одном окне, затем кадры раскладываются контактным листом
import { natRect, sceneTrigger } from '../util.js';

export default function sources(section) {
  const q = (s) => section.querySelector(s);
  const board = q('.src-board');
  const viewer = q('.src-viewer');
  const cells = [...section.querySelectorAll('.src-grid li')];
  const labels = section.querySelectorAll('.cell-label, .dim-label');
  const dim = q('.dim path');
  const now = q('.src-now-text');

  const mm = gsap.matchMedia();
  mm.add('(min-width: 768px)', () => {
    // окно просмотра — квадрат по центру доски
    let g = [];
    const layout = () => {
      const bw = board.clientWidth, bh = board.clientHeight;
      const side = Math.min(bw * 0.62, innerHeight * 0.62);
      Object.assign(viewer.style, { width: side + 'px', height: side + 'px', left: (bw - side) / 2 + 'px', top: (bh - side) / 2 + 'px' });
      const v = { x: (bw - side) / 2, y: (bh - side) / 2 };
      g = cells.map((c) => { const r = natRect(c, board); return { x: v.x - r.x, y: v.y - r.y, s: side / r.w }; });
    };
    layout();
    ScrollTrigger.addEventListener('refreshInit', layout);

    const shots = cells.slice(0, 7);
    const pass = cells[7];
    const tl = gsap.timeline({ defaults: { ease: 'none', duration: 1 } });
    // все кадры встают в окно, видим только первый
    tl.set(cells, { transformOrigin: '0 0' }, 0)
      .set(shots, { x: (i) => g[i].x, y: (i) => g[i].y, scale: (i) => g[i].s, opacity: (i) => (i ? 0 : 1) }, 0)
      .set(pass, { opacity: 0, y: 60 }, 0)
      .to(viewer, { opacity: 1, duration: 1 }, 0);
    // смена ракурсов
    const per = 6;
    shots.forEach((c, i) => {
      if (!i) return;
      tl.to(c, { opacity: 1, duration: 2.2 }, 4 + i * per - 2.2)
        .to(shots[i - 1], { opacity: 0, duration: 2.2 }, 4 + i * per - 2.2);
    });
    const endTurn = 4 + 6 * per;
    tl.set(shots, { opacity: 1 }, endTurn);
    // раскладываем лист
    tl.to(viewer, { opacity: 0, duration: 3 }, endTurn)
      .to(shots, { x: 0, y: 0, scale: 1, duration: 14, ease: 'power3.inOut', stagger: 0.8 }, endTurn)
      .to(pass, { opacity: 1, y: 0, duration: 8, ease: 'power2.out' }, endTurn + 10)
      .to(labels, { opacity: 1, duration: 4 }, endTurn + 14)
      .to(dim, { strokeDashoffset: 0, duration: 6 }, endTurn + 15)
      .to({}, { duration: 6 });

    const names = cells.map((c) => c.dataset.angle);
    const st = sceneTrigger(section, tl, (p) => {
      const t = (p / 100) * tl.duration();
      const i = t >= endTurn ? 7 : Math.min(6, Math.max(0, Math.floor((t - 4 + 1.1) / per)));
      now.textContent = i === 7 ? 'исходники и паспорт' : names[i];
    });
    return () => { ScrollTrigger.removeEventListener('refreshInit', layout); st.kill(); gsap.set(cells, { clearProps: 'all' }); };
  });
}
