(() => {
  const workspace = document.getElementById('review-workspace');
  if (!workspace || !window.fetch) return;
  document.documentElement.classList.add('review-enhanced');
  const backupKey = () => `jurisai-review:${workspace.dataset.user}:${workspace.dataset.root}:${form()?.action || mountedURL}`;
  let dirty = false, timer, saving, navigating = false, needsRecovery = false;
  let mountedURL = location.href;
  const form = () => workspace.querySelector('#case-decision');
  const session = () => workspace.querySelector('.review-session');
  const state = (text, failed = false) => {
    workspace.querySelector('[data-save-state]').textContent = text;
    workspace.querySelector('[data-retry-save]').hidden = !failed;
  };
  const values = current => {
    const data = new FormData(current);
    current.dispatchEvent(new CustomEvent('review:serialize', {detail:data}));
    return [...data].filter(([key]) => !['csrfmiddlewaretoken', 'draft_revision'].includes(key));
  };
  const backup = () => {
    if (!form()) return;
    try { sessionStorage.setItem(backupKey(), JSON.stringify({revision:session().dataset.draftRevision, values: values(form())})); }
    catch { /* The unsaved navigation guard still protects this tab. */ }
  };
  const save = async () => {
    clearTimeout(timer);
    if (needsRecovery) return false;
    if (saving) { const saved = await saving; return saved && dirty ? save() : saved; }
    if (!dirty || !form()) return true;
    const current = form(), snapshot = values(current), revision = session().dataset.draftRevision;
    const body = new URLSearchParams(snapshot);
    body.set('csrfmiddlewaretoken', current.elements.csrfmiddlewaretoken.value);
    body.set('draft_revision', revision);
    state('Guardando…');
    saving = (async () => {
      try {
        const response = await fetch(current.action, {method: 'POST', body, credentials: 'same-origin', signal:AbortSignal.timeout(15000), headers: {'X-Review-Async': '1'}});
        const result = await response.json().catch(() => null);
        if (!response.ok) throw new Error(result?.error || 'No se pudo guardar. Revisá la conexión y reintentá.');
        session().dataset.draftRevision = result.revision;
        current.elements.draft_revision.value = result.revision;
        session().dataset.draftCount = result.count;
        dirty = JSON.stringify(values(current)) !== JSON.stringify(snapshot);
        if (dirty) { backup(); state('Cambios pendientes de guardar'); }
        else { sessionStorage.removeItem(backupKey()); state('Guardado · Borrador privado'); }
        return true;
      } catch (error) {
        dirty = true; backup();
        const message = error instanceof TypeError || ['TimeoutError','AbortError'].includes(error.name) ? 'No se pudo guardar. Revisá la conexión y reintentá.' : error.message;
        state(message + ' Tu avance sigue en esta pestaña.', true);
        return false;
      }
    })();
    const result = await saving; saving = null;
    return result && dirty ? save() : result;
  };
  const changed = () => {
    dirty = true; state('Cambios pendientes de guardar');
    // Preserve raw typing before the debounced request, including tag entry fields.
    backup(); clearTimeout(timer); timer = setTimeout(save, 700);
  };
  const focusReviewError = (hash = location.hash) => {
    let target;
    try { target = hash && document.getElementById(decodeURIComponent(hash.slice(1))); } catch { /* Ignore malformed anchors. */ }
    if (target && workspace.contains(target)) {
      if (target.hidden) target = target.closest('[data-tag-editor]')?.querySelector('.tag-entry input') || target;
      for (let parent = target.parentElement; parent && parent !== workspace; parent = parent.parentElement) {
        if (parent.tagName === 'DETAILS') parent.open = true;
      }
      target.classList.add('review-field-target');
    } else target = workspace.querySelector('[data-decision-errors]');
    if (!target || needsRecovery) return false;
    if (!target.matches('input,textarea,select,button,a')) target.tabIndex = -1;
    target.focus({preventScroll:true}); target.scrollIntoView({block:'center'});
    return true;
  };
  const mount = (hash = location.hash) => {
    document.dispatchEvent(new Event('review:mounted'));
    const current = form();
    if (current) {
      const details = current.querySelector('.review-details');
      const updateDetails = () => {
        const status = current.querySelector('[name=status]:checked')?.value;
        if (status === 'rechazada' || current.elements.notes.value || current.querySelector('[name=issues]:checked') || current.dataset.requiresExplanation === 'true' && status === 'validada') details.open = true;
      };
      current.addEventListener('input', changed); current.addEventListener('change', () => { updateDetails(); changed(); });
      current.addEventListener('submit', event => { event.preventDefault(); dirty = true; save(); });
      updateDetails();
      try {
        const recovered = JSON.parse(sessionStorage.getItem(backupKey()));
        if (recovered) {
          const restore = () => {
            const data = new URLSearchParams(recovered.values);
            for (const field of current.elements) {
              if (!field.name || ['csrfmiddlewaretoken', 'draft_revision', 'draft'].includes(field.name)) continue;
              if (['checkbox', 'radio'].includes(field.type)) field.checked = data.getAll(field.name).includes(field.value);
              else field.value = data.get(field.name) || '';
            }
            current.dispatchEvent(new Event('input', {bubbles:true}));
            current.dispatchEvent(new Event('review:restored'));
            updateDetails();
          };
          const data = new URLSearchParams(recovered.values);
          if (recovered.revision !== session().dataset.draftRevision || data.get('revision') !== current.elements.revision.value) {
            needsRecovery = true; current.inert = true;
            const box = document.createElement('aside'); box.className = 'notice';
            const text = document.createElement('p'); text.textContent = 'Hay cambios locales de otra versión. Revisá el borrador de la pantalla y elegí cuál conservar.';
            const recover = document.createElement('button'); recover.type = 'button'; recover.className = 'button'; recover.textContent = 'Recuperar cambios locales';
            const keep = document.createElement('button'); keep.type = 'button'; keep.className = 'button'; keep.textContent = 'Usar borrador del servidor';
            recover.onclick = () => { const latest = current.elements.revision.value; needsRecovery = false; current.inert = false; restore(); current.elements.revision.value = latest; box.remove(); save(); };
            keep.onclick = () => { needsRecovery = false; current.inert = false; sessionStorage.removeItem(backupKey()); box.remove(); state('Borrador del servidor conservado'); };
            box.append(text, recover, keep); workspace.prepend(box);
            state('Elegí qué cambios recuperar antes de continuar.');
          } else { restore(); state('Avance recuperado de esta pestaña. Guardando…'); }
        }
      } catch { state('No se pudo recuperar el avance de esta pestaña.', true); }
    }
    workspace.querySelector('[data-retry-save]').onclick = () => dirty ? save() : navigate(mountedURL, false);
    const rebase = workspace.querySelector('[data-rebase]');
    if (rebase) rebase.onclick = async () => {
      form().elements.revision.value = rebase.dataset.rebase; changed();
      if (await save()) rebase.closest('.review-conflict').remove();
    };
    for (const select of workspace.querySelectorAll('[data-navigate-select]')) select.onchange = () => navigate(select.selectedOptions[0].dataset.url || select.value);
    for (const select of workspace.querySelectorAll('.copy-navigation select[name=version], .review-case-picker select, [data-dossier-version]')) {
      select.onchange = () => {
        const url = new URL(select.form.action);
        url.search = new URLSearchParams(new FormData(select.form));
        navigate(url.href);
      };
    }
    const finalForm = workspace.querySelector('[data-finalize]');
    if (finalForm) finalForm.addEventListener('submit', async event => {
      event.preventDefault();
      const button = finalForm.querySelector('button'), errorBox = finalForm.querySelector('[data-finalize-error]');
      const showError = result => {
        const heading = document.createElement('h3'); heading.textContent = 'No se pudo finalizar la revisión';
        const reason = document.createElement('p'); reason.textContent = result.error;
        errorBox.replaceChildren(heading, reason);
        if (result.errors?.length) {
          const list = document.createElement('ul');
          for (const error of result.errors) {
            const item = document.createElement('li'), label = document.createElement('strong'), message = document.createElement('p'), link = document.createElement('a');
            label.textContent = `${error.context} · ${error.label}`; message.textContent = error.message;
            link.href = error.url; link.className = 'button'; link.textContent = `Ir a ${error.label.toLowerCase()}`;
            item.append(label, message, link); list.append(item);
          }
          errorBox.append(list);
        } else if (result.url) {
          const link = document.createElement('a'); link.href = result.url; link.className = 'button'; link.textContent = 'Volver a revisar'; errorBox.append(link);
        }
        errorBox.hidden = false; errorBox.focus({preventScroll:true}); errorBox.scrollIntoView({block:'center'});
      };
      button.disabled = true; button.textContent = 'Registrando revisión…'; errorBox.hidden = true;
      try {
        const response = await fetch(finalForm.action, {method:'POST', body: new FormData(finalForm), headers:{'X-Review-Async':'1'}, signal:AbortSignal.timeout(15000), credentials:'same-origin'});
        if (!response.ok) {
          showError(await response.json());
        } else await navigate(response.url);
      } catch { showError({error:'No pudimos confirmar el resultado. Revisá la conexión y volvé al resumen antes de reintentar.',url:mountedURL}); }
      finally { button.disabled = false; button.textContent = 'Finalizar revisión de esta norma'; }
    });
    focusReviewError(hash);
  };
  const navigate = async (href, push = true) => {
    if (navigating) return false;
    navigating = true;
    if (!await save()) { navigating = false; return false; }
    workspace.setAttribute('aria-busy', 'true'); workspace.inert = true;
    try {
      const response = await fetch(href, {credentials:'same-origin', signal:AbortSignal.timeout(15000), headers:{'X-Review-Async':'1'}});
      if (!response.ok) throw new Error('No se pudo cargar el artículo. Reintentá; tu avance está guardado.');
      const documentResult = new DOMParser().parseFromString(await response.text(), 'text/html');
      const next = documentResult.querySelector('#review-workspace');
      if (!next) throw new Error('La sesión pudo haber vencido. Volvé a ingresar para continuar.');
      workspace.replaceChildren(...next.childNodes);
      mountedURL = response.url + new URL(href,location.href).hash;
      if (push) history.pushState({}, '', mountedURL);
      document.title = documentResult.title;
      mount(new URL(mountedURL).hash);
      workspace.inert = false;
      const heading = workspace.querySelector('.copy-work-heading h2, .copy-summary h2');
      if (heading && !focusReviewError(new URL(mountedURL).hash)) { heading.tabIndex = -1; heading.focus({preventScroll:true}); }
      return true;
    } catch (error) { state(error.message, true); return false; }
    finally { navigating = false; workspace.inert = false; workspace.removeAttribute('aria-busy'); }
  };
  workspace.addEventListener('click', event => {
    const link = event.target.closest('a[href]');
    if (!link || event.defaultPrevented || event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || link.target) return;
    const url = new URL(link.href);
    if (url.origin !== location.origin || ![location.pathname, `/validar/${workspace.dataset.root}/`].includes(url.pathname) || (link.getAttribute('href').startsWith('#'))) return;
    event.preventDefault(); navigate(url.href);
  });
  window.addEventListener('popstate', async () => {
    const target = location.href;
    if (!await navigate(target, false)) history.pushState({}, '', mountedURL);
  });
  window.addEventListener('beforeunload', event => {
    if (dirty || saving) { backup(); event.preventDefault(); event.returnValue = ''; }
  });
  mount();
})();
