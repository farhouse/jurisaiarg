(() => {
  const pending = new Map();
  let chain = Promise.resolve();
  const state = document.querySelector('#pilot-save-state');
  const dirty = new Set();
  if (matchMedia('(max-width:850px)').matches) document.querySelectorAll('.pilot-source details').forEach(el => el.open=false);
  const storageKey = form => `jurisai-pilot:${document.querySelector('.pilot-workspace').dataset.user}:${form.action}:${form.elements.phase.value}:${form.dataset.block}`;
  function showError(form, message, fields = {}) {
    const panel = form.querySelector('.pilot-form-errors');
    for (let el = panel.parentElement; el; el = el.parentElement) if (el.tagName==='DETAILS') el.open=true;
    panel.replaceChildren();
    const strong = document.createElement('strong'); strong.textContent = message; panel.append(strong);
    for (const [name, errors] of Object.entries(fields)) {
      const input = form.elements.namedItem(`${form.dataset.block}-${name}`) || form.elements.namedItem(name);
      const p = document.createElement('p');
      p.textContent = errors.map(error => error.message).join(' '); panel.append(p);
      if (input?.setAttribute) input.setAttribute('aria-invalid', 'true');
    }
    panel.hidden = false; panel.focus();
    state.textContent = 'Hay cambios sin guardar. Corregí el bloque señalado.';
  }
  function queueSave(form) {
    clearTimeout(pending.get(form)); pending.delete(form);
    chain = chain.catch(() => {}).then(async () => {
      const values = new FormData(form);
      const revision = form.elements.namedItem(`${form.dataset.block}-revision`) || form.elements.revision;
      const elapsed = form.elements.namedItem(`${form.dataset.block}-elapsed_ms`);
      if (elapsed) { elapsed.value = Math.min(3600000,Math.max(0,Math.round(performance.now()-(form.editStarted || performance.now())))); values.set(elapsed.name,elapsed.value); }
      values.set(revision.name, revision.value);
      const version = JSON.stringify([...values]);
      try {
        state.textContent = 'Guardando…';
        const response = await fetch(form.action, {method:'POST', body:values, headers:{'X-Requested-With':'XMLHttpRequest'}});
        const result = await response.json();
        if (!response.ok) { showError(form, result.error || 'No se pudo guardar.', result.fields); return; }
        revision.value = result.revision;
        form.editStarted = null;
        form.querySelector('.pilot-form-errors').hidden = true;
        form.querySelectorAll('[aria-invalid]').forEach(input => input.removeAttribute('aria-invalid'));
        form.querySelector('.pilot-block-state').textContent = ({confirmed:'Confirmado', corrected:'Corregido', legal:'Requiere criterio jurídico', pending:'Sin decisión'})[result.decision] || 'Guardado';
        const current = new FormData(form); current.set(revision.name, values.get(revision.name));
        if (JSON.stringify([...current]) === version) { dirty.delete(form); sessionStorage.removeItem(storageKey(form)); }
        state.textContent = dirty.size ? 'Hay cambios por guardar.' : 'Todos los cambios están guardados.';
      } catch (_) { showError(form, 'No se pudo conectar para guardar. Tu edición sigue en esta pestaña; volvé a intentar.'); }
    });
    return chain;
  }
  document.querySelectorAll('.pilot-evaluation').forEach(form => {
    const cached = sessionStorage.getItem(storageKey(form));
    if (cached) {
      try {
        const values = JSON.parse(cached);
        if (values.find(([name]) => name.endsWith('-revision') || name==='revision')?.[1] === (form.elements.namedItem(`${form.dataset.block}-revision`) || form.elements.revision).value) {
          for (const [name, value] of values) {
            if (name==='csrfmiddlewaretoken') continue;
            const controls = form.querySelectorAll(`[name="${CSS.escape(name)}"]`);
            controls.forEach(control => {
              if (control.type==='radio' || control.type==='checkbox') control.checked = values.some(([n,v]) => n===name && v===control.value);
              else control.value = value;
            });
          }
          dirty.add(form); state.textContent = 'Se recuperaron cambios locales. Guardalos antes de continuar.';
        } else showError(form, 'Hay una edición local de otra revisión. Se conserva en esta pestaña; comprobá los datos antes de continuar.');
      } catch (_) { showError(form, 'No se pudo recuperar la edición local. Comprobá el bloque antes de continuar.'); }
    }
    form.addEventListener('focusin', () => { form.editStarted ??= performance.now(); });
    form.addEventListener('input', event => {
      const name = event.target.name || '';
      if (!name.endsWith('-decision') && !name.endsWith('-notes') && !name.endsWith('-revision')) {
        const corrected = form.querySelector('input[value="corrected"]'); if (corrected) corrected.checked = true;
        const correction = form.querySelector('.pilot-correction'); if (correction) correction.open = true;
      }
      dirty.add(form); state.textContent = 'Cambios por guardar…';
      sessionStorage.setItem(storageKey(form), JSON.stringify([...new FormData(form)]));
      clearTimeout(pending.get(form)); pending.set(form, setTimeout(() => queueSave(form), 800));
    });
    form.addEventListener('change', () => {
      if (form.querySelector('input[value="corrected"]')?.checked) form.querySelector('.pilot-correction').open = true;
    });
    form.addEventListener('submit', event => { event.preventDefault(); dirty.add(form); queueSave(form); });
  });
  document.querySelector('#pilot-confirm-all')?.addEventListener('click', async event => {
    const button = event.currentTarget;
    button.disabled = true;
    document.querySelectorAll('.pilot-evaluation').forEach(form => {
      // La ficha no exige aprobar las funciones de cada artículo.
      if (form.dataset.block==='articles' && form.elements.phase.value==='result') return;
      if (form.querySelector('input[value="pending"]')?.checked) {
        const input = form.querySelector('input[value="confirmed"]'); input.checked = true;
        input.dispatchEvent(new Event('input', {bubbles:true}));
      }
    });
    for (const form of [...dirty]) queueSave(form);
    await chain;
    button.disabled = false;
    if (!dirty.size) location.href=button.dataset.nextUrl;
  });
  document.querySelectorAll('.pilot-nav').forEach(link => link.addEventListener('click', async event => {
    if (event.ctrlKey || event.metaKey || event.shiftKey) return;
    event.preventDefault();
    for (const form of [...dirty]) queueSave(form);
    await chain;
    if (!dirty.size) location.href = link.href;
  }));
  window.addEventListener('beforeunload', event => { if (dirty.size) { event.preventDefault(); event.returnValue=''; } });
  document.querySelectorAll('.pilot-evidence').forEach(link => link.addEventListener('click', event => {
    event.preventDefault();
    const unit = document.getElementById(link.getAttribute('href').slice(1));
    if (unit) { document.querySelectorAll('.pilot-located').forEach(el => el.classList.remove('pilot-located'));
      for (let el=unit; el; el=el.parentElement) if (el.tagName==='DETAILS') el.open=true;
      unit.classList.add('pilot-located'); unit.scrollIntoView({block:'center', behavior:'smooth'}); }
  }));
})();
