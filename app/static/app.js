// Motion that explains: content and charts build up when they come into view, numbers count up to their value.
// Without JavaScript or with "reduce motion" in Windows, everything is simply there in its final state: the real
// values are always in the HTML, the animation only counts towards them.
(() => {
  const html = document.documentElement;
  const calm = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const euro = (value, digits = 2) => new Intl.NumberFormat("nl-NL", {
    style: "currency", currency: "EUR", minimumFractionDigits: digits, maximumFractionDigits: digits,
  }).format(value);
  const easeOut = t => 1 - Math.pow(1 - t, 3);

  function countUp(el) {
    const target = parseFloat(el.dataset.count);
    const digits = parseInt(el.dataset.digits || "2", 10);
    if (!isFinite(target)) return;
    const final = el.textContent;  // exactly as the server wrote it
    el.setAttribute("aria-label", final);  // screen readers get the final value, not the counting
    const start = performance.now(), duration = 1100;
    const step = now => {
      // clamped: the frame time can lie just before `start`, and a count must never go below zero
      const t = Math.min(Math.max((now - start) / duration, 0), 1);
      el.textContent = t < 1 ? euro(target * easeOut(t), digits) : final;
      if (t < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  function reveal(el) {
    el.classList.add("is-in");
    if (!calm) el.querySelectorAll("[data-count]").forEach(countUp);
  }

  function start() {
    const parts = document.querySelectorAll("[data-reveal]");
    if (calm || !("IntersectionObserver" in window)) {
      parts.forEach(el => el.classList.add("is-in"));
      return;
    }
    // parts that come into view together appear one after the other, in reading order (top to bottom)
    const seen = new IntersectionObserver(entries => entries.filter(e => e.isIntersecting).forEach((e, i) => {
      seen.unobserve(e.target);
      setTimeout(() => reveal(e.target), i * 140);
    }), {threshold: 0.15});
    parts.forEach(el => seen.observe(el));
  }

  html.classList.add("js");
  document.readyState === "loading" ? document.addEventListener("DOMContentLoaded", start) : start();
})();
