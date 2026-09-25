(() => {
  const thread = document.getElementById("dm-thread");
  if (!thread) return;
  const bubbles = thread.querySelectorAll(".bubble");

  const reveal = (el) => {
    el.classList.add("visible");
    // Scroll only the handheld chat pane — not the page.
    thread.scrollTo({ top: thread.scrollHeight, behavior: "smooth" });
  };

  let t = 0;
  bubbles.forEach((el) => {
    const delay = Number(el.dataset.delay || 500);
    t += delay;
    if (el.classList.contains("visible") && delay === 0) {
      reveal(el);
      return;
    }
    window.setTimeout(() => reveal(el), t);
  });
})();
