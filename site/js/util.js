// Общие помощники сцен

/** Прямоугольник элемента без его собственного transform — относительно другого элемента. */
export function natRect(el, base) {
  const t = el.style.transform;
  el.style.transform = 'none';
  const r = el.getBoundingClientRect();
  el.style.transform = t;
  const b = base.getBoundingClientRect();
  return { x: r.left - b.left, y: r.top - b.top, w: r.width, h: r.height };
}

/** Прямоугольник относительно base (transform элемента учитывается). */
export function relRect(el, base) {
  const r = el.getBoundingClientRect();
  const b = base.getBoundingClientRect();
  return { x: r.left - b.left, y: r.top - b.top, w: r.width, h: r.height };
}

/** Включает пункт списка, в диапазон которого попал прогресс (0–100). */
export function stepper(items, ranges, onChange) {
  let cur = -1;
  return (p) => {
    let i = ranges.findIndex(([a, b]) => p >= a && p < b);
    if (i < 0) i = p >= ranges[ranges.length - 1][1] ? ranges.length - 1 : 0;
    if (i === cur) return;
    cur = i;
    items.forEach((el, k) => el.classList.toggle('is-on', k === i));
    onChange?.(i);
  };
}

/** ScrollTrigger сцены с липким экраном: прогресс от верха секции до её низа. */
export function sceneTrigger(section, tl, onProgress, extra = {}) {
  // подписи шагов следуют за видимой анимацией, а не за полосой прокрутки
  if (onProgress) {
    tl.eventCallback('onUpdate', () => onProgress(tl.progress() * 100));
    onProgress(0);
  }
  return ScrollTrigger.create({
    trigger: section,
    start: 'top top',
    end: 'bottom bottom',
    scrub: 0.6,
    animation: tl,
    invalidateOnRefresh: true,
    ...extra,
  });
}
