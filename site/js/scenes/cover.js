// Сцена 4. Лента стоит, вокруг нашей плитки рисуется кольцо — обложка начинает играть
export default function cover(wall) {
  const ring = wall.querySelector('.ring rect');
  const tiles = wall.querySelectorAll('.wall-grid > img');
  gsap.timeline({ scrollTrigger: { trigger: wall, start: 'top 70%', once: true } })
    .from(tiles, { opacity: 0, y: 24, duration: 0.5, stagger: { each: 0.03, from: 'random' }, ease: 'power2.out' })
    .to(ring, { strokeDashoffset: 0, duration: 0.9, ease: 'power2.inOut' }, 0.35);
}
