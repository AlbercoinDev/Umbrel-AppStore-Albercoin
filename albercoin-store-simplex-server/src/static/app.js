'use strict';
const labels = {pending: 'pendiente', running: 'en curso', passed: 'correcto', failed: 'fallo'};
async function refreshStatus() {
  try {
    const response = await fetch('/service-status', {cache: 'no-store', redirect: 'error'});
    if (!response.ok) throw new Error('status');
    const state = await response.json();
    document.getElementById('smp-state').textContent = state.smp ? 'proceso activo' : 'detenido';
    document.getElementById('service-error').textContent = state.error;
    document.getElementById('tor-state').textContent = !state.tor ? 'detenido' :
      `proceso activo · ${state.bootstrap ? 'circuitos disponibles' : 'sin circuito confirmado'} · bootstrap ${state.progress === null ? 'desconocido' : state.progress + '%'}`;
    const probe = state.diagnostic;
    const list = document.getElementById('probe-steps');
    list.replaceChildren();
    for (const step of probe.steps) {
      const item = document.createElement('li');
      item.textContent = `${step.label}: ${labels[step.status] || step.status}${step.detail ? ' — ' + step.detail : ''}${step.test_step ? ' [fase CLI: ' + step.test_step + ']' : ''}${step.status === 'failed' && step.signals?.length ? ' [códigos: ' + step.signals.join(', ') + ']' : ''}${step.status === 'failed' && step.exit_status !== undefined ? ' [salida: ' + step.exit_status + ']' : ''}${step.attempts > 1 ? ' [intentos: ' + step.attempts + ']' : ''}${step.seconds !== undefined ? ' (' + step.seconds + ' s)' : ''}`;
      list.appendChild(item);
    }
    document.getElementById('probe-summary').textContent = probe.status === 'running' ?
      'Comprobación en curso; puede tardar hasta 7 minutos. No hace falta volver a pulsar.' :
      probe.status === 'passed' ? `Prueba completa superada mediante Tor independiente. ${probe.finished_at} (${probe.seconds} s).` :
      probe.status === 'failed' ? `Prueba no superada. Código: ${probe.code}. ${probe.finished_at}` :
      'Todavía no se ha verificado el acceso mediante un Tor independiente.';
    document.getElementById('probe-button').disabled = probe.status === 'running' || !state.tor || !state.smp;
  } catch (_) {
    document.getElementById('probe-summary').textContent = 'No se pudo actualizar el estado. Recarga la página y comprueba tu sesión.';
  } finally {
    setTimeout(refreshStatus, 4000);
  }
}
refreshStatus();
