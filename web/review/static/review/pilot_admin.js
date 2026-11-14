(() => {
  'use strict';
  document.querySelectorAll('.pilot-stages').forEach(group => {
    const form = group.closest('form');
    const options = form.querySelector('[data-llm-options]');
    const update = () => {
      const enabled = !!group.querySelector('input[value="llm"]:checked');
      options.hidden = !enabled;
      options.querySelectorAll('input, select').forEach(field => {
        field.disabled = !enabled;
        field.required = enabled;
      });
    };
    group.addEventListener('change', update);
    update();
  });
  const picks = [...document.querySelectorAll('input[name="items"][form="pilot-execution"]')];
  const all = document.querySelector('[data-select-all]');
  if (all && picks.length) {
    const updateSelection = () => {
      const selected = picks.filter(el => el.checked).length;
      all.checked = selected === picks.length;
      all.indeterminate = selected > 0 && selected < picks.length;
      document.querySelector('#pilot-selection-count').textContent = selected + ' seleccionadas';
    };
    picks.forEach(el => el.addEventListener('change', updateSelection));
    all.addEventListener('change', () => { picks.forEach(el => { el.checked = all.checked; }); updateSelection(); });
    document.querySelector('[data-select-none]').addEventListener('click', () => { picks.forEach(el => { el.checked = false; }); updateSelection(); });
    updateSelection();
  }
  const live = document.querySelector('#pilot-live');
  if (!live || live.dataset.active !== 'true') return;
  const message = document.querySelector('#pilot-live-status');
  let active = true;
  let lastSample = '';
  const refresh = async () => {
    if (document.hidden) { window.setTimeout(refresh, 3000); return; }
    try {
      const response = await fetch(live.dataset.progressUrl, {headers: {'Accept': 'application/json'}, cache: 'no-store', signal: AbortSignal.timeout(10000)});
      if (!response.ok) throw new Error('progress');
      const data = await response.json();
      const progressFocus = document.querySelector('#pilot-progress').contains(document.activeElement) ? document.activeElement.id : null;
      document.querySelector('#pilot-progress').innerHTML = data.progress_html;
      if (progressFocus) (document.getElementById(progressFocus) || document.querySelector('#pilot-progress h2')).focus({preventScroll:true});
      if (lastSample !== data.sample_html) {
        const sample = document.querySelector('#pilot-sample');
        const expanded = [...sample.querySelectorAll('details[open]')].map(el => el.dataset.detailsKey);
        const scrollLeft = sample.querySelector('.table-scroll')?.scrollLeft || 0;
        const focusedHref = sample.contains(document.activeElement) ? document.activeElement.getAttribute('href') : null;
        const focusedSummary = sample.contains(document.activeElement) && document.activeElement.tagName === 'SUMMARY'
          ? document.activeElement.parentElement.dataset.detailsKey : null;
        sample.innerHTML = data.sample_html;
        sample.querySelectorAll('details').forEach(el => { el.open = expanded.includes(el.dataset.detailsKey); });
        if (sample.querySelector('.table-scroll')) sample.querySelector('.table-scroll').scrollLeft = scrollLeft;
        if (focusedHref) [...sample.querySelectorAll('a')].find(el => el.getAttribute('href') === focusedHref)?.focus({preventScroll:true});
        if (focusedSummary) [...sample.querySelectorAll('details')].find(el => el.dataset.detailsKey === focusedSummary)?.querySelector('summary').focus({preventScroll:true});
        lastSample = data.sample_html;
      }
      document.querySelector('#pilot-status').textContent = data.status;
      active = data.active;
      message.textContent = active ? '' : 'Avance actualizado: ' + data.status + '.';
    } catch (_) {
      message.textContent = 'No pudimos actualizar el avance. Conservamos lo que ves y volveremos a intentar.';
    }
    if (active) window.setTimeout(refresh, 3000);
  };
  window.setTimeout(refresh, 3000);
})();
