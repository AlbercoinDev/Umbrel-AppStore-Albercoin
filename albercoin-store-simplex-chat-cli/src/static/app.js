'use strict';
const $ = id => document.getElementById(id);
const state = {logged: false, target: null, user: null, chats: [], currentTab: 'chats', busy: false, refreshing: false, tick: 0, historySignature: ''};
async function api(path, data) {
  const response = await fetch('/api/' + path, {method: data === undefined ? 'GET' : 'POST', credentials: 'same-origin', headers: data === undefined ? {} : {'Content-Type':'application/json','X-SimpleX-UI':'1'}, body: data === undefined ? undefined : JSON.stringify(data)});
  let value; try { value = await response.json(); } catch { throw new Error('El servidor no ha devuelto una respuesta válida.'); }
  if (response.status === 401) { state.logged = false; $('workspace').hidden = true; $('login').hidden = false; }
  if (!response.ok) throw new Error(value.error || 'No se ha podido completar la operación.');
  return value;
}
function notice(text, error = false) { $('notice').hidden = !text; $('notice').textContent = text; $('notice').className = error ? 'error' : ''; }
async function work(fn, button) { if (button) button.disabled = true; try { await fn(); } catch (e) { notice(e.message, true); } finally { if (button) button.disabled = false; } }
function elem(tag, text, className) { const el = document.createElement(tag); if (text !== undefined) el.textContent = text; if (className) el.className = className; return el; }
function nameOf(info) { const object = info.contact || info.groupInfo || info.contactRequest || {}; return object.localDisplayName || object.profile?.displayName || object.groupProfile?.displayName || 'Conexión pendiente'; }
function targetOf(info) { if (info.type === 'direct') return '@' + info.contact.contactId; if (info.type === 'group') return '#' + info.groupInfo.groupId; return null; }
function date(value) { if (!value) return ''; const result = new Date(typeof value === 'number' ? value * 1000 : value); return Number.isNaN(result.getTime()) ? '' : result.toLocaleString('es-ES'); }
async function refreshStatus() {
  const result = await api('status');
  $('status').textContent = result.connected ? 'SimpleX conectado' : 'SimpleX iniciándose / desconectado';
  $('status').className = 'badge' + (result.connected ? '' : ' offline');
  $('integration-status').textContent = `Clientes conectados: ${result.integration_connections} · Órdenes recibidas: ${result.integration_commands}` + (result.integration_last_command ? ` · Última orden: ${date(result.integration_last_command)}` : '') + '. Esto confirma la conexión al conector; no acredita la entrega de mensajes.';
  return result.connected;
}
async function refreshChats() {
  const result = await api('snapshot'); state.user = result.user; state.chats = result.chats || [];
  const list = $('chat-list'); list.replaceChildren(); $('requests').replaceChildren();
  let requests = 0;
  for (const chat of state.chats) {
    const info = chat.chatInfo || {}; const target = targetOf(info);
    if (info.type === 'contactRequest') {
      requests++; const req = info.contactRequest; const row = elem('div', undefined, 'request'); row.append(elem('span', nameOf(info)));
      for (const [action, label] of [['accept','Aceptar'],['reject','Rechazar']]) {
        const button = elem('button', label, action === 'reject' ? 'secondary' : '');
        button.addEventListener('click', () => work(async () => { await api('action', {action, id: req.contactRequestId}); await refreshChats(); notice('Solicitud actualizada.'); }, button)); row.append(button);
      }
      $('requests').append(row); continue;
    }
    if (!target) continue;
    const button = elem('button', undefined, 'chat-entry' + (state.target === target ? ' selected' : ''));
    button.append(elem('strong', nameOf(info)), elem('small', (info.type === 'direct' ? 'Contacto ID ' : 'Grupo ID ') + target.slice(1)));
    button.addEventListener('click', () => work(async () => {
      state.target = target; state.historySignature = ''; $('chat-title').textContent = nameOf(info); $('chat-meta').textContent = (info.type === 'direct' ? 'Contacto ID ' : 'Grupo ID ') + target.slice(1);
      $('message').disabled = false; $('send').disabled = false;
      await refreshHistory(); await refreshChats();
    })); list.append(button);
  }
  if (!list.children.length) list.append(elem('p', 'Todavía no hay conversaciones. Comparte tu dirección o conecta con un contacto.', 'muted'));
  if (!requests) $('requests').append(elem('p', 'No hay solicitudes pendientes.', 'muted'));
  if (!$('profile-name').dataset.loaded) { $('profile-name').value = state.user.profile?.displayName || ''; $('profile-full').value = state.user.profile?.fullName || ''; $('profile-name').dataset.loaded = '1'; }
}
async function refreshHistory() {
  if (!state.target || state.busy) return;
  const result = await api('history?target=' + encodeURIComponent(state.target));
  const chat = result.chat || result; const items = chat.chatItems || [];
  const signature = JSON.stringify(items); if (signature === state.historySignature) return; state.historySignature = signature;
  const area = $('messages'); const atEnd = area.scrollHeight - area.scrollTop - area.clientHeight < 90; area.replaceChildren();
  if (!items.length) area.append(elem('div', 'Aún no hay mensajes.', 'empty'));
  for (let item of items) {
    item = item.chatItem || item;
    const content = item.content || {}; const message = content.msgContent;
    const direction = item.chatDir?.type || ''; const sent = direction.endsWith('Snd');
    let text = message?.text || item.meta?.itemText || content.text || (message ? `[${message.type || 'Contenido'}]` : `[${content.type || 'Evento'}]`);
    const el = elem('div', undefined, 'message' + (sent ? ' sent' : '') + (!message ? ' system' : ''));
    el.append(elem('span', text)); const status = item.meta?.itemStatus?.type || '';
    el.append(elem('small', [date(item.meta?.itemTs || item.meta?.createdAt), status].filter(Boolean).join(' · '))); area.append(el);
  }
  if (atEnd || !area.dataset.loaded) area.scrollTop = area.scrollHeight; area.dataset.loaded = '1';
}
async function refreshAddress() {
  const result = await api('address'); $('address').value = result.address || ''; $('create-address').hidden = !!result.address; $('address-qr').hidden = !result.address;
  if (result.address) $('address-qr').src = '/api/qr?v=' + Date.now();
}
async function refreshIntegration() { const value = await api('integrations'); $('integration-enabled').checked = value.enabled; $('integration-url').value = value.url; }
async function refreshBackups() {
  const result = await api('backups'); const list = $('backup-list'); list.replaceChildren();
  for (const backup of result.backups) { const row = elem('div', undefined, 'backup'); row.append(elem('span', backup.name + ' · ' + (backup.size/1048576).toFixed(1) + ' MB')); const a = elem('a', 'Descargar'); a.href = '/api/backups/' + encodeURIComponent(backup.name); a.download = backup.name; row.append(a); list.append(row); }
  if (!result.backups.length) list.append(elem('p', 'Todavía no hay copias.', 'muted'));
}
async function tab(name) {
  state.currentTab = name; for (const section of document.querySelectorAll('.tab')) section.hidden = section.id !== 'tab-' + name;
  for (const button of document.querySelectorAll('[data-tab]')) { button.classList.toggle('active', button.dataset.tab === name); if (button.dataset.tab === name) $('page-title').textContent = button.textContent; }
  notice('');
  if (name === 'profile') { await refreshChats(); await refreshAddress(); }
  if (name === 'integrations') await refreshIntegration();
  if (name === 'backups') await refreshBackups();
  if (name === 'settings') { const value = await api('settings'); $('smp-servers').value = value.smp_servers; $('xftp-servers').value = value.xftp_servers; $('socks-proxy').value = value.socks_proxy; }
}
async function enter() { state.logged = true; $('login').hidden = true; $('workspace').hidden = false; if (await refreshStatus()) await refreshChats(); }
$('login-form').addEventListener('submit', async event => { event.preventDefault(); const form = new FormData(event.target); try { await api('login', Object.fromEntries(form)); $('login-error').textContent = ''; event.target.password.value = ''; await enter(); } catch (e) { $('login-error').textContent = e.message; } });
$('logout').addEventListener('click', () => work(async () => { await api('logout', {}); state.logged = false; $('workspace').hidden = true; $('login').hidden = false; $('integration-url').value = ''; }));
for (const button of document.querySelectorAll('[data-tab]')) button.addEventListener('click', () => work(() => tab(button.dataset.tab)));
$('refresh').addEventListener('click', () => work(async () => { await refreshChats(); await refreshHistory(); }));
$('send-form').addEventListener('submit', event => { event.preventDefault(); if (state.busy) return; work(async () => { state.busy = true; try { await api('action', {action:'send',target:state.target,text:$('message').value}); $('message').value = ''; notice('Mensaje aceptado por el cliente. Consulta su estado de entrega en el historial.'); } finally { state.busy = false; } await refreshHistory(); }, $('send')); });
$('profile-form').addEventListener('submit', event => { event.preventDefault(); work(async () => { await api('action', {action:'profile',name:$('profile-name').value,full_name:$('profile-full').value}); notice('Perfil guardado.'); }, event.submitter); });
$('connect-form').addEventListener('submit', event => { event.preventDefault(); work(async () => { await api('action', {action:'connect',link:$('connect-link').value.trim()}); $('connect-link').value = ''; notice('Solicitud enviada. Espera a que el otro contacto acepte.'); await refreshChats(); }, event.submitter); });
$('create-address').addEventListener('click', event => work(async () => { await api('action', {action:'create_address'}); await refreshAddress(); }, event.target));
async function copy(input) { try { await navigator.clipboard.writeText(input.value); notice('Copiado.'); } catch { input.type = 'text'; input.focus(); input.select(); notice('Texto seleccionado. Utiliza Copiar en tu navegador.'); } }
$('copy-address').addEventListener('click', () => copy($('address')));
$('copy-url').addEventListener('click', () => copy($('integration-url')));
$('show-url').addEventListener('click', () => { const input = $('integration-url'); input.type = input.type === 'password' ? 'text' : 'password'; });
$('save-integration').addEventListener('click', event => work(async () => { await api('integrations', {enabled:$('integration-enabled').checked}); await refreshIntegration(); notice('Configuración guardada. Los clientes deberán reconectar.'); }, event.target));
$('rotate-integration').addEventListener('click', event => { if (!confirm('Se revocará la URL anterior y se desconectarán todos los clientes. ¿Continuar?')) return; work(async () => { await api('integrations', {enabled:$('integration-enabled').checked,rotate:true}); await refreshIntegration(); notice('Credenciales renovadas. Actualiza la configuración de Hermes y de los demás clientes.'); }, event.target); });
$('export-hermes').addEventListener('click', event => work(async () => { const value = await api('hermes-config?users=' + encodeURIComponent($('allowed-users').value)); const url = URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'})); const link = elem('a'); link.href = url; link.download = 'simplex-hermes.json'; link.click(); setTimeout(() => URL.revokeObjectURL(url),10000); notice('Configuración descargada. Aplica el procedimiento del paquete para Hermes de Umbrel.'); }, event.target));
$('create-backup').addEventListener('click', event => work(async () => { notice('Creando copia; SimpleX se detendrá brevemente…'); await api('backups', {}); await refreshBackups(); notice('Copia creada. SimpleX está arrancando de nuevo. Descárgala y guárdala fuera del nodo.'); }, event.target));
$('settings-form').addEventListener('submit', event => { event.preventDefault(); if (!confirm('Se reiniciará SimpleX y se desconectarán temporalmente los clientes. ¿Guardar?')) return; work(async () => { await api('settings', {smp_servers:$('smp-servers').value,xftp_servers:$('xftp-servers').value,socks_proxy:$('socks-proxy').value}); notice('Configuración guardada. SimpleX se está reiniciando.'); }, event.submitter); });
$('console-form').addEventListener('submit', event => { event.preventDefault(); work(async () => { const value = await api('action', {action:'console',command:$('command').value}); $('console-output').textContent = JSON.stringify(value,null,2); }, event.submitter); });
setInterval(async () => { if (!state.logged || state.refreshing || document.hidden) return; state.refreshing = true; try { if (await refreshStatus()) { state.tick++; if (state.tick % 3 === 0) await refreshChats(); if (state.currentTab === 'chats') await refreshHistory(); } } catch (e) { notice(e.message,true); } finally { state.refreshing = false; } }, 4000);
enter().catch(() => { state.logged = false; $('workspace').hidden = true; $('login').hidden = false; });
