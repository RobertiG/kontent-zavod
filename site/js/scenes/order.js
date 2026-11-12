// Как идёт заказ: «товар в пути» едет к складу, шаги студии загораются раньше, чем он доедет
export default function order(section) {
  const steps = [...section.querySelectorAll('.order-steps li')];
  const list = section.querySelector('.order-steps');
  const line = section.querySelector('.track-line i');
  const track = section.querySelector('.track');
  const state = { p: 0 };
  const draw = () => {
    const p = state.p;
    track.style.setProperty('--p', p.toFixed(4));
    list.style.setProperty('--p', p.toFixed(4));
    if (line) line.style.transform = `scaleX(${p})`;
    // все восемь шагов закончены к 85 % пути
    steps.forEach((li, i) => li.classList.toggle('is-on', p >= (i + 0.5) / steps.length * 0.85));
  };
  draw();
  gsap.to(state, {
    p: 1, ease: 'none', onUpdate: draw,
    scrollTrigger: { trigger: list, start: 'top 80%', end: 'bottom 45%', scrub: 0.6 },
  });
}
