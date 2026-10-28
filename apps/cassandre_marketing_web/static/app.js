// Cassandre marketing site. Presentation only: tab switching, sticky-header
// compaction, and scroll-reveal. No network calls, no forms, nothing submits
// anywhere — this page is static content, not a product surface.

(function () {
  "use strict";

  const header = document.querySelector(".site-header");
  const onScroll = () => {
    if (!header) return;
    header.classList.toggle("is-compact", window.scrollY > 24);
  };
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();

  const tabs = Array.from(document.querySelectorAll(".pillar-tab"));
  const panels = Array.from(document.querySelectorAll(".pillar-panel"));

  function activatePillar(name) {
    tabs.forEach((tab) => {
      const isActive = tab.dataset.pillar === name;
      tab.classList.toggle("active", isActive);
      tab.setAttribute("aria-selected", String(isActive));
    });
    panels.forEach((panel) => {
      panel.classList.toggle("active", panel.dataset.pillarPanel === name);
    });
  }

  tabs.forEach((tab) => {
    tab.addEventListener("click", () => activatePillar(tab.dataset.pillar));
  });

  const revealTargets = document.querySelectorAll(
    ".pillar-copy, .control-record, .control-grid article, .evidence-inner, .trace"
  );
  revealTargets.forEach((el) => el.classList.add("will-reveal"));

  if ("IntersectionObserver" in window) {
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add("is-revealed");
            observer.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.15 }
    );
    revealTargets.forEach((el) => observer.observe(el));
  } else {
    revealTargets.forEach((el) => el.classList.add("is-revealed"));
  }
})();
