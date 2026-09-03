/**
 * Integri-TI - Alertas y Telemetría
 * Renderizado de alertas (máximo 4 recientes) y visor de eventos en crudo
 */

// Función utilitaria para sanitizar cadenas HTML y evitar que logs con código rompan el DOM
function escaparHtml(texto) {
  if (!texto) return '';
  return String(texto)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function renderizarAlertasEnVivo(alertas) {
  const cont = document.getElementById('contenedor-alertas');
  const badge = document.getElementById('contador-alertas-badge');
  const textoResumen = document.getElementById('texto-resumen-alertas');
  if (!cont) return;

  const totalAlertas = (alertas || []).length;
  if (badge) badge.textContent = String(totalAlertas);

  if (totalAlertas === 0) {
    cont.className = 'grid grid-cols-1 gap-2.5 font-mono text-xs sm:text-sm';
    cont.innerHTML = `
      <div id="sin-alertas-aviso" class="col-span-full py-6 text-center text-slate-400 text-xs sm:text-sm border border-dashed border-[#1f2937] rounded-lg bg-[#0b0f19]/30">
        No hay alertas registradas en este momento.
      </div>
    `;
    if (textoResumen) textoResumen.textContent = '';
    return;
  }

  // Tomar estrictamente las 4 más recientes
  const cuatroMasRecientes = (alertas || []).slice(0, 4);
  const cant = cuatroMasRecientes.length;

  if (textoResumen) {
    if (totalAlertas > 4) {
      textoResumen.textContent = `Mostrando las ${cant} más recientes de ${totalAlertas} totales`;
    } else {
      textoResumen.textContent = `${totalAlertas} alerta(s) activa(s)`;
    }
  }

  // Ajuste inteligente de columnas: cada alerta toma exactamente su espacio sin dejar columnas vacías
  let gridColsClass = 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-4';
  if (cant === 1) {
    gridColsClass = 'grid-cols-1';
  } else if (cant === 2) {
    gridColsClass = 'grid-cols-1 sm:grid-cols-2';
  } else if (cant === 3) {
    gridColsClass = 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-3';
  } else {
    gridColsClass = 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-4';
  }

  cont.className = `grid ${gridColsClass} gap-2.5 font-mono text-xs sm:text-sm w-full`;

  let html = '';
  cuatroMasRecientes.forEach(a => {
    const niv = (a.nivel || 'Media').toUpperCase();
    let badgeColor = 'border-amber-700 bg-amber-950/50 text-amber-300';
    if (niv === 'CRÍTICA' || niv === 'CRITICA' || niv === 'ALTA') {
      badgeColor = 'border-red-700 bg-red-950/60 text-red-300';
    }

    const ts = (a.timestamp || '').replace('T', ' ').slice(11, 19) || 'reciente';
    const reglaId = escaparHtml(a.regla_id || 'ALERTA');
    const clientId = escaparHtml(a.client_id || 'Estudiante');
    const reglaNombre = escaparHtml(a.regla_nombre || 'Secuencia');
    const mensajeEscapado = escaparHtml(a.mensaje || '');

    html += `
    <div class="flex flex-col justify-between p-2.5 rounded-lg bg-[#0b0f19] border border-[#1f2937] hover:border-slate-600 gap-2 transition-colors min-w-0">
      <div class="flex items-start justify-between gap-1.5">
        <div class="flex items-center gap-1.5 min-w-0">
          <span class="px-2 py-0.5 rounded text-[11px] uppercase font-mono font-bold border ${badgeColor} shrink-0">${niv}</span>
          <span class="px-2 py-0.5 rounded text-[11px] font-mono bg-[#111827] text-slate-300 border border-[#1f2937] shrink-0">${reglaId}</span>
          <span class="text-slate-100 font-semibold text-sm truncate" title="${clientId}">${clientId}</span>
        </div>
        <span class="text-slate-400 text-xs font-mono shrink-0">${ts}</span>
      </div>

      <div class="text-xs sm:text-[13px] text-slate-200 font-mono bg-[#111827] p-2 rounded border border-[#1f2937] leading-normal break-words max-h-24 overflow-hidden">
        ${mensajeEscapado}
      </div>

      <div class="flex items-center justify-between gap-3 pt-1.5 border-t border-[#1f2937] text-xs">
  <span class="text-slate-400 truncate flex-1 min-w-0">${reglaNombre}</span>
  <button onclick="irAAuditoriaCliente('${clientId}')" class="shrink-0 px-2.5 py-1 rounded bg-[#1f2937] hover:bg-[#374151] text-blue-400 hover:text-blue-300 border border-[#374151] text-xs font-medium transition-colors" type="button">Auditoría &rarr;</button>
</div>
    </div>`;
  });

  cont.innerHTML = html;
}

function renderizarTelemetriaRaw(eventosLogs) {
  const cont = document.getElementById('contenedor-telemetria-raw');
  if (!cont) return;

  if (!eventosLogs || eventosLogs.length === 0) {
    cont.innerHTML = `<div class="text-slate-500 text-center py-1 text-xs">Sin eventos recientes.</div>`;
    return;
  }

  let html = '';
  eventosLogs.forEach(ev => {
    const cid = escaparHtml(ev.client_id || '');
    const msg = escaparHtml(ev.mensaje || '');
    html += `
    <div class="flex items-center justify-between p-1 rounded bg-[#111827] border border-[#1f2937] gap-2 text-xs">
      <span class="text-slate-300 font-medium shrink-0 truncate max-w-[130px]">[${cid}]</span>
      <span class="text-slate-400 font-mono truncate flex-1" title="${msg}">${msg}</span>
      <button onclick="irAAuditoriaCliente('${cid}')" class="text-blue-400 hover:text-blue-300 underline shrink-0">auditar</button>
    </div>`;
  });

  cont.innerHTML = html;
}

async function limpiarAlertas() {
  try {
    await Api.limpiarAlertas();
    refrescarDashboard();
    mostrarToast('Alertas descartadas');
  } catch (e) {
    console.error('Error limpiando alertas:', e);
  }
}

async function reanalizarHistorial() {
  try {
    mostrarToast('Reanalizando logs con las reglas activas...');
    const data = await Api.reanalizarHistorial();
    mostrarToast(`Reanálisis: ${data.total_alertas} alertas detectadas`);
    refrescarDashboard();
  } catch (e) {
    console.error('Error reanalizando:', e);
  }
}

function mostrarToast(mensaje, esError = false) {
  const toast = document.getElementById('toast');
  const msg = document.getElementById('toast-msg');
  if (!toast || !msg) return;

  msg.textContent = mensaje;
  toast.className = esError
    ? 'fixed bottom-3 right-3 max-w-sm bg-[#111827] border border-red-700 text-red-200 text-xs sm:text-sm font-mono px-4 py-2.5 rounded shadow-lg pointer-events-none opacity-100 transition-opacity duration-200 flex items-center gap-2.5 z-50'
    : 'fixed bottom-3 right-3 max-w-sm bg-[#111827] border border-slate-700 text-slate-200 text-xs sm:text-sm font-mono px-4 py-2.5 rounded shadow-lg pointer-events-none opacity-100 transition-opacity duration-200 flex items-center gap-2.5 z-50';

  setTimeout(() => {
    toast.classList.remove('opacity-100');
    toast.classList.add('opacity-0');
  }, 2500);
}
