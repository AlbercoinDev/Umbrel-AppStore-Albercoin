'use strict';
const $ = id => document.getElementById(id);
const state = {logged: false, target: null, user: null, chats: [], currentTab: 'chats', busy: false, refreshing: false, tick: 0, historySignature: ''};
// Installed by enhancements.js before entering the authenticated workspace.
let refreshChats, refreshHistory, refreshBackups;
async function api(path, data) {
  const response = await fetch('/api/' + path, {method: data === undefined ? 'GET' : 'POST', credentials: 'same-origin', headers: data === undefined ? {} : {'Content-Type':'application/json','X-SimpleX-UI':'1'}, body: data === undefined ? undefined : JSON.stringify(data)});
  let value; try { value = await response.json(); } catch { throw new Error('El servidor no ha devuelto una respuesta válida.'); }
  if (response.status === 401) closeWorkspace();
  if (!response.ok) {
    if (response.status !== 401 && !path.startsWith('diagnostics')) fetch('/api/diagnostics/client', {method:'POST', credentials:'same-origin',headers:{'Content-Type':'application/json','X-SimpleX-UI':'1'},body:JSON.stringify({phase:path.startsWith('history')?'history':path.startsWith('snapshot')?'snapshot':data?.action === 'send'?'send':'other'})}).catch(() => {});
    const error = new Error(value.error || 'No se ha podido completar la operación.'); error.status = response.status; throw error;
  }
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
  $('status').textContent = result.connected ? 'Cliente local conectado' : 'SimpleX iniciándose / desconectado';
  $('status').className = 'badge' + (result.connected ? '' : ' offline');
  $('integration-status').textContent = `Clientes conectados: ${result.integration_connections} · Órdenes recibidas: ${result.integration_commands}` + (result.integration_last_command ? ` · Última orden: ${date(result.integration_last_command)}` : '') + '. Esto confirma la conexión al conector; no acredita la entrega de mensajes.';
  return result.connected;
}
async function refreshAddress() {
  const result = await api('address'); $('address').value = result.address || ''; $('create-address').hidden = !!result.address; $('address-qr').hidden = !result.address;
  if (result.address) $('address-qr').src = '/api/qr?v=' + Date.now();
}
async function refreshIntegration() { const value = await api('integrations'); $('integration-enabled').checked = value.enabled; $('integration-url').value = value.url; }
async function tab(name) {
  state.currentTab = name; for (const section of document.querySelectorAll('.tab')) section.hidden = section.id !== 'tab-' + name;
  for (const button of document.querySelectorAll('[data-tab]')) { button.classList.toggle('active', button.dataset.tab === name); if (button.dataset.tab === name) $('page-title').textContent = button.textContent; }
  notice('');
  if (name === 'profile') { await refreshChats(); await refreshAddress(); }
  if (name === 'integrations') await refreshIntegration();
  if (name === 'backups') await refreshBackups();
  if (name === 'diagnostics') await refreshDiagnostics();
  if (name === 'settings') { const value = await api('settings'); $('smp-servers').value = value.smp_servers; $('xftp-servers').value = value.xftp_servers; $('socks-proxy').value = value.socks_proxy; await refreshNetwork(); }
}
async function enter() { state.logged = true; $('login').hidden = true; $('workspace').hidden = false; if (await refreshStatus()) await refreshChats(); }
$('login-form').addEventListener('submit', async event => { event.preventDefault(); const form = new FormData(event.target); try { await api('login', Object.fromEntries(form)); $('login-error').textContent = ''; event.target.password.value = ''; await enter(); } catch (e) { $('login-error').textContent = e.message; } });
$('show-password-change').addEventListener('click', () => { const form = $('password-form'); form.hidden = !form.hidden; $('password-message').textContent = ''; if (!form.hidden) form.current.focus(); });
$('password-form').addEventListener('submit', async event => { event.preventDefault(); const form = new FormData(event.target); const message = $('password-message'); message.textContent = ''; if (form.get('new') !== form.get('confirm')) { message.textContent = 'Las contraseñas nuevas no coinciden.'; return; } event.submitter.disabled = true; try { await api('password', {current: form.get('current'), new: form.get('new')}); event.target.reset(); event.target.hidden = true; message.textContent = 'Contraseña actualizada. Inicia sesión con la nueva.'; $('login-form').password.focus(); } catch (e) { message.textContent = e.message; } finally { event.submitter.disabled = false; } });
$('logout').addEventListener('click', () => work(async () => { await api('logout', {}); closeWorkspace(); }));
for (const button of document.querySelectorAll('[data-tab]')) button.addEventListener('click', () => work(() => tab(button.dataset.tab)));
$('refresh').addEventListener('click', () => work(async () => { await refreshChats(); await refreshHistory(); }));
$('send-form').addEventListener('submit', event => { event.preventDefault(); if (state.busy || !state.target) return; const target = state.target, text = $('message').value, quote = state.quote; work(async () => { state.busy = true; try { await api('action', {action:'send',target,text,quote}); if (state.drafts[target] === text) delete state.drafts[target]; if (state.target === target && $('message').value === text) { $('message').value = ''; delete state.drafts[target]; } if (state.target === target && state.quote === quote) { state.quote = null; $('quote-context').textContent = ''; } notice('Mensaje aceptado por el cliente. Consulta su estado de entrega en el historial.'); } finally { state.busy = false; } await refreshHistory(); }, $('send')); });
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
setInterval(async () => { if (!state.logged || state.refreshing || document.hidden) return; if (state.eventSource?.readyState === 1 && Date.now() - (state.lastFallback || 0) < 30000) return; state.lastFallback = Date.now(); state.refreshing = true; try { if (await refreshStatus()) { state.tick++; if (state.eventSource?.readyState === 1 || state.tick % 3 === 0) await refreshChats(); if (state.currentTab === 'chats') await refreshHistory(); } } catch (e) { notice(e.message,true); } finally { state.refreshing = false; } }, 4000);
let diagnosticData = null;
const diagnosticTab = elem('button', 'Registros y diagnóstico');
diagnosticTab.dataset.tab = 'diagnostics';
diagnosticTab.addEventListener('click', () => work(() => tab('diagnostics')));
document.querySelector('nav').append(diagnosticTab);
function diagnosticReport(entries) { return JSON.stringify({title:'DIAGNÓSTICO SIMPLEX', exported_at:new Date().toISOString(), state:diagnosticData.state, notes:diagnosticData.notes, entries},null,2); }
async function copyReport(text) {
  try { await navigator.clipboard.writeText(text); notice('Informe copiado. Puedes pegarlo para analizar el fallo.'); }
  catch { const output = $('diagnostic-report'); output.hidden = false; output.textContent = text; const range = document.createRange(); range.selectNodeContents(output); const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range); notice('Informe seleccionado. Usa Copiar en el navegador.'); }
}
function renderDiagnostics() {
  if (!diagnosticData) return;
  const level = $('log-level').value, operation = $('log-operation').value;
  const entries = diagnosticData.entries.filter(e => (!level || e.level === level) && (!operation || (operation === 'network' ? e.component === 'network' : operation === 'connection' ? /connect|process/.test(e.phase) : operation === 'hermes' ? e.source === 'hermes/connector' : operation === 'panel' ? e.component === 'panel' : operation === 'event' ? e.phase === 'event' : e.operation === operation)));
  const list = $('diagnostic-list'); list.replaceChildren();
  for (const entry of [...entries].reverse()) {
    const detail = elem('details', undefined, 'diagnostic-entry');
    detail.append(elem('summary', `${date(entry.at)} · ${entry.level.toUpperCase()} · ${entry.phase} · ${entry.summary}`), elem('pre',JSON.stringify(entry,null,2)));
    const button = elem('button','Copiar incidencia y contexto','secondary');
    button.addEventListener('click', () => work(() => { const index = diagnosticData.entries.indexOf(entry); return copyReport(diagnosticReport(diagnosticData.entries.filter((e,i) => (entry.tracking && e.tracking === entry.tracking) || Math.abs(i-index) <= 10))); }));
    detail.append(button); list.append(detail);
  }
  if (!entries.length) list.append(elem('p','No hay registros con estos filtros.'));
}
async function refreshDiagnostics() {
  diagnosticData = await api('diagnostics');
  $('diagnostic-state').textContent = `Proceso: ${diagnosticData.state.process_running?'en ejecución':'detenido'} · WebSocket local: ${diagnosticData.state.connected?'conectado':'desconectado'} · Último evento: ${diagnosticData.state.last_event || 'ninguno'} · ${diagnosticData.notes}`;
  renderDiagnostics();
}
$('refresh-diagnostics').addEventListener('click', e => work(refreshDiagnostics,e.target));
for (const id of ['log-level','log-operation']) $(id).addEventListener('change',renderDiagnostics);
$('copy-diagnostics').addEventListener('click', e => work(async () => { await refreshDiagnostics(); await copyReport(diagnosticReport(diagnosticData.entries)); },e.target));
$('download-diagnostics').addEventListener('click', e => work(async () => { await refreshDiagnostics(); const url = URL.createObjectURL(new Blob([diagnosticReport(diagnosticData.entries)],{type:'application/json'})); const link = elem('a'); link.href = url; link.download = 'simplex-diagnostico.json'; link.click(); setTimeout(() => URL.revokeObjectURL(url),10000); },e.target));
setInterval(() => { if (state.logged && !document.hidden && state.currentTab === 'diagnostics') work(refreshDiagnostics); },8000);

const help = {
  username:'El usuario del panel es admin. No es tu nombre de contacto SimpleX.',
  password:'Contraseña mostrada en la ficha de Umbrel, salvo que la hayas cambiado.',
  current:'Contraseña actual de acceso al panel.', new:'Entre 8 y 1024 caracteres. Al cambiarla se cierran las sesiones.', confirm:'Repite exactamente la nueva contraseña.',
  message:'Elige un contacto conectado y escribe hasta 8000 caracteres. Una orden aceptada no confirma la entrega; consulta el estado del mensaje.',
  'profile-name':'Nombre que verán tus contactos, hasta 64 caracteres. No es el usuario de acceso al panel.',
  'profile-full':'Nombre completo opcional, compartido como parte de tu perfil; hasta 256 caracteres.',
  address:'Comparte esta dirección o el QR con tu móvil. Después acepta la solicitud en este panel. No es la URL que necesita Hermes.',
  'connect-link':'Pega el enlace SimpleX completo del otro contacto, sin espacios. Puede ser una invitación o una dirección pública.',
  'integration-enabled':'Permite a Hermes usar la misma identidad SimpleX del panel. Pulsa Guardar para aplicar.',
  'integration-url':'Copia esta URL completa en SIMPLEX_WS_URL de Hermes. Contiene el token. El nombre del contenedor solo es accesible desde la red Docker adecuada.',
  'allowed-users':'ID numéricos de tus contactos, visibles en Conversaciones. Ejemplo: 1,3. No escribas @, nombres ni el ID de tu propio perfil. El primero será el destino de notificaciones.',
  'smp-servers':'Servidores que transportan mensajes. Vacío usa los predeterminados. Para personalizarlos usa direcciones smp:// completas separadas por espacios.',
  'xftp-servers':'Servidores de archivos; el panel solo admite texto. Vacío usa los predeterminados. Direcciones xftp:// completas separadas por espacios.',
  'socks-proxy':'Opcional: IPv4:puerto de un proxy SOCKS5 externo. Vacío activa Tor local automáticamente para servidores onion; los públicos conectan directamente.',
  command:'Una orden de SimpleX, por ejemplo /user. No ejecuta comandos Linux. Los errores técnicos se pueden consultar en Registros.',
  'log-level':'Filtra por errores, advertencias o información. El informe general incluye los registros recientes de todas las categorías.',
  'log-operation':'Filtra el recorrido de envío, los eventos recibidos, el proceso o las órdenes del conector Hermes.'
};
for (const input of document.querySelectorAll('input,textarea,select')) {
  const text = help[input.id] || help[input.name]; if (!text) continue;
  const wrapper = elem('span',undefined,'help-wrapper'); const button = elem('button','ⓘ','help-button');
  button.type = 'button'; button.setAttribute('aria-label','Ayuda: '+(input.id || input.name)); button.setAttribute('aria-expanded','false');
  const tip = elem('span',text,'help-tip'); tip.id = 'help-'+(input.id || input.name); tip.setAttribute('role','tooltip');
  input.setAttribute('aria-describedby',tip.id); button.setAttribute('aria-controls',tip.id);
  button.addEventListener('click',event => { event.preventDefault(); const opened = wrapper.classList.toggle('open'); button.setAttribute('aria-expanded',String(opened)); });
  button.addEventListener('keydown',event => { if (event.key === 'Escape') { wrapper.classList.remove('open'); button.setAttribute('aria-expanded','false'); button.blur(); } });
  wrapper.append(button,tip); input.insertAdjacentElement('beforebegin',wrapper);
}
$('status').title = 'Conexión interna con SimpleX; no confirma la conexión a servidores SMP ni la entrega de mensajes.';
// enhancements.js initializes the workspace after installing the chat controls.
