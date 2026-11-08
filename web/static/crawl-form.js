(() => {
  const form = document.getElementById('crawl-request-form');
  const mode = form.elements.mode;
  const range = document.getElementById('download-fields');
  const existing = document.getElementById('existing-fields');
  const run = form.elements.run;
  const button = document.getElementById('enqueue-request');
  const help = document.getElementById('mode-help');
  const copy = {
    full: ['Descarga el rango indicado, lo clasifica con Laya y prepara la búsqueda semántica y la revisión.', 'Encolar descarga y procesamiento'],
    download: ['Guarda las fuentes y extrae el texto y las relaciones con el parser. Podés clasificar el lote después.', 'Encolar descarga'],
    process: ['Clasifica con Laya el lote que elijas y prepara la búsqueda semántica, usando los textos ya descargados.', 'Encolar procesamiento del lote'],
  };

  function update() {
    const processing = mode.value === 'process';
    const hasBatches = run.options.length > 1;
    range.hidden = range.disabled = processing;
    existing.hidden = existing.disabled = !processing;
    for (const field of [form.elements.start_id, form.elements.end_id]) {
      field.disabled = processing;
      field.required = !processing;
    }
    run.disabled = !processing || !hasBatches;
    run.required = processing && hasBatches;
    document.getElementById('no-batches').hidden = hasBatches;
    button.disabled = processing && !hasBatches;
    [help.textContent, button.textContent] = copy[mode.value] || ['', 'Encolar solicitud'];
  }

  mode.addEventListener('change', update);
  window.addEventListener('pageshow', update);
  update();
})();
