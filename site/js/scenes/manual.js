// Сцена 6. Буклет инструкции листается вокруг корешка
import { sceneTrigger } from '../util.js';

export default function manual(section) {
  const book = section.querySelector('.book');
  const leaves = [...section.querySelectorAll('.leaf')];
  const n = leaves.length;
  const tl = gsap.timeline({ defaults: { ease: 'none', duration: 1 } });
  // закрытая книжка — обложка по центру; стопка листов чуть приподнята
  tl.set(book, { xPercent: -25 }, 0)
    .set(leaves, { z: (i) => (n - i) * 0.6 }, 0)
    .to(book, { xPercent: 0, duration: 8, ease: 'power2.inOut' }, 4);
  leaves.forEach((leaf, i) => {
    const t = 6 + i * 26;
    tl.to(leaf, { rotationY: -180, duration: 20, ease: 'power2.inOut' }, t)
      .to(leaf, { z: (i + 1) * 0.6 + 0.2, duration: 20 }, t);
  });
  tl.to({}, { duration: 8 });
  const st = sceneTrigger(section, tl);
  return () => st.kill();
}
