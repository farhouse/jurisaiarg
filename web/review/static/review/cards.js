(() => {
  const app=document.getElementById('cards-app');
  if (!app) return;
  const frame=app.querySelector('#card-frame'), error=app.querySelector('.card-error'), notice=app.querySelector('.card-notice');
  const key=`jurisai-cards:${app.dataset.user}`;
  let busy=false, pending=null, noteTimer=null, gesture=null;
  try { pending=JSON.parse(sessionStorage.getItem(key) || 'null'); } catch (_) {}
  function showError(message) {
    error.querySelector('p').textContent=message;
    error.hidden=false; error.querySelector('[data-retry]').hidden=!pending;
    error.focus();
  }
  function keepPending(value) {
    pending=value;
    if (value) sessionStorage.setItem(key,JSON.stringify(value)); else sessionStorage.removeItem(key);
  }
  function setBusy(value) {
    busy=value; app.setAttribute('aria-busy',String(value));
    app.querySelectorAll('button').forEach(button=>button.disabled=value);
  }
  function restoreNote() {
    const form=frame.querySelector('.card-note');
    if (!form) return;
    const saved=sessionStorage.getItem(`${key}:note:${form.elements.answer_id.value}`);
    if (saved!==null && saved!==form.elements.notes.value) {
      form.elements.notes.value=saved; form.dataset.dirty='true';
      form.querySelector('.card-note-state').textContent='Comentario recuperado; pendiente de guardar.';
    }
  }
  function replaceFrame(html) {
    frame.innerHTML=html; frame.classList.add('is-updated');
    setTimeout(()=>frame.classList.remove('is-updated'),200);
    restoreNote();
    frame.querySelector('#card-heading')?.focus();
    window.scrollTo({top:0,behavior:'instant'});
  }
  async function send(attempt) {
    if (busy) return false;
    setBusy(true); keepPending(attempt); notice.textContent=attempt.action==='note'?'Guardando comentario…':'Guardando respuesta…';
    try {
      const response=await fetch(attempt.url,{method:'POST',body:new URLSearchParams(attempt.fields),headers:{'X-Requested-With':'XMLHttpRequest'}});
      if (response.redirected) throw new Error('Tu sesión venció. Volvé a ingresar y reintentá; conservamos el guardado pendiente.');
      if (!response.headers.get('content-type')?.includes('application/json')) {
        if (response.status===403 || response.status===404) keepPending(null);
        throw new Error(response.status===403?'La sesión o el permiso cambió. Volvé a ingresar o buscá una tarjeta nueva.':'El servidor no pudo confirmar el guardado. Conservamos la respuesta para reintentar.');
      }
      const data=await response.json();
      if (!response.ok) {
        if (response.status===400 || response.status===409 || response.status===404 || response.status===403) keepPending(null);
        throw new Error(data.error || 'No se pudo guardar. Buscá una tarjeta nueva si la muestra fue retirada.');
      }
      keepPending(null); error.hidden=true; notice.textContent=data.message;
      if (attempt.action==='note') {
        const form=frame.querySelector('.card-note');
        if (form && form.elements.answer_id.value===attempt.answerId) {
          const submitted=attempt.fields.find(([name])=>name==='notes')?.[1] || '';
          form.elements.request_id.value=data.request_id;
          form.dataset.dirty=String(form.elements.notes.value!==submitted);
          form.querySelector('.card-note-state').textContent=form.dataset.dirty==='true'?'Cambios por guardar…':'Comentario guardado.';
          if (form.dataset.dirty==='false') sessionStorage.removeItem(`${key}:note:${attempt.answerId}`);
        }
      } else replaceFrame(data.html);
      return true;
    } catch (failure) {
      notice.textContent='';
      showError(failure.message==='Failed to fetch'?'No se pudo conectar. Conservamos tu respuesta; reintentá para continuar.':failure.message);
      return false;
    } finally { setBusy(false); }
  }
  async function save(form, decision) {
    if (busy) return;
    clearTimeout(noteTimer);
    if (pending) { showError('Hay un guardado pendiente. Reintentá antes de responder otra tarjeta.'); return; }
    const note=frame.querySelector('.card-note');
    if (form.dataset.action!=='note' && note?.dataset.dirty==='true') {
      if (!await save(note)) return;
      if (note.dataset.dirty==='true') { showError('Esperá a que termine de guardarse el comentario.'); return; }
    }
    const values=new FormData(form);
    if (decision) values.set('decision',decision);
    return send({url:form.action,fields:[...values],action:form.dataset.action,answerId:values.get('answer_id')});
  }
  app.addEventListener('submit',event=>{
    const form=event.target.closest('form[data-action]');
    if (!form) return;
    event.preventDefault(); save(form,event.submitter?.value);
  });
  app.addEventListener('input',event=>{
    const form=event.target.closest('.card-note');
    if (!form) return;
    form.dataset.dirty='true';
    sessionStorage.setItem(`${key}:note:${form.elements.answer_id.value}`,form.elements.notes.value);
    clearTimeout(noteTimer); noteTimer=setTimeout(()=>save(form),800);
  });
  app.querySelector('[data-retry]').addEventListener('click',()=>pending&&send(pending));
  app.querySelector('[data-refresh]').addEventListener('click',async()=>{
    if (busy) return;
    if (pending) { showError('El guardado todavía está pendiente. Reintentá para no perder tu respuesta.'); return; }
    const note=frame.querySelector('.card-note');
    if (note?.dataset.dirty==='true' && !await save(note)) return;
    setBusy(true);
    try {
      const response=await fetch(app.dataset.nextUrl);
      if (!response.ok || response.redirected) throw new Error('No se pudo actualizar. Comprobá la conexión y tu sesión.');
      replaceFrame(await response.text()); error.hidden=true;
    } catch (failure) { showError(failure.message); } finally { setBusy(false); }
  });
  document.addEventListener('keydown',event=>{
    if (busy || event.repeat || event.ctrlKey || event.metaKey || event.altKey || event.target.closest('input,textarea,select,[contenteditable="true"]') || getSelection()?.toString()) return;
    const decision=({'1':'correct','2':'incorrect','3':'skip'})[event.key];
    const form=decision?frame.querySelector('.card-answer'):event.key.toLowerCase()==='z'?frame.querySelector('.card-undo'):null;
    if (form) { event.preventDefault(); save(form,decision); }
  });
  app.addEventListener('pointerdown',event=>{
    if (event.pointerType!=='touch' || busy || event.target.closest('button,a,summary,details,.card-evidence,form')) return;
    const card=event.target.closest('.validation-card');
    if (card) gesture={id:event.pointerId,x:event.clientX,y:event.clientY,card};
  });
  app.addEventListener('pointercancel',()=>gesture=null);
  app.addEventListener('pointerup',event=>{
    const start=gesture; gesture=null;
    if (!start || start.id!==event.pointerId || getSelection()?.toString()) return;
    const dx=event.clientX-start.x,dy=event.clientY-start.y;
    if (Math.abs(dx)>=90 && Math.abs(dx)>Math.abs(dy)*1.5) save(start.card.querySelector('.card-answer'),dx>0?'correct':'incorrect');
  });
  window.addEventListener('beforeunload',event=>{
    if (pending || frame.querySelector('.card-note[data-dirty="true"]')) { event.preventDefault(); event.returnValue=''; }
  });
  restoreNote();
  if (pending) showError('Quedó un guardado pendiente de esta pestaña. Reintentá para continuar.');
})();
