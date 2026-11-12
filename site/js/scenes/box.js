// Сцены 2–3. Развёртка рисуется → печать на гранях → лист переворачивается → складывается в 3D → товар внутрь → инструкция → крышка
import { stepper, sceneTrigger } from '../util.js';

export default function box(section, { lite }) {
  const q = (s) => section.querySelector(s);
  const qa = (s) => [...section.querySelectorAll(s)];
  const sheet = q('.box-sheet');
  const F = (n) => q(`[data-face="${n}"]`);
  const cut = q('.dieline .cut');
  const fold = q('.dieline .fold');
  const marksG = q('.dieline .marks-g');
  const dieline = q('.dieline');
  const shadow = q('.box-shadow');
  const product = q('.inbox-product');
  const manual = q('.inbox-manual');
  const items = qa('.box-steps li');
  const printed = ['lid', 'back', 'front', 'side-l', 'side-r', 'bottom', 'tuck'].map((n) => F(n).querySelector(':scope > .skin.out'));
  const tabs = ['tab-bl', 'tab-br', 'tab-fl', 'tab-fr'].map(F);

  const H = 111; // высота коробки в мм — для подъёма товара и инструкции
  const u = () => parseFloat(getComputedStyle(sheet).width) / 652;

  const tl = gsap.timeline({ defaults: { ease: 'none', duration: 1 } });

  // 0–30: развёртка фабрики — рез, сгибы, метки
  tl.fromTo(sheet, { opacity: 0.25 }, { opacity: 1, duration: 4 }, 0)
    .to(cut, { strokeDashoffset: 0, duration: 14, ease: 'power1.inOut' }, 3)
    .to(fold, { opacity: 1, duration: 6 }, 15)
    .fromTo(fold, { strokeDashoffset: 0 }, { strokeDashoffset: -48, duration: 15 }, 15)
    .to(marksG, { opacity: 1, duration: 5 }, 22);

  // 30–46: печать — дизайн проступает на гранях слева направо
  printed.forEach((skin, i) => tl.to(skin, { '--ink': 1, duration: 7, ease: 'power1.inOut' }, 30 + i * 1.4));
  tl.to([marksG, fold], { opacity: 0, duration: 4 }, 44)
    .to(cut, { opacity: 0, duration: 4 }, 46);

  // 48–58: лист переворачивается печатью вниз и ложится на стол
  tl.to(sheet, { '--flip': '180deg', '--tilt': '56deg', '--zoom': 1.08, duration: 10, ease: 'power2.inOut' }, 48);

  // 58–74: клеевые клапаны → стенки (крышка поднимается вместе с задней) → торцы
  tl.to(tabs, { rotationY: (i) => (i % 2 ? 90 : -90), duration: 4, ease: 'power2.inOut' }, 58)
    .to(F('back'), { rotationX: 90, duration: 7, ease: 'power2.inOut' }, 61)
    .to(F('front'), { rotationX: -90, duration: 7, ease: 'power2.inOut' }, 62)
    .to(F('lid'), { rotationX: -16, duration: 6, ease: 'power2.inOut' }, 64)
    .to(F('side-l'), { rotationY: -90, duration: 6, ease: 'power2.inOut' }, 67)
    .to(F('side-r'), { rotationY: 90, duration: 6, ease: 'power2.inOut' }, 68)
    .to(shadow, { opacity: 0.7, duration: 8 }, 62)
    .to(sheet, { '--zoom': 1, '--tilt': '34deg', duration: 8, ease: 'power2.inOut' }, 70);

  // 76–91: товар опускается внутрь, сверху ложится инструкция
  tl.set([product, manual], { opacity: 1 }, 76)
    .fromTo(product, { z: () => -300 * u(), rotationX: 26 }, { z: 0, rotationX: 34, duration: 9, ease: 'power2.out' }, 76)
    .fromTo(manual, { z: () => -(H + 230) * u(), rotationZ: -28, x: () => 60 * u() }, { z: () => -(H - 5) * u(), rotationZ: -5, x: 0, duration: 6, ease: 'power2.out' }, 85);

  // 91–100: крышка закрывается, язычок заправляется, коробка поворачивается в три четверти
  tl.to(F('lid'), { rotationX: 90, duration: 5, ease: 'power2.inOut' }, 91)
    .to(F('tuck'), { rotationX: 90, duration: 2.5, ease: 'power2.inOut' }, 94.5)
    .to(sheet, { '--spin': '-26deg', '--tilt': '60deg', '--zoom': 1.16, duration: 6, ease: 'power2.inOut' }, 93)
    .to(shadow, { opacity: 1, duration: 4 }, 95)
    .to({}, { duration: 1 }, 100);

  const step = stepper(items, [[0, 30], [30, 48], [48, 76], [76, 91], [91, 101]]);
  const st = sceneTrigger(section, tl, step);

  // не тратим кадры на 3D, пока сцены не видно
  if (lite) dieline.style.willChange = 'auto';
  return () => st.kill();
}
