// Сцена 8. RICH-блок прокручивается внутри рамки телефона, пока секция проходит экран
export default function rich(section) {
  const screen = section.querySelector('.phone-screen');
  const feed = section.querySelector('.phone-feed');
  gsap.to(feed, {
    y: () => -(feed.scrollHeight - screen.clientHeight),
    ease: 'none',
    scrollTrigger: { trigger: section.querySelector('.phone'), start: 'top 75%', end: 'bottom 25%', scrub: 0.6, invalidateOnRefresh: true },
  });
}
