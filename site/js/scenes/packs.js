// Пакеты: сумма по прайсу набегает, перечёркивается, появляется цена пакета
const fmt = (n) => Math.round(n).toLocaleString('ru-RU').replace(/ /g, ' ') + ' ₽';

export default function packs(section) {
  section.querySelectorAll('.pack').forEach((pack) => {
    const items = pack.querySelectorAll('.pack-items li');
    const full = pack.querySelector('.pack-full');
    const price = pack.querySelector('.pack-price .price');
    const sticker = pack.querySelector('.pack-price .sticker');
    const total = +full.dataset.count, final = +price.dataset.count;
    const a = { v: 0 }, b = { v: total };
    // стартовое состояние: сумма ещё не набежала, цена пакета — как по прайсу
    full.textContent = fmt(0);
    price.textContent = fmt(total);
    gsap.set(full, { textDecorationColor: 'rgba(228,0,124,0)' });
    gsap.set(sticker, { opacity: 0, scale: 0.4, rotation: -14 });
    gsap.set(items, { opacity: 0, x: -16 });
    gsap.timeline({ scrollTrigger: { trigger: pack, start: 'top 75%', once: true } })
      .to(items, { opacity: 1, x: 0, duration: 0.35, stagger: 0.08, ease: 'power2.out' })
      .to(a, { v: total, duration: 0.8, ease: 'power2.out', onUpdate: () => { full.textContent = fmt(a.v); } }, 0.1)
      .to(full, { textDecorationColor: 'rgba(228,0,124,1)', duration: 0.3 })
      .to(b, { v: final, duration: 0.7, ease: 'power3.out', onUpdate: () => { price.textContent = fmt(b.v); } })
      .to(sticker, { opacity: 1, scale: 1, rotation: -1.2, duration: 0.45, ease: 'back.out(2)' }, '-=0.2');
  });
}
