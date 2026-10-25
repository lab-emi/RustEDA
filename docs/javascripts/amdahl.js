// SPDX-License-Identifier: Apache-2.0
(() => {
  const fraction = document.getElementById('tool-fraction');
  const speed = document.getElementById('tool-speedup');
  if (!fraction || !speed) return;
  function update() {
    const f = Number(fraction.value) / 100;
    const s = Number(speed.value);
    document.getElementById('fraction-value').value = `${fraction.value}%`;
    document.getElementById('tool-value').value = `${s}×`;
    document.getElementById('iteration-speedup').value = `${(1 / (1 - f + f / s)).toFixed(2)}×`;
  }
  fraction.addEventListener('input', update);
  speed.addEventListener('input', update);
  update();
})();
