import os
import sqlite3
import json
from datetime import datetime
from typing import List, Dict, Any, Optional

DB_NAME = "integri_ti.db"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, DB_NAME)

def get_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def inicializar_db(ruta_json_defaults: Optional[str] = None, db_path: str = DB_PATH):
    """
    Crea las tablas necesarias si no existen.
    Si la tabla 'reglas' está vacía, la puebla con las reglas por defecto desde reglas.json
    sin modificar el archivo JSON.
    """
    if ruta_json_defaults is None:
        ruta_json_defaults = os.path.join(BASE_DIR, "reglas.json")

    with get_connection(db_path) as conn:
        cursor = conn.cursor()

        # 1. Tabla de reglas de correlación
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reglas (
                id TEXT PRIMARY KEY,
                nombre TEXT NOT NULL,
                severidad TEXT NOT NULL,
                ventana_segundos INTEGER NOT NULL,
                pasos TEXT NOT NULL
            )
        """)

        # 2. Tabla de insights de IA (prompts y respuestas enlazados al cliente)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS insights (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id TEXT NOT NULL,
                prompt TEXT NOT NULL,
                response TEXT NOT NULL,
                model TEXT NOT NULL,
                created_at TEXT NOT NULL,
                raw_response TEXT
            )
        """)

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_insights_client ON insights (client_id)")

        # 3. Cargar o sincronizar reglas por defecto desde reglas.json
        if os.path.exists(ruta_json_defaults):
            try:
                with open(ruta_json_defaults, "r", encoding="utf-8") as f:
                    reglas_default = json.load(f)
                nuevas = 0
                for r in reglas_default:
                    r_id = r.get("id")
                    cursor.execute("SELECT id FROM reglas WHERE id = ?", (r_id,))
                    if not cursor.fetchone():
                        nombre = r.get("nombre", "Regla")
                        severidad = r.get("severidad", "MEDIA")
                        ventana = int(r.get("ventana_segundos", 30))
                        pasos_json = json.dumps(r.get("pasos", []), ensure_ascii=False)
                        cursor.execute("""
                            INSERT INTO reglas (id, nombre, severidad, ventana_segundos, pasos)
                            VALUES (?, ?, ?, ?, ?)
                        """, (r_id, nombre, severidad, ventana, pasos_json))
                        nuevas += 1
                conn.commit()
                if nuevas > 0:
                    print(f"[*] {nuevas} reglas nuevas sincronizadas desde {ruta_json_defaults} en SQLite.")
            except Exception as e:
                print(f"[!] Error sincronizando reglas desde {ruta_json_defaults}: {e}")

# --- MÉTODOS CRUD PARA REGLAS ---

def obtener_todas_las_reglas(db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, nombre, severidad, ventana_segundos, pasos FROM reglas ORDER BY id ASC")
        rows = cursor.fetchall()
        reglas = []
        for r in rows:
            try:
                pasos = json.loads(r["pasos"])
            except Exception:
                pasos = []
            reglas.append({
                "id": r["id"],
                "nombre": r["nombre"],
                "severidad": r["severidad"],
                "ventana_segundos": r["ventana_segundos"],
                "pasos": pasos
            })
        return reglas

def guardar_o_actualizar_regla(regla: Dict[str, Any], db_path: str = DB_PATH) -> Dict[str, Any]:
    r_id = regla.get("id")
    nombre = regla.get("nombre", "Regla")
    severidad = regla.get("severidad", "MEDIA")
    ventana = int(regla.get("ventana_segundos", 30))
    pasos_json = json.dumps(regla.get("pasos", []), ensure_ascii=False)

    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO reglas (id, nombre, severidad, ventana_segundos, pasos)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                nombre=excluded.nombre,
                severidad=excluded.severidad,
                ventana_segundos=excluded.ventana_segundos,
                pasos=excluded.pasos
        """, (r_id, nombre, severidad, ventana, pasos_json))
        conn.commit()

    return regla

def eliminar_regla_db(regla_id: str, db_path: str = DB_PATH) -> bool:
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM reglas WHERE id = ?", (regla_id,))
        conn.commit()
        return cursor.rowcount > 0

# --- MÉTODOS PARA INSIGHTS DE IA ---

def guardar_insight(
    client_id: str,
    prompt: str,
    response: str,
    model: str = "luna",
    raw_response: Optional[str] = None,
    db_path: str = DB_PATH
) -> Dict[str, Any]:
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO insights (client_id, prompt, response, model, created_at, raw_response)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (client_id, prompt, response, model, created_at, raw_response or response))
        conn.commit()
        insight_id = cursor.lastrowid

    return {
        "id": insight_id,
        "client_id": client_id,
        "prompt": prompt,
        "response": response,
        "model": model,
        "created_at": created_at,
        "raw_response": raw_response or response
    }

def obtener_ultimo_insight(client_id: str, db_path: str = DB_PATH) -> Optional[Dict[str, Any]]:
    """Recupera el insight más reciente para el cliente para evitar doble generación."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, client_id, prompt, response, model, created_at, raw_response
            FROM insights
            WHERE client_id = ?
            ORDER BY id DESC
            LIMIT 1
        """, (client_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return dict(row)

def obtener_historial_insights(client_id: str, db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    """Recupera todos los insights registrados para un cliente."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, client_id, prompt, response, model, created_at, raw_response
            FROM insights
            WHERE client_id = ?
            ORDER BY id DESC
        """, (client_id,))
        rows = cursor.fetchall()
        return [dict(r) for r in rows]

