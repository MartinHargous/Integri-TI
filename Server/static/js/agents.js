/**
 * Integri-TI - Agentes
 * Lógica para visualización de matriz de agentes y navegación a auditoría
 */
let clienteSeleccionado = 'global';
let clientesConectadosCache = {};

function renderizarAgentesEnVivo(clientes) {
  const matriz = document.getElementById('matriz-agentes');
  const contador = document.getElementById('agentes-contador');
  if (!matriz) return;

  const lista = Object.entries(clientes || {});
  
  if (lista.length === 0) {
    if (contador) contador.textContent = '(0 activos)';
    matriz.innerHTML = `
      <div id="sin-agentes-aviso" class="col-span-full py-12 flex flex-col items-center justify-center text-center text-slate-400 border border-dashed border-[#1f2937] rounded-lg bg-[#0b0f19]/40 p-5">
        <div class="w-10 h-10 rounded-full bg-[#1f2937] flex items-center justify-center mb-2 text-slate-300">
          <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" />
          </svg>
        </div>
        <span class="text-sm font-semibold text-slate-200">Esperando conexión de clientes...</span>
        <span class="text-xs text-slate-400 mt-1">Inicie <code class="text-blue-400 bg-[#0b0f19] px-1.5 py-0.5 rounded border border-[#1f2937]">python client.py</code> en los equipos</span>
      </div>
    `;
    return;
  }

  if (contador) {
    contador.textContent = `(${lista.length} en red)`;
  }

  let html = '';
  lista.forEach(([clientId, info], index) => {
    const numAg = String(index + 1).padStart(2, '0');
    const isSelected = clienteSeleccionado === clientId;
    const estado = (info.estado || 'ESPERANDO').toUpperCase();
    const ip = info.ip || '127.0.0.1';
    const ultimoVisto = (info.ultimo_visto || '').replace('T', ' ');

    let badgeClass = 'border-amber-800/80 bg-amber-950/50 text-amber-300';
    let dotColor = 'bg-amber-400';
    let statusText = 'Esperando';

    if (estado === 'GRABANDO') {
      badgeClass = 'border-emerald-800/80 bg-emerald-950/50 text-emerald-300';
      dotColor = 'bg-emerald-400';
      statusText = 'Grabando';
    } else if (estado === 'FINALIZADO') {
      badgeClass = 'border-slate-700 bg-slate-800 text-slate-300';
      dotColor = 'bg-slate-500';
      statusText = 'Finalizado';
    }

    const borderClass = isSelected 
      ? 'border-2 border-blue-500 bg-[#141d2e]/70' 
      : 'border border-[#1f2937] hover:border-slate-600 bg-[#0b0f19]';

    html += `
    <div class="${borderClass} rounded-lg p-2.5 flex flex-col justify-between gap-2 transition-colors">
      <div>
        <div class="flex items-start justify-between gap-1.5">
          <div class="min-w-0 flex items-center gap-1.5">
            <span class="text-slate-400 text-xs font-semibold">#${numAg}</span>
            <span class="font-semibold text-slate-100 block text-sm truncate" title="${clientId}">${clientId}</span>
          </div>
          <span class="px-2 py-0.5 text-[11px] font-mono font-medium rounded border ${badgeClass} shrink-0">${estado}</span>
        </div>

        <div class="text-xs text-slate-400 mt-1.5 flex items-center justify-between">
          <span>IP: <span class="text-slate-200 font-mono">${ip}</span></span>
          <span class="flex items-center gap-1.5">
            <span class="w-2 h-2 rounded-full ${dotColor}"></span>
            <span class="text-slate-300 font-medium">${statusText}</span>
          </span>
        </div>
      </div>

      <button onclick="irAAuditoriaCliente('${clientId}')" class="w-full mt-1 py-1.5 px-2.5 rounded bg-[#1f2937] hover:bg-[#374151] text-slate-100 hover:text-white border border-[#374151] text-xs sm:text-sm font-medium flex items-center justify-center gap-1.5 transition-colors" type="button">
        <svg class="w-3.5 h-3.5 text-blue-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
        </svg>
        Auditar Alumno &rarr;
      </button>
    </div>`;
  });

  matriz.innerHTML = html;
}

function irAAuditoriaCliente(clientId) {
  if (!clientId) return;
  window.location.href = `/auditoria/${encodeURIComponent(clientId)}`;
}

function cambiarCuadricula(tipo) {
  const matriz = document.getElementById('matriz-agentes');
  const btn3x3 = document.getElementById('btn-grid-3x3');
  const btn4x3 = document.getElementById('btn-grid-4x3');
  const btn4x4 = document.getElementById('btn-grid-4x4');
  if (!matriz) return;

  const baseClass = 'grid gap-2.5 font-mono text-sm max-h-[400px] xl:max-h-[430px] overflow-y-auto pr-0.5';
  const activeBtn = 'px-2.5 py-1 rounded text-xs bg-[#1f2937] text-slate-200 font-medium border border-slate-700 transition-colors';
  const inactBtn = 'px-2.5 py-1 rounded text-xs text-slate-400 hover:text-slate-200 transition-colors border border-transparent';

  if (btn3x3) btn3x3.className = tipo === '3x3' ? activeBtn : inactBtn;
  if (btn4x3) btn4x3.className = tipo === '4x3' ? activeBtn : inactBtn;
  if (btn4x4) btn4x4.className = tipo === '4x4' ? activeBtn : inactBtn;

  if (tipo === '3x3') {
    matriz.className = `${baseClass} grid-cols-1 sm:grid-cols-2 md:grid-cols-3`;
  } else if (tipo === '4x3') {
    matriz.className = `${baseClass} grid-cols-1 sm:grid-cols-2 md:grid-cols-3 xl:grid-cols-4`;
  } else if (tipo === '4x4') {
    matriz.className = `${baseClass} grid-cols-1 sm:grid-cols-2 md:grid-cols-4`;
  }
}

function actualizarSelectDestino(clientes) {
  const select = document.getElementById('select-destino');
  if (!select) return;

  const idsActuales = Object.keys(clientes || {});
  const idsEnSelect = Array.from(select.options).map(o => o.value).filter(v => v !== 'global' && v !== 'alertas');
  
  if (JSON.stringify(idsActuales.sort()) === JSON.stringify(idsEnSelect.sort())) {
    if (select.value !== clienteSeleccionado && idsActuales.includes(clienteSeleccionado)) {
      select.value = clienteSeleccionado;
    }
    return;
  }

  let options = `<option value="global" ${clienteSeleccionado === 'global' ? 'selected' : ''}>Global</option><option value="alertas" ${clienteSeleccionado === 'alertas' ? 'selected' : ''}>Solo en alerta</option>`;

  idsActuales.forEach(id => {
    const selected = (id === clienteSeleccionado) ? 'selected' : '';
    options += `<option value="${id}" ${selected}>${id}</option>`;
  });

  select.innerHTML = options;
}

function cambiarDestinoSeleccionado(val) {
  clienteSeleccionado = val;
  renderizarAgentesEnVivo(clientesConectadosCache);
  cargarConfiguracionModulos();
}
