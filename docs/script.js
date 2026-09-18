(() => {
  const bubbles = document.querySelectorAll("#dm-thread .bubble");
  let t = 0;
  bubbles.forEach((el) => {
    const delay = Number(el.dataset.delay || 500);
    t += delay;
    window.setTimeout(() => {
      el.classList.add("visible");
    }, t);
  });
})();
