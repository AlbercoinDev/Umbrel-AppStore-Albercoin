'use strict';
Object.assign(state, {drafts: {}, quote: null, items: [], search: '', historyRequest: 0,
  preferences: {pins: [], preview: false}, eventSource: null, chatsCursor: '', historyMore: false});

function control(tag, id, text, parent) {
  const node = elem(tag, text); node.id = id;
  if (tag === 'button') node.type = 'button';
  parent.append(node); return node;
}
const contactPane = document.querySelector('.contact-pane');
const filters = elem('div', undefined, 'chat-controls');
contactPane.insertBefore(filters, $('chat-list'));
control('input','contact-search',undefined,filters).placeholder = 'Buscar contacto o grupo';
$('contact-search').setAttribute('aria-label','Buscar contacto o grupo');
for (const [id,text] of [['unread-only','Solo no leídos'],['preview-toggle','Mostrar vista previa']]) {
  const label = elem('label',text); const input = control('input',id,undefined,label); input.type = 'checkbox'; filters.append(label);
}
control('button','more-chats','Cargar más conversaciones',contactPane).hidden = true;
const header = document.querySelector('.thread-header');
const actions = elem('details'); actions.append(elem('summary','Opciones de conversación'));
header.append(actions);
for (const [id,label] of [['pin-chat','Fijar / desfijar'],['clear-chat','Vaciar historial local'],['delete-contact','Eliminar contacto'],['leave-group','Salir del grupo']]) control('button',id,label,actions);
const searchForm = control('form','history-search-form',undefined,header);
control('input','history-search',undefined,searchForm).placeholder = 'Buscar en todo el historial';
$('history-search').maxLength = 256; $('history-search').setAttribute('aria-label','Buscar mensajes');
control('button','search-messages','Buscar',searchForm).type = 'submit';
control('button','load-older','Cargar mensajes anteriores',header).hidden = true;
control('button','quote-context','',header).className = 'subtle';
control('button','enable-notifications','Activar notificaciones privadas',filters);
const backupCard = $('create-backup').parentElement;
control('p','backup-storage','',backupCard);
const backupForm = control('form','backup-options',undefined,backupCard);
let label = elem('label','Copias programadas (interrumpen brevemente SimpleX)');
const interval = control('select','backup-interval',undefined,label);
for (const [value,text] of [[0,'Desactivadas'],[24,'Cada día'],[48,'Cada dos días'],[168,'Cada semana']]) { const option = elem('option',text); option.value = value; interval.append(option); }
backupForm.append(label);
label = elem('label','Clave pública age para cifrado (opcional)');
control('input','backup-recipient',undefined,label).placeholder = 'age1…'; backupForm.append(label);
control('button','save-backup-options','Guardar programación y cifrado',backupForm).type = 'submit';
backupCard.append(elem('p','Retención obligatoria: 10 días. Las copias caducadas se eliminan al arrancar y cada minuto. Conserva la clave privada age fuera del nodo; sin ella no podrás restaurar las copias cifradas.','warning'));
document.querySelector('.thread > .fine').textContent = 'Se cargan 100 entradas por página. Usa «Cargar mensajes anteriores» para consultar el resto. Los archivos y las llamadas no están incluidos.';

function resetChat() {
  state.target = null; state.items = []; state.quote = null; state.historyRequest++; state.historySignature = '';
  $('messages').replaceChildren(); $('message').value = ''; $('message').disabled = true; $('send').disabled = true;
  $('chat-title').textContent = 'Elige una conversación'; $('chat-meta').textContent = ''; $('quote-context').textContent = '';
}
function closeWorkspace() {
  snapshotRequest++;
  state.logged = false; state.eventSource?.close(); state.eventSource = null; state.drafts = {}; state.chats = []; resetChat();
  state.notifications = false; clearTimeout(eventTimer); clearTimeout(filterTimer);
  $('chat-list').replaceChildren(); $('console-output').textContent = ''; $('integration-url').value = '';
  $('profile-name').removeAttribute('data-loaded'); $('workspace').hidden = true; $('login').hidden = false;
}
let snapshotRequest = 0;
refreshChats = async function(before = '') {
  const request = ++snapshotRequest;
  const filter = {search:$('contact-search').value.trim(), unread:$('unread-only').checked};
  const signature = JSON.stringify(filter);
  if (state.chatFilter !== signature) { state.chats = []; state.chatsCursor = ''; state.chatFilter = signature; before = ''; }
  const result = await api('snapshot',{...filter,before});
  if (!state.logged || request !== snapshotRequest) return;
  state.user = result.user;
  const incoming = result.chats || [];
  // Keep already loaded pages when refreshing the first page.
  const firstTargets = new Set(incoming.map(c => targetOf(c.chatInfo || {}) || JSON.stringify(c.chatInfo)));
  const combined = before ? [...state.chats,...incoming] : incoming.length < 200 ? incoming : [...incoming,...state.chats.filter(c => !firstTargets.has(targetOf(c.chatInfo || {}) || JSON.stringify(c.chatInfo)))];
  state.chats = [...new Map(combined.map(c => [targetOf(c.chatInfo || {}) || JSON.stringify(c.chatInfo),c])).values()];
  state.preferences = result.preferences || {pins: [], preview: false}; state.preferences.pins ||= [];
  $('preview-toggle').checked = !!state.preferences.preview;
  if (before || !state.chatsCursor) {
    const last = incoming[incoming.length - 1];
    state.chatsCursor = last?.chatInfo?.contact?.chatTs || last?.chatInfo?.groupInfo?.chatTs || last?.chatItems?.[0]?.meta?.itemTs || '';
    $('more-chats').hidden = incoming.length < 200 || !state.chatsCursor;
  }
  renderChats();
};
function renderChats() {
  const list = $('chat-list'); list.replaceChildren(); $('requests').replaceChildren(); let requests = 0;
  const sorted = [...state.chats].sort((a,b) => Number(state.preferences.pins.includes(targetOf(b.chatInfo || {}))) - Number(state.preferences.pins.includes(targetOf(a.chatInfo || {}))));
  for (const chat of sorted) {
    const info = chat.chatInfo || {}, target = targetOf(info);
    if (info.type === 'contactRequest' || (info.contact?.contactRequestId && !info.contact?.activeConn)) {
      requests++; const req = info.contactRequest || info.contact, row = elem('div',undefined,'request'); row.append(elem('span',nameOf(info)));
      for (const [action,text] of [['accept','Aceptar'],['reject','Rechazar']]) {
        const b = elem('button',text); b.addEventListener('click', () => work(async () => { await api('action',{action,id:req.contactRequestId}); state.chats = []; await refreshChats(); },b)); row.append(b);
      }
      $('requests').append(row); continue;
    }
    if (!target) continue;
    const unread = chat.chatStats?.unreadCount || 0;
    if ($('unread-only').checked && !unread) continue;
    if (!nameOf(info).toLocaleLowerCase().includes($('contact-search').value.toLocaleLowerCase())) continue;
    const b = elem('button',undefined,'chat-entry' + (state.target === target ? ' selected' : ''));
    b.append(elem('strong',(state.preferences.pins.includes(target) ? '★ ' : '') + nameOf(info) + (unread ? ` (${unread})` : '')),elem('small',(info.type === 'direct' ? 'Contacto ID ' : 'Grupo ID ') + target.slice(1)));
    const recent = chat.chatItems?.[chat.chatItems.length - 1];
    if (recent) b.append(elem('small',date(recent.meta?.itemTs)));
    if (state.preferences.preview && recent) b.append(elem('small',(recent.content?.msgContent?.text || recent.meta?.itemText || '').slice(0,100)));
    b.addEventListener('click', () => work(async () => {
      if (state.target) state.drafts[state.target] = $('message').value;
      state.target = target; state.items = []; state.quote = null; state.search = ''; state.historyRequest++; state.historySignature = '';
      $('history-search').value = ''; $('quote-context').textContent = ''; $('messages').replaceChildren(); $('messages').dataset.loaded = '';
      $('chat-title').textContent = nameOf(info); $('chat-meta').textContent = target;
      $('message').value = state.drafts[target] || ''; $('message').disabled = false; $('send').disabled = state.busy;
      $('delete-contact').hidden = !target.startsWith('@'); $('leave-group').hidden = !target.startsWith('#');
      renderChats(); await refreshHistory();
    })); list.append(b);
  }
  if (!list.children.length) list.append(elem('p','No hay conversaciones con estos filtros.','muted'));
  if (!requests) $('requests').append(elem('p','No hay solicitudes pendientes.','muted'));
  if (!$('profile-name').dataset.loaded) { $('profile-name').value = state.user.profile?.displayName || ''; $('profile-full').value = state.user.profile?.fullName || ''; $('profile-name').dataset.loaded = '1'; }
}

refreshHistory = async function(older = false) {
  if (!state.target || state.busy) return;
  const target = state.target, search = state.search, request = ++state.historyRequest;
  const before = older ? state.items[0]?.meta?.itemId : null;
  if (older && !before) return;
  const result = await api('history',{target,before,search});
  if (!state.logged || target !== state.target || search !== state.search || request !== state.historyRequest) return;
  const chat = result.chat || result, incoming = (chat.chatItems || []).map(i => i.chatItem || i);
  const merged = older ? [...incoming,...state.items] : [...state.items.filter(i => i.meta?.itemId < (incoming[0]?.meta?.itemId || 0)),...incoming];
  state.items = [...new Map(merged.map(i => [i.meta?.itemId,i])).values()];
  if (older || !state.items.length || state.items.length <= 100) state.historyMore = incoming.length === 100;
  $('load-older').hidden = !state.historyMore;
  const area = $('messages'), oldHeight = area.scrollHeight, oldTop = area.scrollTop;
  const atEnd = oldHeight - oldTop - area.clientHeight < 90;
  const readVisible = async () => {
    const ids = incoming.filter(i => i.meta?.itemStatus?.type === 'rcvNew').map(i => i.meta.itemId);
    if (!older && !search && document.visibilityState === 'visible' && state.currentTab === 'chats' && (atEnd || !area.dataset.loaded) && ids.length) await api('action',{action:'read',target,ids});
  };
  const signature = JSON.stringify(state.items); if (signature === state.historySignature) { await readVisible(); return; } state.historySignature = signature;
  // Preserve unchanged DOM nodes, selection and open menus.
  const existing = new Map([...area.children].filter(n => n.dataset.item).map(n => [n.dataset.item,n]));
  if (!state.items.length) area.replaceChildren(elem('div',search ? 'Sin resultados.' : 'Aún no hay mensajes.','empty'));
  else for (const child of [...area.children]) if (!child.dataset.item) child.remove();
  const keep = new Set();
  for (const item of state.items) {
    const key = String(item.meta?.itemId), version = JSON.stringify(item); keep.add(key);
    let node = existing.get(key);
    if (!node || node.dataset.version !== version) { const replacement = messageNode(item,target); replacement.dataset.item = key; replacement.dataset.version = version; if (node) node.replaceWith(replacement); node = replacement; }
    area.append(node);
  }
  for (const [key,node] of existing) if (!keep.has(key)) node.remove();
  if (older) area.scrollTop = oldTop + area.scrollHeight - oldHeight;
  else if (atEnd || !area.dataset.loaded) area.scrollTop = area.scrollHeight;
  area.dataset.loaded = '1';
  await readVisible();
};
function messageNode(item,target) {
  const message = item.content?.msgContent, sent = (item.chatDir?.type || '').endsWith('Snd');
  const text = message?.text || item.meta?.itemText || '[' + (item.content?.type || 'Evento') + ']';
  const node = elem('div',undefined,'message' + (sent ? ' sent' : '') + (!message ? ' system' : ''));
  const member = item.chatDir?.groupMember;
  if (member) node.append(elem('strong',member.localDisplayName || member.memberProfile?.displayName || 'Participante'));
  if (item.quotedItem) node.append(elem('blockquote',item.quotedItem.content?.text || item.quotedItem.msgContent?.text || item.quotedItem.itemText || 'Mensaje citado'));
  node.append(elem('span',text));
  const status = item.meta?.itemStatus?.type || '';
  const labels = {sndNew:'Pendiente',sndSent:'Enviado al servidor',sndRcvd:'Recibido',sndRead:'Leído',sndError:'Error de envío',sndWarning:'Advertencia de envío',rcvNew:'Recibido',rcvRead:'Leído localmente'};
  node.append(elem('small',[date(item.meta?.itemTs || item.meta?.createdAt),labels[status] || status,item.meta?.itemEdited ? 'Editado' : ''].filter(Boolean).join(' · ')));
  const menu = elem('details'); menu.append(elem('summary','Acciones'));
  const add = (text,fn) => { const b = elem('button',text,'subtle'); b.type = 'button'; b.addEventListener('click', () => work(fn,b)); menu.append(b); };
  add('Copiar', () => copyText(text));
  if (message && item.meta?.itemId) {
    add('Responder',async () => { if (state.target !== target) return; state.quote = item.meta.itemId; $('quote-context').textContent = 'Respondiendo: ' + text.slice(0,100) + ' · Cancelar'; $('message').focus(); });
    if (sent && item.meta?.editable) add('Editar',async () => { const replacement = prompt('Editar mensaje',text); if (replacement === null) return; await api('action',{action:'edit',target,id:item.meta.itemId,text:replacement}); await refreshHistory(); });
    add('Eliminar localmente', () => removeMessage(target,item.meta.itemId,'internal'));
    if (sent) add('Solicitar borrado para todos', () => removeMessage(target,item.meta.itemId,'broadcast'));
  }
  add('Detalles de entrega',async () => alert(JSON.stringify({id:item.meta?.itemId,estado:status,fecha:item.meta?.itemTs},null,2)));
  node.append(menu); return node;
}
async function copyText(text) { try { await navigator.clipboard.writeText(text); notice('Copiado.'); } catch { prompt('Copia el texto:',text); } }
async function destructive(body) {
  try { return await api('action',body); }
  catch (error) {
    if (error.status === undefined || error.status >= 500) {
      // Reconcile instead of automatically replaying an operation with an unknown result.
      state.items = []; state.historySignature = ''; state.chats = []; state.chatsCursor = '';
      try {
        await refreshChats();
        if (body.action === 'delete_contact' && !state.chats.some(c => targetOf(c.chatInfo || {}) === body.target) && state.target === body.target) resetChat();
        else await refreshHistory();
      } catch { /* The client may still be unavailable; do not retry the mutation. */ }
      throw new Error('Resultado incierto. Se ha intentado comprobar el estado actualizado. Revisa la conversación y los registros antes de repetir la operación.');
    }
    throw error;
  }
}
async function removeMessage(target,id,mode) {
  const chat = state.chats.find(c => targetOf(c.chatInfo || {}) === target), name = chat ? nameOf(chat.chatInfo) : target;
  if (!confirm(name + ': ' + (mode === 'internal' ? 'Borrar este mensaje solo del nodo. Seguirá en copias anteriores. ¿Continuar?' : 'Solicitar borrado para todos. No garantiza eliminar copias del destinatario. ¿Continuar?'))) return;
  await destructive({action:'delete_message',target,id,mode,confirm:target});
  if (state.target === target) { state.items = state.items.filter(i => i.meta?.itemId !== id); state.historySignature = ''; await refreshHistory(); }
}
for (const [id,action,warning] of [
  ['clear-chat','clear','Se borrará el historial local conservando el contacto o grupo.'],
  ['delete-contact','delete_contact','Se eliminarán el contacto y su historial local y se notificará la desconexión. Para volver a conversar hará falta reconectar.'],
  ['leave-group','leave','Dejarás de participar en el grupo; no equivale a vaciar el historial.']]) {
  $(id).addEventListener('click', () => work(async () => {
    const target = state.target; if (!target) return;
    if (!confirm(`${$('chat-title').textContent}: ${warning} Las copias anteriores y los dispositivos ajenos no se borran. ¿Continuar?`)) return;
    await destructive({action,target,confirm:target});
    delete state.drafts[target]; state.chats = []; state.chatsCursor = '';
    if (state.target === target) { if (action === 'delete_contact' || action === 'leave') resetChat(); else { state.items = []; state.historySignature = ''; await refreshHistory(); } }
    await refreshChats(); notice('Operación completada.');
  },$(id)));
}
async function savePreferences() { await api('preferences',state.preferences); renderChats(); }
let filterTimer;
$('contact-search').addEventListener('input', () => { renderChats(); clearTimeout(filterTimer); filterTimer = setTimeout(() => work(() => refreshChats()),250); });
$('unread-only').addEventListener('change', () => work(() => refreshChats()));
$('preview-toggle').addEventListener('change', () => work(async () => { state.preferences.preview = $('preview-toggle').checked; await savePreferences(); }));
$('pin-chat').addEventListener('click', () => work(async () => { if (!state.target) return; const pins = state.preferences.pins; state.preferences.pins = pins.includes(state.target) ? pins.filter(p => p !== state.target) : [...pins,state.target]; await savePreferences(); }));
$('more-chats').addEventListener('click', () => work(() => refreshChats(state.chatsCursor),$('more-chats')));
$('load-older').addEventListener('click', () => work(() => refreshHistory(true),$('load-older')));
$('message').addEventListener('input', () => { if (state.target) state.drafts[state.target] = $('message').value; });
$('quote-context').addEventListener('click', () => { state.quote = null; $('quote-context').textContent = ''; });
$('history-search-form').addEventListener('submit',e => { e.preventDefault(); state.search = $('history-search').value.trim(); state.items = []; state.historySignature = ''; work(() => refreshHistory()); });

refreshBackups = async function() {
  const result = await api('backups'), list = $('backup-list'); list.replaceChildren();
  $('backup-storage').textContent = `Libre: ${(result.storage.free/1073741824).toFixed(2)} GB · Datos: ${(result.storage.data/1048576).toFixed(1)} MB · Copias: ${(result.storage.backups/1048576).toFixed(1)} MB`;
  $('backup-interval').value = result.interval; $('backup-recipient').value = result.recipient;
  for (const backup of result.backups) {
    const row = elem('div',undefined,'backup'); row.append(elem('span',`${backup.name} · ${(backup.size/1048576).toFixed(1)} MB · Caduca: ${date(backup.expires)}`));
    const a = elem('a','Descargar'); a.href = '/api/backups/' + encodeURIComponent(backup.name); a.download = backup.name;
    const del = elem('button','Eliminar','secondary'); del.addEventListener('click', () => work(async () => { if (!confirm('Eliminar definitivamente ' + backup.name + '?')) return; await api('backups/delete',{name:backup.name,confirm:backup.name}); await refreshBackups(); },del));
    row.append(a,del); list.append(row);
  }
  if (!result.backups.length) list.append(elem('p','No hay copias vigentes.','muted'));
};
$('backup-options').addEventListener('submit',e => { e.preventDefault(); work(async () => { await api('backup-options',{interval:Number($('backup-interval').value),recipient:$('backup-recipient').value.trim()}); notice('Opciones guardadas. La clave privada debe conservarse fuera del nodo.'); },e.submitter); });

let eventTimer;
function startEvents() {
  if (state.eventSource) return;
  const source = new EventSource('/api/events'); state.eventSource = source;
  source.onmessage = event => {
    try { if (JSON.parse(event.data).invalidate) { state.items = []; state.historySignature = ''; state.chats = []; state.chatsCursor = ''; } } catch { /* Initial refresh signal has no payload. */ }
    clearTimeout(eventTimer);
    eventTimer = setTimeout(() => {
      if (!state.logged) return;
      if (document.hidden) {
        if (state.notifications && 'Notification' in window && Notification.permission === 'granted') new Notification('SimpleX',{body:'Hay actividad nueva. Abre el panel para consultarla.',tag:'simplex-activity'});
        return;
      }
      work(async () => { await refreshChats(); if (state.currentTab === 'chats') await refreshHistory(); });
    },300);
  };
}
const originalEnter = enter;
enter = async function() { await originalEnter(); startEvents(); };
$('enable-notifications').addEventListener('click', () => work(async () => {
  if (!('Notification' in window) || !window.isSecureContext) { notice('Las notificaciones requieren un navegador compatible y HTTPS. Se mantienen los avisos del panel.'); return; }
  state.notifications = await Notification.requestPermission() === 'granted'; notice(state.notifications ? 'Notificaciones de actividad activadas, sin contenido.' : 'Notificaciones no autorizadas.');
}));
enter().catch(closeWorkspace);
