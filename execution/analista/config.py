"""[DET] Configuracion central: rutas, puertos y parametros del modelo.

Los DATOS NO viajan con la aplicacion. El corpus de empalme pesa varios GB y
es material publico que cada quien descarga por su cuenta; la app solo guarda
la RUTA donde el usuario los tiene y los lee de ahi.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

# --------------------------------------------------------------------------
# Datos del usuario (fuera del repositorio)
# --------------------------------------------------------------------------
# Todo lo que la app escribe vive en la carpeta del usuario, no junto al
# ejecutable: en Windows "Program Files" es de solo lectura para el usuario.
DATOS = Path(
    os.environ.get("ANALISTA_DATOS")
    or (Path(os.environ.get("LOCALAPPDATA", Path.home())) / "AnalistaGestion")
)
DB_FILE = DATOS / "consultas.db"
AJUSTES_FILE = DATOS / "ajustes.json"

# Carpeta del corpus: la elige el usuario en la propia app.
# Debe contener embeddings.npy, metadatos.jsonl y (opcional) indice.tv
CORPUS_DEFECTO = ""

# --------------------------------------------------------------------------
# Servidor
# --------------------------------------------------------------------------
PUERTO = int(os.environ.get("ANALISTA_PUERTO", "8756"))
HOST = os.environ.get("ANALISTA_HOST", "0.0.0.0")  # 0.0.0.0 para que entre el movil

# --------------------------------------------------------------------------
# Modelos (servidor OpenAI-compatible: LM Studio, Ollama, llama.cpp)
# --------------------------------------------------------------------------
LLM_URL_DEFECTO = "http://localhost:1234/v1"
LLM_MODELO_DEFECTO = ""      # vacio = autodetectar el que este cargado
EMBED_MODELO_DEFECTO = "text-embedding-nomic-embed-text-v1.5"

# El modelo razona antes de responder y ese razonamiento gasta del mismo cupo:
# con un tope bajo devuelve contenido VACIO sin ningun error. Ver llm.py.
MAX_TOKENS = 3500
TEMPERATURA = 0.2            # baja: fidelidad al documento, no creatividad

# Recuperacion
FRAGMENTOS = 20              # cuantos trozos se pasan al modelo
CHARS_POR_FRAGMENTO = 4000
MAX_POR_DOCUMENTO = 2        # evita que un solo archivo copie toda la respuesta


def asegurar_dirs() -> None:
    DATOS.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------
# Ajustes persistentes
# --------------------------------------------------------------------------
_DEFECTOS = {
    "corpus_dir": CORPUS_DEFECTO,
    "llm_url": LLM_URL_DEFECTO,
    "llm_api_key": "",
    "llm_modelo": LLM_MODELO_DEFECTO,
    "embed_modelo": EMBED_MODELO_DEFECTO,
    "fragmentos": FRAGMENTOS,
    "max_por_documento": MAX_POR_DOCUMENTO,
}


def leer_ajustes() -> dict:
    asegurar_dirs()
    datos = dict(_DEFECTOS)
    if AJUSTES_FILE.exists():
        try:
            datos.update(json.loads(AJUSTES_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass  # ajustes corruptos: se vuelve a los valores por defecto
    return datos


def guardar_ajustes(nuevos: dict) -> dict:
    actuales = leer_ajustes()
    for clave, valor in nuevos.items():
        if clave in _DEFECTOS:
            actuales[clave] = valor
    asegurar_dirs()
    AJUSTES_FILE.write_text(
        json.dumps(actuales, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return actuales


def corpus_dir() -> Path | None:
    ruta = (leer_ajustes().get("corpus_dir") or "").strip()
    return Path(ruta) if ruta else None
