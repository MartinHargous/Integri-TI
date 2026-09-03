/**
 * Integri-TI - API Client
 * Centraliza las llamadas HTTP con el servidor FastAPI
 */
const Api = {
  async getStatus() {
    const res = await fetch('/api/status?t=' + Date.now(), { cache: 'no-store' });
    if (!res.ok) throw new Error('Error en /api/status: ' + res.status);
    return await res.json();
  },

  async cambiarComando(nuevoComando) {
    const res = await fetch(`/profesor/comando/${encodeURIComponent(nuevoComando)}`);
    if (!res.ok) throw new Error('Error cambiando comando');
    return await res.json();
  },

  async getModulos(destino = 'global') {
    const res = await fetch('/api/modulos?destino=' + encodeURIComponent(destino));
    if (!res.ok) throw new Error('Error obteniendo módulos');
    return await res.json();
  },

  async getModulo(nombre, destino = 'global') {
    const res = await fetch(`/api/modulos/${encodeURIComponent(nombre)}?destino=${encodeURIComponent(destino)}`);
    if (!res.ok) throw new Error('Error obteniendo módulo: ' + nombre);
    return await res.json();
  },

  async actualizarModulo(nombre, destino, valores) {
    const res = await fetch(`/api/modulos/${encodeURIComponent(nombre)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ destino, valores })
    });
    if (!res.ok) throw new Error('Error actualizando módulo');
    return await res.json();
  },

  async getReglas() {
    const res = await fetch('/api/reglas?t=' + Date.now());
    if (!res.ok) throw new Error('Error cargando reglas');
    return await res.json();
  },

  async guardarRegla(regla) {
    const res = await fetch('/api/reglas', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(regla)
    });
    if (!res.ok) throw new Error('Error guardando regla');
    return await res.json();
  },

  async eliminarRegla(reglaId) {
    const res = await fetch(`/api/reglas/${encodeURIComponent(reglaId)}`, { method: 'DELETE' });
    if (!res.ok) throw new Error('Error eliminando regla');
    return await res.json();
  },

  async limpiarAlertas() {
    const res = await fetch('/api/alertas/limpiar', { method: 'POST' });
    if (!res.ok) throw new Error('Error limpiando alertas');
    return await res.json();
  },

  async reanalizarHistorial() {
    const res = await fetch('/api/reglas/reanalizar', { method: 'POST' });
    if (!res.ok) throw new Error('Error reanalizando logs');
    return await res.json();
  }
};

