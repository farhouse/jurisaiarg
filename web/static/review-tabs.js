(() => {
  const form = document.getElementById('case-decision');
  if (!form) return;
  let dirty = form.dataset.unsaved === 'true';
  form.addEventListener('input', () => { dirty = true; });
  form.addEventListener('change', () => { dirty = true; });
  form.addEventListener('submit', () => { dirty = false; });
  window.addEventListener('beforeunload', event => {
    if (dirty) {
      event.preventDefault();
      event.returnValue = '';
    }
  });
})();
