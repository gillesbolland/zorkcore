(() => {
  const thread = document.getElementById("dm-thread");
  if (!thread) return;
  const bubbles = thread.querySelectorAll(".bubble");

  const reveal = (el) => {
    el.classList.add("visible");
    // Layout updates after display:block — scroll once the bubble has height.
    requestAnimationFrame(() => {
      thread.scrollTo({ top: thread.scrollHeight, behavior: "smooth" });
    });
  };

  let t = 0;
  bubbles.forEach((el) => {
    const delay = Number(el.dataset.delay || 500);
    t += delay;
    if (el.classList.contains("visible") && delay === 0) {
      // Opening "new game" is already painted; keep the pane pinned to the top.
      thread.scrollTop = 0;
      return;
    }
    window.setTimeout(() => reveal(el), t);
  });
})();
