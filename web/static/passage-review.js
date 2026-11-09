function enhancePassageReview() {
  const form = document.getElementById('case-decision');
  if (!form || !form.dataset.proposalKind) return;
  const original = JSON.parse(document.getElementById('passage-original').textContent);
  const splitTags = value => [...new Map(value.split(/[,;\n]/).map(t => t.trim().replace(/\s+/g, ' ')).filter(Boolean).map(t => [t.toLocaleLowerCase('es'), t])).values()];
  const editors = [];
  for (const group of form.querySelectorAll('[data-tag-editor]')) {
    const field = group.querySelector('textarea');
    if (!field) continue;
    const input = document.createElement('input');
    input.type = 'text'; input.id = field.id + '-entry'; input.placeholder = 'Escribí una etiqueta';
    input.setAttribute('aria-label', group.querySelector('label').textContent);
    if (field.getAttribute('aria-invalid') === 'true') input.setAttribute('aria-invalid','true');
    const add = document.createElement('button');
    add.type = 'button'; add.className = 'button'; add.textContent = 'Proponer agregar';
    const entry = document.createElement('div'); entry.className = 'tag-entry'; entry.append(input, add);
    field.hidden = true; field.before(entry); group.querySelector('label').htmlFor = input.id;
    const list = group.querySelector('[data-tag-list]');
    const render = () => {
      list.replaceChildren();
      for (const label of splitTags(field.value)) {
        const chip = document.createElement('span'); chip.className = 'proposed-tag';
        const text = document.createElement('span'); text.textContent = label + ' · Agregar propuesto';
        const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = 'Quitar';
        remove.setAttribute('aria-label', 'Quitar propuesta ' + label);
        remove.addEventListener('click', () => {
          field.value = splitTags(field.value).filter(t => t !== label).join('\n');
          render(); field.dispatchEvent(new Event('input', {bubbles: true}));
        });
        chip.append(text, remove); list.append(chip);
      }
    };
    const commit = () => {
      if (!input.value.trim()) return;
      field.value = splitTags(field.value + '\n' + input.value).join('\n'); input.value = '';
      render(); field.dispatchEvent(new Event('input', {bubbles: true}));
    };
    add.addEventListener('click', commit);
    input.addEventListener('keydown', event => { if (event.key === 'Enter') {event.preventDefault(); commit();} });
    editors.push({commit, input, field, render}); render();
  }
  form.addEventListener('submit', () => editors.forEach(editor => editor.commit()));
  form.addEventListener('review:serialize', event => editors.forEach(({field, input}) => {
    event.detail.set(field.name, splitTags(field.value + '\n' + input.value).join('\n'));
  }));
  form.addEventListener('review:restored', () => {editors.forEach(editor => editor.render()); updateChanges();});
  const changesList = form.querySelector('[data-change-summary]');
  const updateChanges = () => {
    const changes = [];
    if (original.kind === 'topics') {
      for (const [key, label] of [['matters', 'materia'], ['concepts', 'concepto']]) {
        const kept = [...form.querySelectorAll(`input[name="keep_${key}"]:checked`)].map(e => e.value);
        const editor = editors.find(editor => editor.field.name === 'add_' + key);
        const added = splitTags((editor?.field.value || '') + '\n' + (editor?.input.value || ''));
        const proposed = [...kept, ...added];
        changes.push(...original[key].filter(t => !proposed.includes(t)).map(t => `Quitar ${label}: ${t}`));
        changes.push(...added.filter(t => !original[key].includes(t)).map(t => `Agregar ${label}: ${t}`));
      }
    } else {
      const labels = {amending_reference:'Norma modificadora', amending_article:'Artículo modificador', affected_reference:'Norma afectada', affected_article:'Artículo afectado', change_type:'Tipo de cambio', scope:'Alcance', scope_detail:'Inciso o párrafo exacto'};
      for (const [key, label] of Object.entries(labels)) {
        const field = form.elements.namedItem(key);
        if (field && field.value !== original[key]) changes.push(label + ': ' + (field.tagName === 'SELECT' ? field.selectedOptions[0].textContent : field.value || 'Sin dato'));
      }
    }
    changesList.replaceChildren();
    for (const change of changes.length ? changes : ['Sin cambios propuestos.']) {
      const item = document.createElement('li'); item.textContent = change; changesList.append(item);
    }
  };
  form.addEventListener('input', updateChanges); form.addEventListener('change', updateChanges); updateChanges();
  for (const button of form.querySelectorAll('[data-evidence-tab]')) {
    button.addEventListener('click', () => {
      for (const tab of form.querySelectorAll('[data-evidence-tab]')) tab.setAttribute('aria-pressed', String(tab === button));
      for (const panel of form.querySelectorAll('[data-evidence-panel]')) panel.hidden = panel.dataset.evidencePanel !== button.dataset.evidenceTab;
    });
  }
  for (const link of document.querySelectorAll('.passage-steps a')) {
    link.addEventListener('click', () => {
      const target = document.getElementById(link.hash.slice(1));
      if (target?.tagName === 'DETAILS') target.open = true;
      for (const step of document.querySelectorAll('.passage-steps a')) step.removeAttribute('aria-current');
      link.setAttribute('aria-current', 'step');
    });
  }
}
document.addEventListener("review:mounted", enhancePassageReview);
