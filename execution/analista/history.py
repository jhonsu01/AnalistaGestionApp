"""[DET] Historial persistente de consultas. SQLite, sin dependencias externas.

Cada pregunta y su respuesta quedan guardadas con las fuentes que la
sustentan, para poder releerlas, buscarlas y comprobar de donde salio cada
dato meses despues.
"""
from __future__ import annotations

import contextlib
import json
import sqlite3
from datetime import datetime
from typing import Optional

from . import config

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS consultas (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    pregunta   TEXT NOT NULL,
    respuesta  TEXT NOT NULL,
    fuentes    TEXT NOT NULL DEFAULT '[]',
    sector     TEXT NOT NULL DEFAULT '',
    entidad    TEXT NOT NULL DEFAULT '',
    fragmentos INTEGER NOT NULL DEFAULT 0,
    favorita   INTEGER NOT NULL DEFAULT 0,
    fijada     INTEGER NOT NULL DEFAULT 0,
    creada_en  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_consulta_fecha ON consultas(creada_en DESC);
CREATE VIRTUAL TABLE IF NOT EXISTS consultas_fts
    USING fts5(pregunta, respuesta, content='consultas', content_rowid='id');
CREATE TRIGGER IF NOT EXISTS consultas_ai AFTER INSERT ON consultas BEGIN
    INSERT INTO consultas_fts(rowid, pregunta, respuesta)
    VALUES (new.id, new.pregunta, new.respuesta);
END;
CREATE TRIGGER IF NOT EXISTS consultas_ad AFTER DELETE ON consultas BEGIN
    INSERT INTO consultas_fts(consultas_fts, rowid, pregunta, respuesta)
    VALUES ('delete', old.id, old.pregunta, old.respuesta);
END;
"""


def _conectar() -> sqlite3.Connection:
    config.asegurar_dirs()
    con = sqlite3.connect(config.DB_FILE, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


@contextlib.contextmanager
def _bd():
    """Abre, confirma y CIERRA la conexion.

    `with sqlite3.connect(...)` gestiona la transaccion pero NO cierra: usarlo
    a secas filtra una conexion por peticion.
    """
    con = _conectar()
    try:
        with con:
            yield con
    finally:
        con.close()


def iniciar() -> None:
    with _bd() as con:
        con.executescript(_ESQUEMA)


def guardar(
    pregunta: str,
    respuesta: str,
    fuentes: list[dict],
    sector: str = "",
    entidad: str = "",
) -> int:
    with _bd() as con:
        cur = con.execute(
            "INSERT INTO consultas "
            "(pregunta, respuesta, fuentes, sector, entidad, fragmentos, creada_en) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                pregunta,
                respuesta,
                json.dumps(fuentes, ensure_ascii=False),
                sector or "",
                entidad or "",
                len(fuentes),
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        return int(cur.lastrowid)


_CAMPOS = (
    "id, pregunta, sector, entidad, fragmentos, favorita, fijada, creada_en, "
    "substr(respuesta, 1, 200) AS extracto, length(respuesta) AS largo"
)


def listar(
    limite: int = 40,
    desplazamiento: int = 0,
    solo_favoritas: bool = False,
    solo_fijadas: bool = False,
) -> list[dict]:
    sql = f"SELECT {_CAMPOS} FROM consultas "
    filtros = []
    if solo_favoritas:
        filtros.append("favorita = 1")
    if solo_fijadas:
        filtros.append("fijada = 1")
    if filtros:
        sql += "WHERE " + " AND ".join(filtros) + " "
    sql += "ORDER BY creada_en DESC, id DESC LIMIT ? OFFSET ?"
    with _bd() as con:
        return [dict(r) for r in con.execute(sql, (limite, desplazamiento))]


def obtener(consulta_id: int) -> Optional[dict]:
    with _bd() as con:
        fila = con.execute(
            "SELECT * FROM consultas WHERE id = ?", (consulta_id,)
        ).fetchone()
        if not fila:
            return None
        datos = dict(fila)
        try:
            datos["fuentes"] = json.loads(datos.get("fuentes") or "[]")
        except json.JSONDecodeError:
            datos["fuentes"] = []
        return datos


def buscar(texto: str, limite: int = 40) -> list[dict]:
    """Busqueda full-text sobre el historial de consultas."""
    with _bd() as con:
        try:
            filas = con.execute(
                "SELECT c.id, c.pregunta, c.creada_en, c.favorita, c.fijada, "
                "c.sector, c.entidad, "
                "substr(c.respuesta, 1, 200) AS extracto "
                "FROM consultas_fts f JOIN consultas c ON c.id = f.rowid "
                "WHERE consultas_fts MATCH ? ORDER BY rank LIMIT ?",
                (texto, limite),
            ).fetchall()
        except sqlite3.OperationalError:
            # FTS5 no disponible o consulta con sintaxis que no acepta.
            patron = f"%{texto}%"
            filas = con.execute(
                "SELECT id, pregunta, creada_en, favorita, fijada, sector, entidad, "
                "substr(respuesta, 1, 200) AS extracto FROM consultas "
                "WHERE pregunta LIKE ? OR respuesta LIKE ? "
                "ORDER BY creada_en DESC LIMIT ?",
                (patron, patron, limite),
            ).fetchall()
        return [dict(r) for r in filas]


def alternar_favorita(consulta_id: int) -> bool:
    with _bd() as con:
        con.execute(
            "UPDATE consultas SET favorita = 1 - favorita WHERE id = ?", (consulta_id,)
        )
        fila = con.execute(
            "SELECT favorita FROM consultas WHERE id = ?", (consulta_id,)
        ).fetchone()
        return bool(fila and fila["favorita"])


def alternar_fijada(consulta_id: int) -> bool:
    with _bd() as con:
        con.execute(
            "UPDATE consultas SET fijada = 1 - fijada WHERE id = ?", (consulta_id,)
        )
        fila = con.execute(
            "SELECT fijada FROM consultas WHERE id = ?", (consulta_id,)
        ).fetchone()
        return bool(fila and fila["fijada"])


def borrar(consulta_id: int) -> None:
    with _bd() as con:
        con.execute("DELETE FROM consultas WHERE id = ?", (consulta_id,))


def estadisticas() -> dict:
    with _bd() as con:
        fila = con.execute(
            "SELECT COUNT(*) AS total, "
            "COALESCE(SUM(favorita), 0) AS favoritas, "
            "COALESCE(SUM(fijada), 0) AS fijadas FROM consultas"
        ).fetchone()
        return dict(fila)


def exportar_markdown(consulta_id: int) -> str:
    """Convierte una consulta guardada en un Markdown citable."""
    datos = obtener(consulta_id)
    if not datos:
        return ""
    lineas = [
        f"# {datos['pregunta']}",
        "",
        f"*Consultado el {datos['creada_en']}*",
        "",
        datos["respuesta"],
        "",
        "## Fuentes",
        "",
    ]
    for i, f in enumerate(datos.get("fuentes", []), 1):
        lineas.append(
            f"{i}. **{f.get('entidad', '?')}** — `{f.get('archivo', '?')}`"
            + (f" (seccion: {f['seccion']})" if f.get("seccion") else "")
        )
    lineas += [
        "",
        "---",
        "",
        "Fuente primaria: informes de empalme 2022-2026, "
        "Departamento Nacional de Planeacion (https://datalogo.dnp.gov.co/informe-empalme)",
    ]
    return "\n".join(lineas)
