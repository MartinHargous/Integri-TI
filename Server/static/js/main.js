/**
 * Integri-TI - Entrada Principal del Dashboard
 * Orquestación de sincronización, comandos globales y ciclo de actualización
 */

// 1. SINCRONIZACIÓN PERIÓDICA CON EL SERVIDOR FASTAPI
async function refrescarDashboard() {
  try {
    const data = await Api.getStatus();

    const horaActual = new Date().toLocaleTimeString();
    const lastSyncEl = document.getElementById('last-sync-time');
    if (lastSyncEl) lastSyncEl.textContent = `Sinc: ${horaActual}`;

    const connStatus = document.getElementById('connection-status');
    if (connStatus) {
      connStatus.className = 'px-2 py-0.5 rounded text-[11px] border border-emerald-800 bg-emerald-950/40 text-emerald-300 flex items-center gap-1.5 font-mono';
      connStatus.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-emerald-400"></span> Activo';
    }

    actualizarBotonesComando(data.comando_global || 'ESPERANDO');

    clientesConectadosCache = data.clientes || {};
    renderizarAgentesEnVivo(clientesConectadosCache);
    actualizarSelectDestino(clientesConectadosCache);

    renderizarAlertasEnVivo(data.alertas || []);
    renderizarTelemetriaRaw(data.eventos_logs || []);

  } catch (error) {
    const connStatus = document.getElementById('connection-status');
    if (connStatus) {
      connStatus.className = 'px-2 py-0.5 rounded text-[11px] border border-amber-800 bg-amber-950/40 text-amber-300 flex items-center gap-1.5 font-mono';
      connStatus.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-amber-400"></span> Conectando...';
    }
  }
}

// 2. CAMBIO DE COMANDO GLOBAL DEL EXAMEN
async function cambiarComando(nuevoComando) {
  actualizarBotonesComando(nuevoComando);
  try {
    await Api.cambiarComando(nuevoComando);
    mostrarToast(`Comando global: ${nuevoComando}`);
    refrescarDashboard();
  } catch (err) {
    console.error('Error enviando comando:', err);
  }
}

// 3. ACTUALIZACIÓN VISUAL DE BOTONES DE COMANDO
function actualizarBotonesComando(comando) {
  const btnEsperando = document.getElementById('btn-cmd-esperando');
  const btnGrabando = document.getElementById('btn-cmd-grabando');
  const btnFinalizado = document.getElementById('btn-cmd-finalizado');
  if (!btnEsperando || !btnGrabando || !btnFinalizado) return;

  const cmd = (comando || 'ESPERANDO').toUpperCase();
  const activeClassEsperando = 'px-2.5 py-0.5 rounded text-xs font-medium bg-[#1f2937] text-amber-300 border border-amber-800/80 transition-colors flex items-center gap-1.5';
  const activeClassGrabando = 'px-2.5 py-0.5 rounded text-xs font-medium bg-[#1f2937] text-emerald-300 border border-emerald-800/80 transition-colors flex items-center gap-1.5';
  const activeClassFinalizado = 'px-2.5 py-0.5 rounded text-xs font-medium bg-[#1f2937] text-slate-300 border border-slate-700 transition-colors flex items-center gap-1.5';
  const inactiveClass = 'px-2.5 py-0.5 rounded text-xs text-slate-400 hover:text-slate-200 transition-colors flex items-center gap-1.5 border border-transparent';

  btnEsperando.className = cmd === 'ESPERANDO' ? activeClassEsperando : inactiveClass;
  btnGrabando.className = cmd === 'GRABANDO' ? activeClassGrabando : inactiveClass;
  btnFinalizado.className = cmd === 'FINALIZADO' ? activeClassFinalizado : inactiveClass;
}

// 4. INICIALIZACIÓN AL CARGAR EL DOM
document.addEventListener('DOMContentLoaded', () => {
  actualizarBotonesComando('ESPERANDO');
  refrescarDashboard();
  cargarConfiguracionModulos();
  cargarReglas();
  setInterval(refrescarDashboard, 2000);
});
