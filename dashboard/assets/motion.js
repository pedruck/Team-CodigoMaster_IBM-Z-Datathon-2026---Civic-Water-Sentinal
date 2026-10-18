(() => {
  // Streamlit may replace the page on a rerun. Dispose the previous enhancement first.
  window.civicWaterMotion?.dispose();
  const root = document.querySelector('.st-key-sentinel-shell');
  const sidebar = document.querySelector('[data-testid="stSidebar"]');
  if (!root || !window.gsap) return;

  const media = gsap.matchMedia();
  let navObserver;
  let frame;
  media.add('(prefers-reduced-motion: no-preference)', (context) => {
    const seen = new WeakSet();
    const reveal = new IntersectionObserver((entries) => {
      const visible = entries.filter(entry => entry.isIntersecting).map(entry => entry.target);
      if (!visible.length) return;
      context.add(() => gsap.fromTo(visible, { y: 12, autoAlpha: 0 }, {
        y: 0, autoAlpha: 1, duration: 0.55, stagger: 0.045,
        ease: 'power3.out', clearProps: 'transform,opacity,visibility',
      }));
      visible.forEach(element => reveal.unobserve(element));
    }, { threshold: 0.08 });
    const discover = () => {
      root.querySelectorAll('[data-sentinel-reveal]').forEach(element => {
        if (seen.has(element)) return;
        seen.add(element);
        reveal.observe(element);
      });
    };
    const mutations = new MutationObserver(() => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(discover);
    });
    mutations.observe(root, { childList: true, subtree: true });
    discover();
    return () => { mutations.disconnect(); reveal.disconnect(); cancelAnimationFrame(frame); };
  }, root);

  // Navigation is real in-page navigation, with the current section exposed to AT.
  const links = sidebar?.querySelectorAll('.workspace-nav a') || [];
  const headings = ['recent-alerts', 'alert-detail', 'history']
    .map(id => document.getElementById(id)).filter(Boolean);
  navObserver = new IntersectionObserver(entries => {
    const current = entries.find(entry => entry.isIntersecting)?.target.id;
    if (!current) return;
    links.forEach(link => {
      const selected = link.hash === `#${current}`;
      link.classList.toggle('selected', selected);
      if (selected) link.setAttribute('aria-current', 'location');
      else link.removeAttribute('aria-current');
    });
  }, { rootMargin: '0px 0px -55% 0px', threshold: 0 });
  headings.forEach(heading => navObserver.observe(heading));
  window.civicWaterMotion = {
    dispose() { media.revert(); navObserver.disconnect(); cancelAnimationFrame(frame); },
  };
})();
