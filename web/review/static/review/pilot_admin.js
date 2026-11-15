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
  const picks = () => [...document.querySelectorAll('input[name="items"][form="pilot-execution"]')];
  const execution = document.querySelector('#pilot-execution');
  const sample = document.querySelector('#pilot-sample');
  const updateSelection = () => {
    const all = document.querySelector('[data-select-all]');
    if (!all) return;
    const items = picks(), selected = items.filter(el => el.checked).length;
    all.checked = items.length > 0 && selected === items.length;
    all.indeterminate = selected > 0 && selected < items.length;
    const stages = [...(execution?.querySelectorAll('input[name="stages"]:checked') || [])].map(el => el.value === 'laya' ? 'Laya' : 'LLM');
    document.querySelector('#pilot-selection-count').textContent = selected + (selected === 1 ? ' norma seleccionada' : ' normas seleccionadas') + (stages.length ? ' · ' + stages.join(' + ') : ' · elegí una etapa');
  };
  sample?.addEventListener('change', event => {
    if (event.target.matches('[data-select-all]')) picks().forEach(el => { el.checked = event.target.checked; });
    updateSelection();
  });
  sample?.addEventListener('click', event => {
    const clear = event.target.closest('[data-select-none]');
    const filter = event.target.closest('[data-select-stage]');
    if (clear) picks().forEach(el => { el.checked = false; });
    if (filter && !filter.disabled && execution) {
      const stage = filter.dataset.selectStage;
      picks().forEach(el => { el.checked = el.dataset[stage] === filter.dataset.selectState; });
      execution.querySelectorAll('input[name="stages"]').forEach(el => { el.checked = el.value === stage; });
      execution.querySelector('.pilot-stages').dispatchEvent(new Event('change', {bubbles:true}));
    }
    if (clear || filter) updateSelection();
  });
  execution?.addEventListener('change', updateSelection);
  updateSelection();

  const detailsKey = el => el.dataset.detailsKey || el.querySelector('summary')?.textContent.trim();
  const focusKey = el => {
    if (el.id) return 'id:' + el.id;
    if (el.getAttribute('aria-label')) return 'label:' + el.getAttribute('aria-label');
    if (el.hasAttribute('data-select-all')) return 'all';
    if (el.hasAttribute('data-select-none')) return 'none';
    if (el.tagName === 'SUMMARY') return 'summary:' + detailsKey(el.parentElement);
    return el.getAttribute('href') ? 'href:' + el.getAttribute('href') : null;
  };
  const previousHtml = new Map();
  const replaceFragment = (id, html) => {
    const target = document.getElementById(id);
    if (!target || typeof html !== 'string' || previousHtml.get(id) === html) return;
    const key = target.contains(document.activeElement) ? focusKey(document.activeElement) : null;
    const expanded = [...target.querySelectorAll('details[open]')].map(detailsKey);
    const scroll = target.querySelector('.table-scroll')?.scrollLeft || 0;
    const selected = new Set(picks().filter(el => el.checked).map(el => el.value));
    target.innerHTML = html;
    target.querySelectorAll('details').forEach(el => { el.open = expanded.includes(detailsKey(el)); });
    if (id === 'pilot-sample') {
      picks().forEach(el => { el.checked = selected.has(el.value); });
      updateSelection();
    }
    if (target.querySelector('.table-scroll')) target.querySelector('.table-scroll').scrollLeft = scroll;
    if (key) {
      const next = [...target.querySelectorAll('a, button, input, summary, [tabindex]')].find(el => focusKey(el) === key && !el.disabled);
      (next || target.querySelector('h2, summary'))?.focus({preventScroll:true});
    }
    previousHtml.set(id, html);
  };
  const live = document.querySelector('#pilot-live');
  if (!live || live.dataset.active !== 'true') return;
  const message = document.querySelector('#pilot-live-status');
  let active = true;
  const refresh = async () => {
    if (document.hidden) { window.setTimeout(refresh, 3000); return; }
    try {
      const response = await fetch(live.dataset.progressUrl, {headers: {'Accept': 'application/json'}, cache: 'no-store', signal: AbortSignal.timeout(10000)});
      if (!response.ok) throw new Error('progress');
      const data = await response.json();
      replaceFragment('pilot-progress', data.progress_html);
      replaceFragment('pilot-sample', data.sample_html);
      replaceFragment('pilot-history', data.history_html);
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
