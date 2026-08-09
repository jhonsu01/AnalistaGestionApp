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

# El modelo razona antes de responder y ese razonamiento gasta del MISMO cupo
# que la respuesta. Con 20 fragmentos el razonamiento se dispara y un tope de
# 3.500 se agotaba antes de escribir nada: la API devolvia contenido VACIO sin
# error alguno. Con holgura eso deja de pasar. Ver llm.py.
MAX_TOKENS = 8000
TEMPERATURA = 0.2            # baja: fidelidad al documento, no creatividad

# Ventana de contexto del modelo. gemma-4-26b admite 262.144; se deja un valor
# prudente y el usuario puede subirlo en Ajustes si su modelo da para mas.
CONTEXTO_MAXIMO = 32000      # tokens de ENTRADA que se consideran seguros

# Recuperacion
FRAGMENTOS = 20              # cuantos trozos se pasan al modelo
CHARS_POR_FRAGMENTO = 4000
MAX_POR_DOCUMENTO = 2        # evita que un solo archivo copie toda la respuesta

# Aproximacion para espanol: ~4 caracteres por token. Sirve para saber si el
# contexto cabe ANTES de enviarlo, en vez de descubrirlo por un error.
CHARS_POR_TOKEN = 4


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
    "max_tokens": MAX_TOKENS,
    "contexto_maximo": CONTEXTO_MAXIMO,
    "chars_por_fragmento": CHARS_POR_FRAGMENTO,
    # Voz
    "voz_activa": "",            # vacio = sin lectura en voz alta
    "voz_velocidad": 1.0,
}

# --------------------------------------------------------------------------
# Voces (Piper ONNX). Se descargan aparte: NO viajan en el instalador.
# --------------------------------------------------------------------------
VOCES_DIR = DATOS / "voces"


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
