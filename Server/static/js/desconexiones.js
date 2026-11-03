/**
 * Últimas Desconexiones
 * Reutiliza el mismo arreglo de alertas que ya trae /api/status (state.historial_alertas).
 * No basta con filtrar por regla_id === "SISTEMA": ese valor solo lo tienen las
 * alertas insertadas EN VIVO por Server/state.py (watchdog de silencio) y
 * Server/routers/agent.py (detección de reconexión) — pero el botón "Re-analizar"
 * (POST /api/reglas/reanalizar) hace historial_alertas.clear() y reconstruye todo
 * desde cero releyendo los logs guardados con el correlator, que nunca vuelve a
 * generar esas entradas "SISTEMA" (son producto de comparar timestamps en vivo,
 * no de releer contenido). Si el profesor usa una regla de correlación propia que
 * combine el paso "sistema" con el texto "AGENTE_INTERRUMPIDO" (que sí queda escrito
 * en el .log del alumno, ver agent.py), esa desconexión sigue existiendo, pero bajo
 * el regla_id de esa regla (ej. "R-08"), no "SISTEMA" — y el filtro anterior la
 * perdía. Por eso acá se detecta por CONTENIDO, no solo por regla_id.
 */

// ¿Esta alerta es, de algún modo, un evento de desconexión/reconexión?
function esEventoDesconexion(alerta) {
  if (alerta.regla_id === 'SISTEMA') return true;
  // AGENTE_INTERRUMPIDO es el texto exacto que agent.py escribe en el .log del
  // alumno al detectar un gap de reconexión (ver _sync); si una regla de
  // correlación propia lo usa como paso, esta alerta también es de desconexión.
  return (alerta.mensaje || '').includes('AGENTE_INTERRUMPIDO');
}

// ¿Significa que el agente YA volvió (reconectado), o que sigue sin responder?
function esReconexion(alerta) {
  if (alerta.regla_id === 'SISTEMA') {
    // Único caso ambiguo: las dos alertas directas que emite el propio servidor.
    // "Interrupción del Agente" se dispara cuando llega un /sync después del
    // corte -> ya volvió. "Silencio Prolongado (en vivo)" la dispara el watchdog
    // mientras el agente SIGUE sin responder -> todavía desconectado.
    return (alerta.regla_nombre || '') === 'Interrupción del Agente';
  }
  // Si vino de una regla de correlación, solo pudo haberse armado a partir de la
  // línea AGENTE_INTERRUMPIDO ya escrita en el log — y esa línea únicamente se
  // escribe cuando el agente volvió a sincronizar. O sea que sí, ya está de vuelta.
  return true;
}

function renderizarDesconexionesEnVivo(alertas) {
  const cont = document.getElementById('contenedor-desconexiones');
  const badge = document.getElementById('contador-desconexiones-badge');
  const badgeHeader = document.getElementById('badge-desconexiones-header');
  if (!cont) return;

  const eventosDesconexion = (alertas || []).filter(esEventoDesconexion);
  if (badge) badge.textContent = String(eventosDesconexion.length);

  // Badge junto a la pestaña del header, igual que el de pares del comparador:
  // visible solo cuando hay algo que mirar, para no ensuciar el header en el
  // caso normal (examen sin cortes de red).
  if (badgeHeader) {
    if (eventosDesconexion.length > 0) {
      badgeHeader.textContent = String(eventosDesconexion.length);
      badgeHeader.classList.remove('hidden');
    } else {
      badgeHeader.classList.add('hidden');
    }
  }

  if (eventosDesconexion.length === 0) {
    cont.className = 'grid grid-cols-1 gap-2.5 font-mono text-xs sm:text-sm';
    cont.innerHTML = `
      <div id="sin-desconexiones-aviso" class="col-span-full py-12 text-center text-slate-400 text-xs sm:text-sm border border-dashed border-[#1f2937] rounded-lg bg-[#0b0f19]/30">
        No se han detectado desconexiones en este examen.
      </div>
    `;
    return;
  }

  // Ya es una pestaña propia (página completa), no un módulo apretado entre
  // otros — alcanza con el historial completo de alertas (tope de 100 en
  // state.historial_alertas) en vez de limitarlo a un puñado como antes.
  const cant = eventosDesconexion.length;

  // Mismo ajuste de columnas que alerts.js: con pocas tarjetas no se estiran
  // ocupando toda la fila vacías.
  let gridColsClass = 'grid-cols-1 sm:grid-cols-2 xl:grid-cols-3';
  if (cant === 1) {
    gridColsClass = 'grid-cols-1';
  } else if (cant === 2) {
    gridColsClass = 'grid-cols-1 sm:grid-cols-2';
  }
  cont.className = `grid ${gridColsClass} gap-2.5 font-mono text-xs sm:text-sm w-full`;

  let html = '';
  eventosDesconexion.forEach(ev => {
    const reconectado = esReconexion(ev);
    const badgeColor = reconectado
      ? 'border-emerald-700 bg-emerald-950/50 text-emerald-300'
      : 'border-amber-700 bg-amber-950/50 text-amber-300 animate-pulse';
    const etiquetaEstado = reconectado ? 'RECONECTADO' : 'DESCONECTADO';

    const ts = (ev.timestamp || '').replace('T', ' ').slice(11, 19) || 'reciente';
    const clientId = escaparHtml(ev.client_id || 'Estudiante');
    const reglaNombre = escaparHtml(ev.regla_nombre || 'Interrupción del Agente');
    const mensajeEscapado = escaparHtml(ev.mensaje || '');

    html += `
    <div class="flex flex-col justify-between p-2.5 rounded-lg bg-[#0b0f19] border border-[#1f2937] hover:border-slate-600 gap-2 transition-colors min-w-0">
      <div class="flex items-start justify-between gap-1.5">
        <div class="flex items-center gap-1.5 min-w-0">
          <span class="px-2 py-0.5 rounded text-[11px] uppercase font-mono font-bold border ${badgeColor} shrink-0">${etiquetaEstado}</span>
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