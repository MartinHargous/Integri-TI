/**
 * Integri-TI - Reglas de Correlación Secuencial
 * Manejo del listado de reglas y del editor plegable
 */
let reglasActivasCache = [];
let editandoReglaId = null;
let editorAbierto = false;

function toggleEditorRegla(forzarEstado = null) {
  const editor = document.getElementById('editor-regla-contenedor');
  const labelBtn = document.getElementById('label-toggle-editor');
  if (!editor || !labelBtn) return;

  if (forzarEstado !== null) {
    editorAbierto = forzarEstado;
  } else {
    editorAbierto = !editorAbierto;
  }

  if (editorAbierto) {
    editor.classList.remove('hidden');
    labelBtn.textContent = '✕ Cerrar Editor';
  } else {
    editor.classList.add('hidden');
    labelBtn.textContent = '+ Nueva Regla';
    editandoReglaId = null;
  }
}

async function cargarReglas() {
  try {
    reglasActivasCache = await Api.getReglas();
    renderizarListaReglas(reglasActivasCache);
  } catch (e) {
    console.error('Error cargando reglas:', e);
  }
}

function renderizarListaReglas(reglas) {
  const lista = document.getElementById('lista-reglas-guardadas');
  const contador = document.getElementById('contador-reglas');
  if (!lista) return;

  if (contador) contador.textContent = `(${reglas.length} activas)`;

  if (!reglas || reglas.length === 0) {
    lista.innerHTML = `<div class="text-slate-500 text-center py-4 text-xs sm:text-sm">No hay reglas configuradas.</div>`;
    return;
  }

  let html = '';
  reglas.forEach(r => {
    const sev = (r.severidad || 'MEDIA').toUpperCase();
    let badgeSev = 'border-amber-800/80 bg-amber-950/50 text-amber-300';
    if (sev === 'CRÍTICA' || sev === 'CRITICA' || sev === 'ALTA') {
      badgeSev = 'border-red-800/80 bg-red-950/60 text-red-300';
    }

    const secuenciaTexto = (r.pasos || []).map(p => `${p.modulo} (${p.patron})`).join(' -> ');

    html += `
    <div class="p-2 rounded bg-[#0b0f19] border border-[#1f2937] hover:border-slate-600 flex flex-col xs:flex-row xs:items-center justify-between gap-2 transition-colors">
      <div class="flex flex-col gap-0.5 min-w-0">
        <div class="flex items-center gap-2">
          <span class="px-1.5 py-0.5 rounded text-[11px] uppercase font-bold border ${badgeSev}">${sev}</span>
          <span class="text-slate-100 font-semibold text-xs sm:text-sm truncate">${r.id}: ${r.nombre}</span>
          <span class="text-[11px] text-slate-400">(&lt;= ${r.ventana_segundos}s)</span>
        </div>
        <div class="text-xs text-slate-300 font-mono truncate" title="${secuenciaTexto}">${secuenciaTexto}</div>
      </div>
      <div class="flex items-center gap-1.5 shrink-0 self-end xs:self-auto">
        <button onclick="editarRegla('${r.id}')" class="px-2 py-0.5 rounded bg-[#111827] hover:bg-[#1f2937] text-xs text-slate-200 border border-[#374151] transition-colors" type="button">Editar</button>
        <button onclick="eliminarReglaBackend('${r.id}')" class="px-2 py-0.5 rounded bg-[#111827] hover:bg-[#1f2937] text-xs text-slate-400 hover:text-red-400 border border-[#374151] transition-colors" type="button">Eliminar</button>
      </div>
    </div>`;
  });

  lista.innerHTML = html;
}

function agregarPaso(modulo = 'sniffer', patron = '') {
  const contenedor = document.getElementById('contenedor-pasos');
  if (!contenedor) return;

  const numPasos = contenedor.querySelectorAll('.paso-item').length + 1;
  const div = document.createElement('div');
  div.className = 'paso-item flex flex-wrap items-center gap-1.5 font-mono text-xs bg-[#111827] border border-[#1f2937] rounded p-2';
  div.innerHTML = `
    <span class="paso-label px-1.5 py-0.5 rounded bg-[#0b0f19] text-[10px] sm:text-xs text-blue-400 border border-[#1f2937] font-semibold shrink-0">Paso ${numPasos}</span>
    <select class="paso-modulo bg-[#0b0f19] border border-[#1f2937] text-slate-200 rounded px-2 py-1 text-xs focus:outline-none shrink-0">
      <option value="sniffer" ${modulo === 'sniffer' ? 'selected' : ''}>Sniffer</option>
      <option value="keylogger" ${modulo === 'keylogger' ? 'selected' : ''}>Keylogger</option>
      <option value="paperclip" ${modulo === 'paperclip' ? 'selected' : ''}>Paperclip</option>
      <option value="program_monitor" ${modulo === 'program_monitor' ? 'selected' : ''}>Program Monitor</option>
      <option value="error_detection" ${modulo === 'error_detection' ? 'selected' : ''}>Error Detection</option>
    </select>
    <input class="paso-valor bg-[#0b0f19] border border-[#1f2937] text-slate-200 rounded px-2 py-1 text-xs flex-1 min-w-[110px] focus:outline-none" placeholder="patrón (regex o texto)" type="text" value="${patron}">
    <button onclick="eliminarPaso(this)" class="text-slate-400 hover:text-red-400 px-1 text-base leading-none shrink-0" type="button">×</button>
  `;
  contenedor.appendChild(div);
}

function eliminarPaso(btn) {
  const item = btn.closest('.paso-item');
  if (item) {
    item.remove();
    renumerarPasos();
  }
}

function renumerarPasos() {
  const items = document.querySelectorAll('#contenedor-pasos .paso-item');
  items.forEach((item, index) => {
    const label = item.querySelector('.paso-label');
    if (label) label.textContent = `Paso ${index + 1}`;
  });
}

function nuevaRegla() {
  editandoReglaId = null;
  const nombreInput = document.getElementById('regla-nombre-input');
  if (nombreInput) nombreInput.value = 'Nueva Secuencia Sospechosa';
  const contenedor = document.getElementById('contenedor-pasos');
  if (contenedor) {
    contenedor.innerHTML = '';
    agregarPaso('keylogger', '\\[CTRL\\]\\+c');
    agregarPaso('sniffer', 'gemini\\.google\\.com|chatgpt\\.com');
  }
  toggleEditorRegla(true);
}

function editarRegla(reglaId) {
  const regla = reglasActivasCache.find(r => r.id === reglaId);
  if (!regla) return;

  editandoReglaId = reglaId;
  const nombreInput = document.getElementById('regla-nombre-input');
  if (nombreInput) nombreInput.value = regla.nombre || '';

  const ventanaSelect = document.getElementById('select-ventana');
  if (ventanaSelect) ventanaSelect.value = String(regla.ventana_segundos || '30');

  const sevSelect = document.getElementById('select-sev');
  if (sevSelect) sevSelect.value = regla.severidad || 'ALTA';

  const contenedor = document.getElementById('contenedor-pasos');
  if (contenedor) {
    contenedor.innerHTML = '';
    (regla.pasos || []).forEach(p => {
      agregarPaso(p.modulo, p.patron);
    });
  }

  toggleEditorRegla(true);
  mostrarToast(`Editando regla ${reglaId}`);
}

async function guardarReglaBackend() {
  const pasosEl = document.querySelectorAll('#contenedor-pasos .paso-item');
  if (pasosEl.length === 0) {
    mostrarToast('Agregue al menos un paso a la regla', true);
    return;
  }

  const nombre = document.getElementById('regla-nombre-input')?.value.trim() || 'Secuencia Sospechosa';
  const ventana = parseInt(document.getElementById('select-ventana')?.value || '30');
  const sev = document.getElementById('select-sev')?.value || 'ALTA';

  const pasos = [];
  pasosEl.forEach(p => {
    const mod = p.querySelector('.paso-modulo')?.value || 'sniffer';
    const val = p.querySelector('.paso-valor')?.value || '';
    pasos.push({ modulo: mod, patron: val });
  });

  const payload = {
    nombre: nombre,
    severidad: sev,
    ventana_segundos: ventana,
    pasos: pasos
  };

  if (editandoReglaId) {
    payload.id = editandoReglaId;
  }

  const btn = document.getElementById('btn-guardar-regla');
  if (btn) btn.disabled = true;

  try {
    const data = await Api.guardarRegla(payload);
    if (data.status === 'ok') {
      mostrarToast(`Regla ${data.regla.id} guardada activamente`);
      toggleEditorRegla(false);
      cargarReglas();
      refrescarDashboard();
    } else {
      mostrarToast('Error guardando regla', true);
    }
  } catch (e) {
    console.error('Error guardando regla:', e);
    mostrarToast('Error de red al guardar regla', true);
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function eliminarReglaBackend(reglaId) {
  if (!confirm(`¿Eliminar la regla ${reglaId}?`)) return;
  try {
    const res = await Api.eliminarRegla(reglaId);
    if (res.status === 'ok') {
      mostrarToast(`Regla ${reglaId} eliminada`);
      cargarReglas();
      refrescarDashboard();
    }
  } catch (e) {
    console.error('Error eliminando regla:', e);
  }
}
