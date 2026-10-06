/**
 * Integri-TI - Módulo Frontend del Comparador de Logs y Similitud Multi-Señal
 * Orquestación de pestañas, configuración dinámica y renderizado de relaciones entre alumnos.
 */

// Valores por defecto canónicos para calibración heurística y scoring
const CONFIG_COMPARADOR_DEFAULT = Object.freeze({
  intervalo_segundos: 60,
  auto_analisis: true,
  k_shingle: 5,
  num_perm: 128,
  ventana_sincronia_seg: 8,
  umbral_codigo: 0.35,
  umbral_portapapeles: 0.50,
  apps_permitidas: 'code, python, pythonw',
  titulos_permitidos: 'integri-ti',
  peso_codigo: 0.45,
  peso_portapapeles: 0.35,
  peso_fugas: 0.15,
  peso_ejecucion: 0.05,
  boost_clip_umbral: 0.85,
  boost_clip_exec_score: 0.70,
  boost_clip_score: 0.50,
  umbral_critico_score: 0.55,
  umbral_critico_jaccard: 0.65,
  umbral_alto_score: 0.35,
  umbral_alto_jaccard: 0.45,
  umbral_alto_clip: 0.70
});

let paresComparadorCache = [];
let paresFiltradosCache = [];
let parDetalleActivo = null;
let configComparadorCache = null;
let pestanaActiva = 'monitoreo';
let estadoExamenGlobal = 'ESPERANDO';
let compCountdownInterval = null;
let compSegundosRestantesLocales = 0;

function notificarEstadoExamenAComparador(estado) {
  const previo = estadoExamenGlobal;
  estadoExamenGlobal = (estado || 'ESPERANDO').toUpperCase();

  if (estadoExamenGlobal === 'FINALIZADO') {
    compSegundosRestantesLocales = 0;
  }

  actualizarEstadoScheduler({
    auto_analisis: configComparadorCache?.auto_analisis ?? CONFIG_COMPARADOR_DEFAULT.auto_analisis,
    intervalo_segundos: configComparadorCache?.intervalo_segundos ?? CONFIG_COMPARADOR_DEFAULT.intervalo_segundos,
    estado_examen: estadoExamenGlobal,
    pausado_por_finalizacion: (estadoExamenGlobal === 'FINALIZADO'),
    segundos_restantes: (estadoExamenGlobal === 'FINALIZADO') ? 0 : compSegundosRestantesLocales
  });

  if (estadoExamenGlobal === 'FINALIZADO' && previo !== 'FINALIZADO') {
    setTimeout(cargarResultadosComparador, 1200);
  }
}

// 1. GESTIÓN DE PESTAÑAS (MONITOREO EN VIVO vs COMPARADOR)
function cambiarPestana(nombre) {
  pestanaActiva = nombre;
  const vistaMonitoreo = document.getElementById('vista-monitoreo');
  const vistaComparador = document.getElementById('vista-comparador');
  const btnMonitoreo = document.getElementById('tab-btn-monitoreo');
  const btnComparador = document.getElementById('tab-btn-comparador');

  const claseActiva = 'px-3 py-1.5 rounded-md text-xs font-medium bg-[#1f2937] text-white border border-[#374151] flex items-center gap-1.5 transition-all shadow-sm';
  const claseInactiva = 'px-3 py-1.5 rounded-md text-xs font-medium text-slate-400 hover:text-slate-200 border border-transparent flex items-center gap-1.5 transition-all';

  if (nombre === 'monitoreo') {
    if (vistaMonitoreo) vistaMonitoreo.classList.remove('hidden');
    if (vistaComparador) vistaComparador.classList.add('hidden');
    if (btnMonitoreo) btnMonitoreo.className = claseActiva;
    if (btnComparador) btnComparador.className = claseInactiva;
  } else if (nombre === 'comparador') {
    if (vistaMonitoreo) vistaMonitoreo.classList.add('hidden');
    if (vistaComparador) vistaComparador.classList.remove('hidden');
    if (btnMonitoreo) btnMonitoreo.className = claseInactiva;
    if (btnComparador) btnComparador.className = claseActiva;

    // Cargar datos al abrir la pestaña
    cargarConfigComparador();
    cargarResultadosComparador();
  }
}

// 2. TOGGLE Y SUBPESTAÑAS DEL MODAL DE CONFIGURACIÓN
function toggleConfigComparador(forzarEstado) {
  const panel = document.getElementById('panel-config-comparador');
  if (!panel) return;

  const abrir = (typeof forzarEstado === 'boolean') ? forzarEstado : panel.classList.contains('hidden');
  if (abrir) {
    if (!configComparadorCache) {
      actualizarFormularioConfig(CONFIG_COMPARADOR_DEFAULT);
    }
    panel.classList.remove('hidden');
    document.body.classList.add('overflow-hidden');
    cargarConfigComparador();
  } else {
    panel.classList.add('hidden');
    document.body.classList.remove('overflow-hidden');
  }
}

function cerrarModalConfigSiBackdrop(e) {
  if (e && e.target && e.target.id === 'panel-config-comparador') {
    toggleConfigComparador(false);
  }
}

function cambiarSubpestanaConfig(subpestanaId) {
  const tabs = ['general', 'ponderaciones', 'umbrales'];
  tabs.forEach(t => {
    const btn = document.getElementById(`tab-cfg-btn-${t}`);
    const sec = document.getElementById(`tab-cfg-sec-${t}`);
    if (t === subpestanaId) {
      if (btn) {
        btn.className = 'px-3 py-1.5 rounded-md text-xs font-semibold bg-purple-950/80 text-purple-300 border border-purple-800/80 shadow-sm flex items-center gap-1.5 transition-all';
      }
      if (sec) sec.classList.remove('hidden');
    } else {
      if (btn) {
        btn.className = 'px-3 py-1.5 rounded-md text-xs font-medium text-slate-400 hover:text-slate-200 border border-transparent flex items-center gap-1.5 transition-all';
      }
      if (sec) sec.classList.add('hidden');
    }
  });
}

// Helper de notificación seguro (usa mostrarToast si existe, o fallback)
function notificarComp(mensaje, esError = false) {
  if (typeof mostrarToast === 'function') {
    mostrarToast(mensaje, esError);
  } else {
    console.log(`[Comparador] ${esError ? 'ERROR: ' : ''}${mensaje}`);
  }
}

// Helpers API auto-contenidos (evitan fallo si api.js fue retenido en caché por el navegador)
async function compFetch(endpoint, opciones = {}) {
  const url = '/api/comparador/' + endpoint;
  const res = await fetch(url, opciones);
  if (!res.ok) {
    let errDetalle = `HTTP ${res.status}`;
    try {
      const j = await res.json();
      if (j && j.mensaje) errDetalle = j.mensaje;
    } catch (_) {}
    throw new Error(errDetalle);
  }
  return await res.json();
}

async function apiRunComparatorNow() {
  if (typeof Api !== 'undefined' && typeof Api.runComparatorNow === 'function') {
    return await Api.runComparatorNow();
  }
  return await compFetch('ejecutar', { method: 'POST' });
}

async function apiGetComparatorConfig() {
  if (typeof Api !== 'undefined' && typeof Api.getComparatorConfig === 'function') {
    return await Api.getComparatorConfig();
  }
  return await compFetch('config?t=' + Date.now(), { method: 'GET' });
}

async function apiSaveComparatorConfig(payload) {
  if (typeof Api !== 'undefined' && typeof Api.saveComparatorConfig === 'function') {
    return await Api.saveComparatorConfig(payload);
  }
  return await compFetch('config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
}

async function apiGetComparatorResults() {
  if (typeof Api !== 'undefined' && typeof Api.getComparatorResults === 'function') {
    return await Api.getComparatorResults();
  }
  return await compFetch('resultados?t=' + Date.now(), { method: 'GET' });
}

// 3. CARGAR CONFIGURACIÓN Y ESTADO DESDE EL SERVIDOR
// rellenarFormulario=false se usa en el polling periódico: refresca el estado del
// scheduler (cuenta regresiva, último análisis) y el caché de configuración, pero NO
// toca los campos del formulario. Si lo hiciera, cualquier valor que el profesor
// esté escribiendo y aún no haya guardado se pisaría con el valor guardado cada 5 s.
// Los demás llamadores (abrir la pestaña, abrir el panel) siguen rellenando el
// formulario, que es lo que se espera al empezar a editar.
async function cargarConfigComparador({ rellenarFormulario = true } = {}) {
  try {
    const data = await apiGetComparatorConfig();
    if (data && data.status === 'ok') {
      configComparadorCache = data.config || {};
      if (rellenarFormulario) actualizarFormularioConfig(configComparadorCache);
      actualizarEstadoScheduler(data.estado || {});
    } else if (rellenarFormulario) {
      actualizarFormularioConfig(CONFIG_COMPARADOR_DEFAULT);
    }
  } catch (err) {
    console.warn('No se pudo cargar la configuración del comparador:', err);
    if (rellenarFormulario && !configComparadorCache) {
      actualizarFormularioConfig(CONFIG_COMPARADOR_DEFAULT);
    }
  }
}

function actualizarSumaPesosUI() {
  const c = parseFloat(document.getElementById('cfg-comp-pesocodigo')?.value) || 0;
  const p = parseFloat(document.getElementById('cfg-comp-pesoclip')?.value) || 0;
  const f = parseFloat(document.getElementById('cfg-comp-pesofugas')?.value) || 0;
  const e = parseFloat(document.getElementById('cfg-comp-pesoexec')?.value) || 0;
  const suma = Math.round((c + p + f + e) * 100) / 100;
  const el = document.getElementById('cfg-comp-suma-pesos');
  if (el) {
    el.textContent = `${suma.toFixed(2)} (${Math.round(suma * 100)}%)`;
    if (Math.abs(suma - 1.0) < 0.01) {
      el.className = 'font-bold text-emerald-400 font-mono';
    } else {
      el.className = 'font-bold text-amber-400 font-mono';
    }
  }
}

function actualizarFormularioConfig(cfg) {
  const datos = Object.assign({}, CONFIG_COMPARADOR_DEFAULT, cfg || {});

  const setVal = (id, val) => {
    const el = document.getElementById(id);
    if (el) el.value = (val !== undefined && val !== null) ? val : '';
  };

  setVal('cfg-comp-intervalo', datos.intervalo_segundos);
  const elAuto = document.getElementById('cfg-comp-auto');
  if (elAuto) elAuto.checked = Boolean(datos.auto_analisis);
  setVal('cfg-comp-kshingle', datos.k_shingle);
  setVal('cfg-comp-numperm', String(datos.num_perm));
  setVal('cfg-comp-ventanasync', datos.ventana_sincronia_seg);
  setVal('cfg-comp-umbralcodigo', datos.umbral_codigo);
  setVal('cfg-comp-umbralclip', datos.umbral_portapapeles);
  setVal('cfg-comp-apps', datos.apps_permitidas);
  setVal('cfg-comp-titulos', datos.titulos_permitidos);

  // Ponderaciones del Score
  setVal('cfg-comp-pesocodigo', datos.peso_codigo);
  setVal('cfg-comp-pesoclip', datos.peso_portapapeles);
  setVal('cfg-comp-pesofugas', datos.peso_fugas);
  setVal('cfg-comp-pesoexec', datos.peso_ejecucion);

  // Consideraciones y Escalación
  setVal('cfg-comp-boostclipumbral', datos.boost_clip_umbral);
  setVal('cfg-comp-boostclipexec', datos.boost_clip_exec_score);
  setVal('cfg-comp-boostclipscore', datos.boost_clip_score);

  // Umbrales de Severidad
  setVal('cfg-comp-criticoscore', datos.umbral_critico_score);
  setVal('cfg-comp-criticojaccard', datos.umbral_critico_jaccard);
  setVal('cfg-comp-altoscore', datos.umbral_alto_score);
  setVal('cfg-comp-altojaccard', datos.umbral_alto_jaccard);
  setVal('cfg-comp-altoclip', datos.umbral_alto_clip);

  actualizarSumaPesosUI();
}

function actualizarEstadoScheduler(est) {
  if (est.estado_examen) {
    estadoExamenGlobal = est.estado_examen.toUpperCase();
  }
  const esFinalizado = (estadoExamenGlobal === 'FINALIZADO') || !!est.pausado_por_finalizacion;

  compSegundosRestantesLocales = esFinalizado ? 0 : (est.segundos_restantes || 0);

  const elSegundos = document.getElementById('comp-segundos-restantes');
  if (elSegundos) elSegundos.textContent = String(compSegundosRestantesLocales);

  const elUltimo = document.getElementById('comp-ultimo-analisis');
  if (elUltimo) {
    elUltimo.textContent = est.ultimo_analisis ? `Último: ${est.ultimo_analisis}` : 'Último: Pendiente';
  }

  const elTextoEstado = document.getElementById('texto-comp-estado');
  const elBadgeEstado = document.getElementById('badge-comp-estado');
  const elCountdown = document.getElementById('comp-countdown');

  if (elTextoEstado && elBadgeEstado) {
    if (esFinalizado) {
      elTextoEstado.textContent = 'Detenido (Examen Finalizado)';
      elBadgeEstado.className = 'px-2 py-0.5 rounded text-[11px] font-medium bg-slate-800 text-amber-300 border border-amber-700/60 flex items-center gap-1.5';
      if (elCountdown) {
        elCountdown.innerHTML = 'Auto-análisis: <strong class="text-amber-400">Detenido</strong> <span class="text-slate-500">(Examen finalizado)</span>';
      }
    } else if (est.auto_analisis && est.intervalo_segundos > 0) {
      elTextoEstado.textContent = `Auto (cada ${est.intervalo_segundos}s)`;
      elBadgeEstado.className = 'px-2 py-0.5 rounded text-[11px] font-medium bg-purple-950/60 text-purple-300 border border-purple-800/60 flex items-center gap-1.5';
      if (elCountdown) {
        elCountdown.innerHTML = 'Próximo análisis en: <strong id="comp-segundos-restantes" class="text-purple-300">' + compSegundosRestantesLocales + '</strong>s';
      }
    } else {
      elTextoEstado.textContent = 'Manual / En pausa';
      elBadgeEstado.className = 'px-2 py-0.5 rounded text-[11px] font-medium bg-slate-800 text-slate-300 border border-slate-700 flex items-center gap-1.5';
      if (elCountdown) {
        elCountdown.innerHTML = '<span class="text-slate-500">Auto-análisis en pausa</span>';
      }
    }
  }
}

// 4. GUARDAR CONFIGURACIÓN EN EL SERVIDOR
async function guardarConfigComparador(e) {
  if (e) e.preventDefault();

  const getFloat = (id, def) => {
    const val = parseFloat(document.getElementById(id)?.value);
    return isNaN(val) ? def : val;
  };
  const getInt = (id, def) => {
    const val = parseInt(document.getElementById(id)?.value, 10);
    return isNaN(val) ? def : val;
  };

  const payload = {
    intervalo_segundos: getInt('cfg-comp-intervalo', CONFIG_COMPARADOR_DEFAULT.intervalo_segundos),
    auto_analisis: !!document.getElementById('cfg-comp-auto')?.checked,
    k_shingle: getInt('cfg-comp-kshingle', CONFIG_COMPARADOR_DEFAULT.k_shingle),
    num_perm: getInt('cfg-comp-numperm', CONFIG_COMPARADOR_DEFAULT.num_perm),
    ventana_sincronia_seg: getInt('cfg-comp-ventanasync', CONFIG_COMPARADOR_DEFAULT.ventana_sincronia_seg),
    umbral_codigo: getFloat('cfg-comp-umbralcodigo', CONFIG_COMPARADOR_DEFAULT.umbral_codigo),
    umbral_portapapeles: getFloat('cfg-comp-umbralclip', CONFIG_COMPARADOR_DEFAULT.umbral_portapapeles),
    apps_permitidas: (document.getElementById('cfg-comp-apps')?.value || CONFIG_COMPARADOR_DEFAULT.apps_permitidas).trim(),
    titulos_permitidos: (document.getElementById('cfg-comp-titulos')?.value || CONFIG_COMPARADOR_DEFAULT.titulos_permitidos).trim(),

    // Ponderaciones del Score Compuesto
    peso_codigo: getFloat('cfg-comp-pesocodigo', CONFIG_COMPARADOR_DEFAULT.peso_codigo),
    peso_portapapeles: getFloat('cfg-comp-pesoclip', CONFIG_COMPARADOR_DEFAULT.peso_portapapeles),
    peso_fugas: getFloat('cfg-comp-pesofugas', CONFIG_COMPARADOR_DEFAULT.peso_fugas),
    peso_ejecucion: getFloat('cfg-comp-pesoexec', CONFIG_COMPARADOR_DEFAULT.peso_ejecucion),

    // Consideraciones y Escalación
    boost_clip_umbral: getFloat('cfg-comp-boostclipumbral', CONFIG_COMPARADOR_DEFAULT.boost_clip_umbral),
    boost_clip_exec_score: getFloat('cfg-comp-boostclipexec', CONFIG_COMPARADOR_DEFAULT.boost_clip_exec_score),
    boost_clip_score: getFloat('cfg-comp-boostclipscore', CONFIG_COMPARADOR_DEFAULT.boost_clip_score),

    // Umbrales de Severidad
    umbral_critico_score: getFloat('cfg-comp-criticoscore', CONFIG_COMPARADOR_DEFAULT.umbral_critico_score),
    umbral_critico_jaccard: getFloat('cfg-comp-criticojaccard', CONFIG_COMPARADOR_DEFAULT.umbral_critico_jaccard),
    umbral_alto_score: getFloat('cfg-comp-altoscore', CONFIG_COMPARADOR_DEFAULT.umbral_alto_score),
    umbral_alto_jaccard: getFloat('cfg-comp-altojaccard', CONFIG_COMPARADOR_DEFAULT.umbral_alto_jaccard),
    umbral_alto_clip: getFloat('cfg-comp-altoclip', CONFIG_COMPARADOR_DEFAULT.umbral_alto_clip)
  };

  try {
    const res = await apiSaveComparatorConfig(payload);
    if (res.status === 'ok') {
      configComparadorCache = res.config;
      actualizarEstadoScheduler(res.estado || {});
      notificarComp('Ponderaciones y configuración guardadas. Re-ejecutando análisis...');
      toggleConfigComparador(false);
      await ejecutarComparadorAhora();
    } else {
      notificarComp('Error al guardar configuración: ' + (res.mensaje || 'Error desconocido'), true);
    }
  } catch (err) {
    notificarComp('Error de red al guardar: ' + (err.message || err), true);
    console.error(err);
  }
}

function restablecerConfigComparadorDefecto() {
  actualizarFormularioConfig(CONFIG_COMPARADOR_DEFAULT);
  notificarComp('Valores por defecto aplicados.');
}

// 5. EJECUTAR ANÁLISIS BAJO DEMANDA
async function ejecutarComparadorAhora() {
  const btn = document.getElementById('btn-ejecutar-comp');
  const txt = document.getElementById('texto-btn-ejecutar-comp');
  const icono = document.getElementById('icono-btn-ejecutar-comp');

  if (btn) btn.disabled = true;
  if (txt) txt.textContent = 'Analizando...';
  if (icono) icono.classList.add('animate-spin');

  try {
    const data = await apiRunComparatorNow();
    if (data.status === 'ok') {
      const res = data.resultado || {};
      paresComparadorCache = res.pares || [];
      actualizarEstadisticas(res);
      filtrarParesComparador();
      sincronizarDetalleActivo();
      actualizarEstadoScheduler(data.estado || {});
      notificarComp(`Análisis completado: ${paresComparadorCache.length} pares sospechosos encontrados.`);
    } else {
      notificarComp('Error en análisis: ' + (data.mensaje || 'Error desconocido'), true);
    }
  } catch (err) {
    notificarComp('Error al ejecutar comparador: ' + (err.message || err), true);
    console.error('Error detallado en ejecutarComparadorAhora:', err);
  } finally {
    if (btn) btn.disabled = false;
    if (txt) txt.textContent = 'Ejecutar Ahora';
    if (icono) icono.classList.remove('animate-spin');
  }
}

// 6. CARGAR RESULTADOS Y RENDERIZAR
async function cargarResultadosComparador() {
  try {
    const data = await apiGetComparatorResults();
    if (data.status === 'ok') {
      const res = data.resultado || {};
      paresComparadorCache = res.pares || [];
      actualizarEstadisticas(res);
      filtrarParesComparador();
      sincronizarDetalleActivo();
      if (data.estado) actualizarEstadoScheduler(data.estado);
    }
  } catch (err) {
    console.warn('Error cargando resultados del comparador:', err);
  }
}

function actualizarEstadisticas(res) {
  const elAlumnos = document.getElementById('stat-comp-alumnos');
  const elPares = document.getElementById('stat-comp-pares');
  const elCriticos = document.getElementById('stat-comp-criticos');
  const elRed = document.getElementById('stat-comp-con-red');
  const elBadgeHeader = document.getElementById('badge-pares-header');

  const pares = res.pares || [];
  const criticos = pares.filter(p => p.nivel_riesgo === 'CRÍTICO').length;
  const conRed = pares.filter(p => p.fugas_con_red).length;

  if (elAlumnos) elAlumnos.textContent = String(res.total_alumnos ?? 0);
  if (elPares) elPares.textContent = String(pares.length);
  if (elCriticos) elCriticos.textContent = String(criticos);
  if (elRed) elRed.textContent = String(conRed);

  if (elBadgeHeader) {
    if (pares.length > 0) {
      elBadgeHeader.textContent = String(pares.length);
      elBadgeHeader.classList.remove('hidden');
    } else {
      elBadgeHeader.classList.add('hidden');
    }
  }
}

// 7. FILTRADO Y RENDERIZADO DE TARJETAS DE PARES SOSPECHOSOS
function filtrarParesComparador() {
  const textoFiltro = (document.getElementById('filtro-texto-comp')?.value || '').toLowerCase().trim();
  const nivelFiltro = document.getElementById('filtro-riesgo-comp')?.value || 'TODOS';

  paresFiltradosCache = paresComparadorCache.filter(p => {
    if (nivelFiltro !== 'TODOS') {
      if (nivelFiltro === 'CRÍTICO' && p.nivel_riesgo !== 'CRÍTICO') return false;
      if (nivelFiltro === 'ALTO' && p.nivel_riesgo === 'MEDIO') return false;
    }
    if (textoFiltro) {
      const matchA = (p.alumno_a || '').toLowerCase().includes(textoFiltro);
      const matchB = (p.alumno_b || '').toLowerCase().includes(textoFiltro);
      if (!matchA && !matchB) return false;
    }
    return true;
  });

  const elContador = document.getElementById('comp-pares-mostrados');
  if (elContador) elContador.textContent = `(${paresFiltradosCache.length} pares coincidentes)`;

  renderizarParesComparador(paresFiltradosCache);
}

// Renderizado de tarjetas compactas en cuadrícula de 2 o 3 columnas
function renderizarParesComparador(pares) {
  const cont = document.getElementById('contenedor-pares-comparador');
  if (!cont) return;

  if (!pares || pares.length === 0) {
    cont.innerHTML = `
      <div class="col-span-full text-center py-16 text-slate-500 border border-dashed border-[#1f2937] rounded-lg bg-[#0b0f19]/40 p-6 font-mono text-xs">
        <svg class="w-10 h-10 mx-auto text-slate-600 mb-2" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
        <p class="text-slate-300 font-semibold text-sm">No se detectaron relaciones por encima del umbral</p>
        <p class="text-slate-500 mt-1">Los registros analizados no presentan similitud de código ni sincronía sospechosa con los parámetros actuales.</p>
        <button onclick="ejecutarComparadorAhora()" class="mt-4 px-3.5 py-1.5 rounded bg-[#1f2937] hover:bg-[#374151] text-purple-300 border border-purple-800/60 font-mono text-xs transition-colors">
          Re-analizar Logs Ahora
        </button>
      </div>
    `;
    return;
  }

  let html = '';
  pares.forEach((p, idx) => {
    const nivel = (p.nivel_riesgo || 'MEDIO').toUpperCase();
    let badgeRiesgoColor = 'bg-amber-950/60 text-amber-300 border-amber-800/60';
    let barraColor = 'bg-amber-500';
    let bordeCard = 'border-[#1f2937]';

    if (nivel === 'CRÍTICO') {
      badgeRiesgoColor = 'bg-red-950/80 text-red-200 border-red-700 shadow-sm';
      barraColor = 'bg-red-500';
      bordeCard = 'border-red-900/60';
    } else if (nivel === 'ALTO') {
      badgeRiesgoColor = 'bg-amber-950/70 text-amber-200 border-amber-700';
      barraColor = 'bg-amber-500';
      bordeCard = 'border-amber-900/50';
    }

    const senalesNum = p.señales_independientes || 1;
    const scorePct = p.score_porcentaje || Math.round((p.score_riesgo || 0) * 100);

    html += `
    <div onclick="verDetallePar(${idx})" class="group cursor-pointer border ${bordeCard} bg-[#0e1424] hover:bg-[#131b30] hover:border-purple-500/70 p-3 rounded-lg transition-all font-mono shadow-sm flex flex-col justify-between gap-2.5 hover:shadow-md hover:shadow-purple-950/20 select-none">
      
      <!-- CABECERA DE LA CARD: SEVERIDAD Y CANTIDAD DE SEÑALES -->
      <div class="flex items-center justify-between gap-2">
        <span class="px-2 py-0.5 rounded text-[10px] uppercase font-bold border ${badgeRiesgoColor}">${nivel}</span>
        <span class="text-[10px] text-slate-400 bg-[#090d16] px-2 py-0.5 rounded border border-[#1f2937]">
          ${senalesNum} ${senalesNum === 1 ? 'señal' : 'señales'}
        </span>
      </div>

      <!-- CUERPO DE LA CARD: ALUMNOS DEL PAR -->
      <div class="flex flex-col gap-1 my-0.5">
        <div class="flex items-center gap-1.5 text-xs font-semibold text-white truncate" title="${escaparHtml(p.alumno_a)}">
          <span class="w-1.5 h-1.5 rounded-full bg-slate-500 group-hover:bg-purple-400 transition-colors shrink-0"></span>
          <span class="truncate">${escaparHtml(p.alumno_a)}</span>
        </div>
        <div class="flex items-center gap-1.5 text-xs font-semibold text-white truncate" title="${escaparHtml(p.alumno_b)}">
          <span class="w-1.5 h-1.5 rounded-full bg-slate-500 group-hover:bg-purple-400 transition-colors shrink-0"></span>
          <span class="truncate">${escaparHtml(p.alumno_b)}</span>
        </div>
      </div>

      <!-- PIE DE LA CARD: SCORE DE RIESGO Y ACCESO AL DETALLE -->
      <div class="pt-2 border-t border-[#1f2937]/70 flex items-center justify-between gap-2">
        <div class="flex items-center gap-2 flex-1 min-w-0">
          <span class="text-[11px] text-slate-400 font-medium">Riesgo:</span>
          <div class="flex-1 bg-[#1f2937] rounded-full h-1.5 overflow-hidden">
            <div class="${barraColor} h-1.5 rounded-full" style="width: ${scorePct}%;"></div>
          </div>
          <span class="text-xs font-bold text-white">${scorePct}%</span>
        </div>
        <span class="text-[11px] text-purple-400 group-hover:text-purple-300 font-medium flex items-center shrink-0 transition-colors">
          Ver &rarr;
        </span>
      </div>

    </div>
    `;
  });

  cont.innerHTML = html;
}

// NAVEGACIÓN HACIA LA VISTA DE DETALLE COMPLETO
function verDetallePar(idx) {
  const p = paresFiltradosCache[idx];
  if (!p) return;

  parDetalleActivo = { alumno_a: p.alumno_a, alumno_b: p.alumno_b };
  renderizarDetallePar(p);

  const vistaLista = document.getElementById('comp-vista-lista');
  const vistaDetalle = document.getElementById('comp-vista-detalle');

  if (vistaLista) vistaLista.classList.add('hidden');
  if (vistaDetalle) vistaDetalle.classList.remove('hidden');

  window.scrollTo({ top: 0, behavior: 'smooth' });
}

// NAVEGACIÓN DE REGRESO AL LISTADO DE PARES
function volverAListaPares() {
  parDetalleActivo = null;

  const vistaLista = document.getElementById('comp-vista-lista');
  const vistaDetalle = document.getElementById('comp-vista-detalle');

  if (vistaDetalle) vistaDetalle.classList.add('hidden');
  if (vistaLista) vistaLista.classList.remove('hidden');
}

// SINCRONIZAR VISTA DETALLE SI SE ENCUENTRA ABIERTA DURANTE UN AUTO-REFRESH
function sincronizarDetalleActivo() {
  if (!parDetalleActivo) return;
  const vistaDetalle = document.getElementById('comp-vista-detalle');
  if (vistaDetalle && !vistaDetalle.classList.contains('hidden')) {
    const encontrado = paresComparadorCache.find(p => 
      (p.alumno_a === parDetalleActivo.alumno_a && p.alumno_b === parDetalleActivo.alumno_b) ||
      (p.alumno_a === parDetalleActivo.alumno_b && p.alumno_b === parDetalleActivo.alumno_a)
    );
    if (encontrado) {
      renderizarDetallePar(encontrado);
    }
  }
}

// RENDERIZADO DEL DETALLE COMPLETO DEL PAR SELECCIONADO
function renderizarDetallePar(p) {
  const elAcciones = document.getElementById('comp-detalle-acciones-auditar');
  const elContenido = document.getElementById('comp-detalle-contenido');
  if (!elContenido) return;

  const idA = p.alumno_a_id || (p.alumno_a ? p.alumno_a.replace(/\.log$/, '') : '');
  const idB = p.alumno_b_id || (p.alumno_b ? p.alumno_b.replace(/\.log$/, '') : '');

  // Acciones en la barra superior
  if (elAcciones) {
    elAcciones.innerHTML = `
      <a href="/auditoria/${encodeURIComponent(idA)}" target="_blank" class="px-2.5 py-1.5 rounded bg-[#1f2937] hover:bg-[#374151] text-sky-300 border border-sky-800/60 text-xs transition-colors flex items-center gap-1.5 font-medium">
        <svg class="w-3.5 h-3.5 text-sky-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
        </svg>
        <span>Auditar ${escaparHtml(idA)}</span>
      </a>
      <a href="/auditoria/${encodeURIComponent(idB)}" target="_blank" class="px-2.5 py-1.5 rounded bg-[#1f2937] hover:bg-[#374151] text-sky-300 border border-sky-800/60 text-xs transition-colors flex items-center gap-1.5 font-medium">
        <svg class="w-3.5 h-3.5 text-sky-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
        </svg>
        <span>Auditar ${escaparHtml(idB)}</span>
      </a>
    `;
  }

  const nivel = (p.nivel_riesgo || 'MEDIO').toUpperCase();
  let badgeRiesgoColor = 'bg-amber-950/60 text-amber-300 border-amber-800/60';
  let barraColor = 'bg-amber-500';
  let bannerBorde = 'border-amber-900/60';

  if (nivel === 'CRÍTICO') {
    badgeRiesgoColor = 'bg-red-950/80 text-red-200 border-red-700 shadow-sm';
    barraColor = 'bg-red-500';
    bannerBorde = 'border-red-900/70';
  } else if (nivel === 'ALTO') {
    badgeRiesgoColor = 'bg-amber-950/70 text-amber-200 border-amber-700';
    barraColor = 'bg-amber-500';
    bannerBorde = 'border-amber-900/60';
  }

  const senalesNum = p.señales_independientes || 1;
  const scorePct = p.score_porcentaje || Math.round((p.score_riesgo || 0) * 100);

  // Muestra de portapapeles si existe
  let bloquePortapapeles = '<span class="text-slate-500 italic text-xs">Sin coincidencias de portapapeles detectadas.</span>';
  if (p.muestra_portapapeles) {
    bloquePortapapeles = `
      <div class="mt-2 p-3 bg-[#090d16] border border-[#1f2937] rounded-lg text-xs font-mono text-slate-300 overflow-x-auto max-h-48">
        <span class="text-slate-500 text-[10px] block mb-1 uppercase font-semibold">Fragmento coincidente en portapapeles:</span>
        <pre class="whitespace-pre-wrap leading-relaxed">${escaparHtml(p.muestra_portapapeles)}</pre>
      </div>
    `;
  }

  // Fugas de contexto sincronizadas
  let bloqueFugas = '<span class="text-slate-500 italic text-xs">Ninguna fuga de contexto simultánea registrada.</span>';
  if (p.fugas_sincronizadas && p.fugas_sincronizadas.length > 0) {
    const itemsFugas = p.fugas_sincronizadas.map(f => {
      const esRed = f.includes('con red en ambos');
      const badgeFuerza = esRed 
        ? '<span class="px-2 py-0.5 rounded text-[10px] bg-red-950/80 text-red-300 border border-red-800 font-bold shrink-0">RED CONFIRMADA</span>'
        : '<span class="px-2 py-0.5 rounded text-[10px] bg-slate-800 text-slate-400 border border-slate-700 shrink-0">SIN RED</span>';
      return `<li class="flex items-center justify-between gap-3 py-1.5 border-b border-[#1f2937]/60 text-slate-300 text-xs">
        <span class="truncate leading-relaxed">${escaparHtml(f)}</span>
        ${badgeFuerza}
      </li>`;
    }).join('');
    bloqueFugas = `<div class="max-h-64 overflow-y-auto pr-1"><ul class="flex flex-col">${itemsFugas}</ul></div>`;
  }

  // Ejecuciones sincronizadas
  let bloqueExec = '<span class="text-slate-500 italic text-xs">Ninguna ejecución simultánea registrada.</span>';
  if (p.sincronia_ejecucion && p.sincronia_ejecucion.length > 0) {
    const itemsExec = p.sincronia_ejecucion.map(e => {
      return `<li class="py-1.5 border-b border-[#1f2937]/60 text-slate-300 font-mono text-xs leading-relaxed">&bull; ${escaparHtml(e)}</li>`;
    }).join('');
    bloqueExec = `<div class="max-h-64 overflow-y-auto pr-1"><ul class="flex flex-col">${itemsExec}</ul></div>`;
  }

  elContenido.innerHTML = `
    <!-- BANNER DE RESUMEN DEL PAR SELECCIONADO -->
    <div class="border ${bannerBorde} bg-[#0e1424] p-4 rounded-lg flex flex-col gap-3 shadow-md">
      
      <div class="flex flex-col lg:flex-row lg:items-center justify-between gap-3 border-b border-[#1f2937] pb-3">
        <div>
          <div class="flex items-center gap-2 flex-wrap mb-1">
            <span class="px-2.5 py-0.5 rounded text-xs uppercase font-bold border ${badgeRiesgoColor}">${nivel}</span>
            <span class="text-xs text-slate-400 bg-slate-800/80 px-2.5 py-0.5 rounded border border-slate-700">
              ${senalesNum} ${senalesNum === 1 ? 'señal independiente' : 'señales independientes'}
            </span>
          </div>
          <h2 class="text-lg font-bold text-white flex items-center gap-2 flex-wrap mt-1">
            <span>${escaparHtml(p.alumno_a)}</span>
            <span class="text-purple-400">&harr;</span>
            <span>${escaparHtml(p.alumno_b)}</span>
          </h2>
        </div>

        <div class="flex items-center gap-3 bg-[#090d16] px-4 py-2 rounded-lg border border-[#1f2937] self-start lg:self-auto">
          <div class="flex flex-col">
            <span class="text-[10px] uppercase tracking-wider text-slate-400">Score de Riesgo Global</span>
            <span class="text-xl font-bold text-white">${scorePct}%</span>
          </div>
          <div class="w-24 sm:w-32 bg-[#1f2937] rounded-full h-2.5 overflow-hidden">
            <div class="${barraColor} h-2.5 rounded-full" style="width: ${scorePct}%;"></div>
          </div>
        </div>
      </div>

      <div class="text-[11px] text-slate-400 flex flex-col sm:flex-row sm:items-center justify-between gap-2">
        <span>Fórmula ponderada: ${Math.round((configComparadorCache?.peso_codigo ?? 0.45) * 100)}% Código + ${Math.round((configComparadorCache?.peso_portapapeles ?? 0.35) * 100)}% Portapapeles + ${Math.round((configComparadorCache?.peso_fugas ?? 0.15) * 100)}% Fugas + ${Math.round((configComparadorCache?.peso_ejecucion ?? 0.05) * 100)}% Ejecución</span>
        <span class="text-slate-300 font-semibold">Score numérico exacto: ${p.score_riesgo}</span>
      </div>

    </div>

    <!-- MATRIZ DETALLADA DE LAS 4 SEÑALES DE CORRELACIÓN -->
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-3.5">
      
      <!-- SEÑAL 1: CÓDIGO FUENTE (MINHASH / JACCARD) -->
      <div class="bg-[#111827] p-3.5 rounded-lg border border-[#1f2937] flex flex-col justify-between gap-3 shadow-sm">
        <div>
          <div class="flex items-center justify-between gap-2">
            <h4 class="text-xs font-semibold text-slate-200 uppercase tracking-wide">1. Similitud de Código (Jaccard)</h4>
            <span class="text-sm font-bold text-sky-400">${p.similitud_codigo_porcentaje}%</span>
          </div>
          <div class="w-full bg-slate-800 h-2 rounded-full overflow-hidden mt-2">
            <div class="bg-sky-500 h-2 rounded-full" style="width: ${p.similitud_codigo_porcentaje}%;"></div>
          </div>
          <p class="text-[11px] text-slate-400 mt-2 leading-relaxed">
            Análisis de n-gramas de palabras continuas procesado mediante MinHash y LSH. Estima la intersección Jaccard entre secuencias de código de ambos alumnos sin costo de comparación cuadrático.
          </p>
        </div>
        <div class="p-2 rounded bg-[#0b0f19] border border-[#1f2937] text-[11px] flex items-center justify-between">
          <span class="text-slate-400">Estado de coincidencia:</span>
          <span class="font-bold ${p.similitud_codigo_porcentaje >= 50 ? 'text-red-400' : (p.similitud_codigo_porcentaje >= 35 ? 'text-amber-400' : 'text-slate-400')}">
            ${p.similitud_codigo_porcentaje >= 50 ? 'ALTA COINCIDENCIA' : (p.similitud_codigo_porcentaje >= 35 ? 'COINCIDENCIA MODERADA' : 'BAJO')}
          </span>
        </div>
      </div>

      <!-- SEÑAL 2: PORTAPAPELES COMPARTIDO (PAPERCLIP) -->
      <div class="bg-[#111827] p-3.5 rounded-lg border border-[#1f2937] flex flex-col justify-between gap-3 shadow-sm">
        <div>
          <div class="flex items-center justify-between gap-2">
            <h4 class="text-xs font-semibold text-slate-200 uppercase tracking-wide">2. Portapapeles Compartido</h4>
            <span class="text-sm font-bold text-purple-400">${p.similitud_portapapeles_porcentaje}%</span>
          </div>
          <div class="w-full bg-slate-800 h-2 rounded-full overflow-hidden mt-2">
            <div class="bg-purple-500 h-2 rounded-full" style="width: ${p.similitud_portapapeles_porcentaje}%;"></div>
          </div>
          <p class="text-[11px] text-slate-400 mt-2 leading-relaxed">
            Similitud léxica exacta calculada mediante SequenceMatcher sobre textos copiados o pegados en el portapapeles.
          </p>
        </div>
        <div>
          ${bloquePortapapeles}
        </div>
      </div>

      <!-- SEÑAL 3: FUGAS DE CONTEXTO SIMULTÁNEAS -->
      <div class="bg-[#111827] p-3.5 rounded-lg border border-[#1f2937] flex flex-col gap-3 shadow-sm">
        <div>
          <div class="flex items-center justify-between gap-2">
            <h4 class="text-xs font-semibold text-slate-200 uppercase tracking-wide">3. Fugas de Contexto Sincronizadas</h4>
            <span class="text-xs font-bold text-amber-400 px-2 py-0.5 rounded bg-amber-950/60 border border-amber-800/60">
              ${(p.fugas_sincronizadas || []).length} eventos
            </span>
          </div>
          <p class="text-[11px] text-slate-400 mt-1 leading-relaxed">
            Ambos alumnos cambiaron a aplicaciones no autorizadas dentro de la ventana de sincronía configurada.
          </p>
        </div>
        <div>
          ${bloqueFugas}
        </div>
      </div>

      <!-- SEÑAL 4: EJECUCIÓN SINCRONIZADA DE SCRIPTS -->
      <div class="bg-[#111827] p-3.5 rounded-lg border border-[#1f2937] flex flex-col gap-3 shadow-sm">
        <div>
          <div class="flex items-center justify-between gap-2">
            <h4 class="text-xs font-semibold text-slate-200 uppercase tracking-wide">4. Ejecución Sincronizada (Auditoría)</h4>
            <span class="text-xs font-bold text-emerald-400 px-2 py-0.5 rounded bg-emerald-950/60 border border-emerald-800/60">
              ${(p.sincronia_ejecucion || []).length} coincidencias
            </span>
          </div>
          <p class="text-[11px] text-slate-400 mt-1 leading-relaxed">
            Ejecuciones simultáneas de comandos o scripts registradas en sus terminales o entornos.
          </p>
        </div>
        <div>
          ${bloqueExec}
        </div>
      </div>

    </div>
  `;
}

function escaparHtml(texto) {
  if (!texto) return '';
  return String(texto)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

// 8. CICLO DE ACTUALIZACIÓN EN VIVO DE CUENTA REGRESIVA
function tickComparador() {
  if (estadoExamenGlobal === 'FINALIZADO') {
    return; // No realizar cuenta regresiva si el examen está finalizado
  }
  if (compSegundosRestantesLocales > 0) {
    compSegundosRestantesLocales -= 1;
    const elSegundos = document.getElementById('comp-segundos-restantes');
    if (elSegundos) elSegundos.textContent = String(compSegundosRestantesLocales);
  }
}

// Inicialización
document.addEventListener('DOMContentLoaded', () => {
  // Inicializar formulario con defaults inmediatamente para que nunca esté vacío
  actualizarFormularioConfig(CONFIG_COMPARADOR_DEFAULT);

  // Cargar configuración persistida y estado inicial desde el servidor
  cargarConfigComparador();

  // Iniciar tick local de cuenta regresiva
  setInterval(tickComparador, 1000);

  // Polling suave de resultados cuando la pestaña de comparador esté visible
  setInterval(() => {
    if (pestanaActiva === 'comparador') {
      cargarConfigComparador({ rellenarFormulario: false });
      // Si el examen ya está finalizado, no hay nuevos análisis periódicos automáticos corriendo
      if (estadoExamenGlobal !== 'FINALIZADO') {
        cargarResultadosComparador();
      }
    }
  }, 5000);

  // Escuchar tecla Escape para cerrar el modal de configuración
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      const panel = document.getElementById('panel-config-comparador');
      if (panel && !panel.classList.contains('hidden')) {
        toggleConfigComparador(false);
      }
    }
  });
});