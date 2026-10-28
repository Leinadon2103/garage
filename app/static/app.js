// Behaviour shared by all pages.
// Motion that explains, everywhere in the app (the list-by-list entrance is pure CSS, see app.css "Motion"):
// - parts marked data-reveal build up when they come into view (charts rise, lines draw);
// - numbers count up to their value.
// Further: multi-select dropdowns and copy buttons (bottom of this file).
// Without JavaScript or with "reduce motion" in Windows everything is simply there in its final state: the real
// values are always in the HTML, the animation only counts towards them.
(() => {
  const html = document.documentElement;
  const calm = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const formats = {
    euro: (value, digits) => new Intl.NumberFormat("nl-NL", {
      style: "currency", currency: "EUR", minimumFractionDigits: digits, maximumFractionDigits: digits }).format(value),
    int: value => new Intl.NumberFormat("nl-NL", {maximumFractionDigits: 0}).format(Math.round(value)),
  };
  const easeOut = t => 1 - Math.pow(1 - t, 3);

  function countUp(el) {
    const target = parseFloat(el.dataset.count);
    const digits = parseInt(el.dataset.digits || "2", 10);
    const format = formats[el.dataset.format] || formats.euro;
    if (!isFinite(target) || target === 0) return;
    const final = el.textContent;  // exactly as the server wrote it
    el.setAttribute("aria-label", final.trim());  // screen readers get the final value, not the counting
    const start = performance.now(), duration = 1000;
    const step = now => {
      // clamped: the frame time can lie just before `start`, and a count must never go below zero
      const t = Math.min(Math.max((now - start) / duration, 0), 1);
      el.textContent = t < 1 ? format(target * easeOut(t), digits) : final;
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
    document.querySelectorAll("[data-count]").forEach(el => { if (!el.closest("[data-reveal]")) countUp(el); });
    // parts that come into view together appear one after the other, in reading order (top to bottom)
    const seen = new IntersectionObserver(entries => entries.filter(e => e.isIntersecting).forEach((e, i) => {
      seen.unobserve(e.target);
      setTimeout(() => reveal(e.target), i * 140);
    }), {threshold: 0.15});
    parts.forEach(el => seen.observe(el));
  }

  // Multi-select (details.dropdown with checkboxes): the choice as chips in the header, a search field, and
  // closing when you click elsewhere. Code that ticks boxes itself sends a "change" event to the dropdown.
  function dropdowns() {
    document.querySelectorAll("details.dropdown").forEach(dd => {
      const header = dd.querySelector("summary");
      const placeholder = dd.dataset.placeholder || "Kies…";
      const update = () => {
        const names = [...dd.querySelectorAll("input[type=checkbox]:checked")].map(i => i.parentElement.textContent.trim());
        header.replaceChildren(...(names.length
          ? names.map(n => Object.assign(document.createElement("span"), {className: "chip accent", textContent: n}))
          : [Object.assign(document.createElement("span"), {className: "placeholder", textContent: placeholder})]));
      };
      dd.addEventListener("change", update);
      dd.querySelector("input[type=search]")?.addEventListener("input", e => {
        const q = e.target.value.toLowerCase();
        dd.querySelectorAll("li").forEach(li => li.hidden = !li.textContent.toLowerCase().includes(q));
      });
      document.addEventListener("click", e => { if (!dd.contains(e.target)) dd.open = false; });
      update();
    });
  }

  // Copy buttons (data-copy): the text to the clipboard, with a short confirmation on the button
  function copyButtons() {
    document.querySelectorAll("button[data-copy]").forEach(b => b.addEventListener("click", async () => {
      const label = b.querySelector("span");
      const before = label.textContent;
      try {
        await navigator.clipboard.writeText(b.dataset.copy);
        label.textContent = "Gekopieerd";
      } catch {
        label.textContent = "Kopiëren lukte niet";
      }
      setTimeout(() => { label.textContent = before; }, 1800);
    }));
  }

  html.classList.add("js");
  const ready = () => { start(); dropdowns(); copyButtons(); };
  document.readyState === "loading" ? document.addEventListener("DOMContentLoaded", ready) : ready();
})();
