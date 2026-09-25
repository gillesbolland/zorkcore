(() => {
  const thread = document.getElementById("dm-thread");
  if (!thread) return;
  const bubbles = thread.querySelectorAll(".bubble");

  // Keep the newest message glued to the bottom of the handheld frame
  // (older messages are pushed up, the way real mobile DMs work).
  const pinBottom = () => {
    thread.scrollTop = thread.scrollHeight;
  };

  const reveal = (el) => {
    el.classList.add("visible");
    // Wait for display:block layout, then pin — no smooth scroll jump.
    requestAnimationFrame(() => {
      pinBottom();
      requestAnimationFrame(pinBottom);
    });
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
