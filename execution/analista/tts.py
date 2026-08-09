"""Sintesis de voz: texto -> audio, con Piper ONNX sobre onnxruntime.

Corre en CPU y sin red. Cada voz es un modelo .onnx de `voices/`; el ritmo de
lectura se ajusta con `length_scale` (velocidad) y `noise_w_scale` (expresividad).
"""
from __future__ import annotations

import io
import re
import threading
import wave
from pathlib import Path
from typing import Optional

import numpy as np

from . import config

# --------------------------------------------------------------------------
# Catalogo de voces
# --------------------------------------------------------------------------
# `archivo` se busca por prefijo dentro de voices/, asi la app no se rompe si
# el usuario instala otra calidad de la misma voz.
# `archivo` se busca por PREFIJO: la app no se rompe si instalas otra calidad.
# `hablante` es el speaker_id dentro del modelo (sharvard trae M y F en uno).
VOCES = {
    "es_davefx": {
        "nombre": "Dave",
        "pais": "Espana", "bandera": "🇪🇸",
        "descripcion": "Masculina, calida y grave (119 Hz)",
        "archivo": "es_ES-davefx", "hablante": None,
    },
    "es_sharvard_m": {
        "nombre": "Harvard (M)",
        "pais": "Espana", "bandera": "🇪🇸",
        "descripcion": "Masculina, neutra y clara (124 Hz)",
        "archivo": "es_ES-sharvard", "hablante": 0,
    },
    "es_sharvard_f": {
        "nombre": "Harvard (F)",
        "pais": "Espana", "bandera": "🇪🇸",
        "descripcion": "Femenina, neutra y clara (206 Hz)",
        "archivo": "es_ES-sharvard", "hablante": 1,
    },
    "es_carlfm": {
        "nombre": "Carlos",
        "pais": "Espana", "bandera": "🇪🇸",
        "descripcion": "Masculina, ligera (113 Hz)",
        "archivo": "es_ES-carlfm", "hablante": None,
    },
    "mx_claude": {
        "nombre": "Claudia",
        "pais": "Mexico", "bandera": "🇲🇽",
        "descripcion": "Femenina, acento mexicano (180 Hz)",
        "archivo": "es_MX-claude", "hablante": None,
    },
    "mx_ald": {
        "nombre": "Aldo",
        "pais": "Mexico", "bandera": "🇲🇽",
        "descripcion": "Masculina, acento mexicano (145 Hz)",
        "archivo": "es_MX-ald", "hablante": None,
    },
    "ar_daniela": {
        "nombre": "Daniela",
        "pais": "Argentina", "bandera": "🇦🇷",
        "descripcion": "Femenina, rioplatense y expresiva (182 Hz)",
        "archivo": "es_AR-daniela", "hablante": None,
    },
}
VOZ_POR_DEFECTO = "es_davefx"

# Ritmo de lectura. length_scale > 1 alarga las silabas (mas pausado);
# noise_w_scale sube la variacion de duracion por fonema, que es lo que se
# percibe como "dramatismo" al narrar.
RITMOS = {
    "normal": {"nombre": "Normal", "length_scale": 1.0, "noise_w_scale": 0.8},
    "pausado": {"nombre": "Pausado", "length_scale": 1.25, "noise_w_scale": 0.95},
    # Para ninos: muy lento y con mucha variacion de duracion por fonema, que es
    # lo que se percibe como dramatismo al narrar.
    "respuesta": {
        "nombre": "Cuento (para peques)",
        "length_scale": 1.6,
        "noise_w_scale": 1.1,
    },
}
RITMO_POR_DEFECTO = "pausado"

# Silencio entre frases, en segundos. Un analista respira entre ideas.
PAUSA_FRASE = {"normal": 0.2, "pausado": 0.4, "respuesta": 0.65}

_VOCES_CARGADAS: dict[str, object] = {}
_LOCK_CARGA = threading.Lock()


# --------------------------------------------------------------------------
# 1. Sintesis base: Piper
# --------------------------------------------------------------------------
def _fichero_voz(clave: str) -> Optional[Path]:
    if not config.VOCES_DIR.exists():
        return None
    prefijo = VOCES.get(clave, {}).get("archivo", "")
    for onnx in sorted(config.VOCES_DIR.glob(f"{prefijo}*.onnx")):
        if onnx.with_suffix(".onnx.json").exists():
            return onnx
    return None


def _cargar_voz(clave: str):
    """Carga perezosa. La cache va por ARCHIVO, no por clave: sharvard M y F
    comparten el mismo .onnx y seria absurdo tenerlo dos veces en memoria.

    SOLO se cachean los aciertos. Cachear el fallo parecia inofensivo y era el
    bug mas escurridizo de la app: el servidor arranca sin voces instaladas,
    guardaba None, y como las voces se descargan DESDE la propia app, esa
    entrada envenenada sobrevivia a la descarga. La voz aparecia instalada, se
    podia seleccionar, y el audio fallaba siempre hasta reiniciar.
    """
    archivo = VOCES.get(clave, {}).get("archivo", clave)
    cargado = _VOCES_CARGADAS.get(archivo)
    if cargado is not None:
        return cargado
    with _LOCK_CARGA:
        cargado = _VOCES_CARGADAS.get(archivo)
        if cargado is not None:
            return cargado
        ruta = _fichero_voz(clave)
        if ruta is None:
            return None
        try:
            from piper import PiperVoice

            modelo = PiperVoice.load(str(ruta))
        except Exception:
            return None
        _VOCES_CARGADAS[archivo] = modelo
        return modelo


def olvidar_cache() -> None:
    """Se llama tras instalar una voz nueva, para que se recargue el catalogo."""
    with _LOCK_CARGA:
        _VOCES_CARGADAS.clear()


def voces_disponibles() -> list[dict]:
    return [
        {
            "id": clave,
            "nombre": datos["nombre"],
            "pais": datos["pais"],
            "bandera": datos["bandera"],
            "descripcion": datos["descripcion"],
            "instalada": _fichero_voz(clave) is not None,
        }
        for clave, datos in VOCES.items()
    ]


def hay_alguna_voz() -> bool:
    return any(_fichero_voz(c) is not None for c in VOCES)


def _wav_bytes(muestras: np.ndarray, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(muestras.astype(np.int16).tobytes())
    return buf.getvalue()


def _frases(texto: str, maximo: int = 220) -> list[str]:
    """Parte el texto en unidades cortas, para pausar entre ellas."""
    piezas: list[str] = []
    actual = ""
    for parte in re.split(r"(?<=[.!?:;])\s+", texto.strip()):
        if not parte:
            continue
        if len(actual) + len(parte) + 1 <= maximo:
            actual = f"{actual} {parte}".strip()
        else:
            if actual:
                piezas.append(actual)
            while len(parte) > maximo:
                corte = parte.rfind(",", 0, maximo)
                corte = corte if corte > maximo // 3 else maximo
                piezas.append(parte[:corte].strip())
                parte = parte[corte:].lstrip(", ")
            actual = parte
    if actual:
        piezas.append(actual)
    return piezas or [texto]


def _config_sintesis(ritmo: str, hablante=None):
    ajustes = RITMOS.get(ritmo, RITMOS[RITMO_POR_DEFECTO])
    try:
        from piper import SynthesisConfig

        return SynthesisConfig(
            speaker_id=hablante,
            length_scale=ajustes["length_scale"],
            noise_w_scale=ajustes["noise_w_scale"],
            normalize_audio=True,
        )
    except Exception:
        return None


def _piper(modelo, texto: str, syn_config) -> tuple[Optional[np.ndarray], int]:
    trozos = []
    sample_rate = 22050
    try:
        generador = modelo.synthesize(texto, syn_config=syn_config)
    except TypeError:  # versiones antiguas sin syn_config
        generador = modelo.synthesize(texto)

    for chunk in generador:
        if hasattr(chunk, "audio_int16_array"):
            trozos.append(np.asarray(chunk.audio_int16_array, dtype=np.int16))
            sample_rate = getattr(chunk, "sample_rate", sample_rate)
        elif isinstance(chunk, (bytes, bytearray)):
            trozos.append(np.frombuffer(bytes(chunk), dtype=np.int16))
        else:
            trozos.append(np.asarray(chunk, dtype=np.int16))

    if not trozos:
        return None, sample_rate
    return np.concatenate(trozos), sample_rate


def sintetizar(
    texto: str,
    voz: str = VOZ_POR_DEFECTO,
    ritmo: str = RITMO_POR_DEFECTO,
) -> Optional[bytes]:
    """Texto -> WAV en bytes. None si no hay motor de sintesis local."""
    if voz not in VOCES:
        voz = VOZ_POR_DEFECTO
    if ritmo not in RITMOS:
        ritmo = RITMO_POR_DEFECTO

    modelo = _cargar_voz(voz)
    if modelo is None:  # esa voz no esta instalada: probamos las otras
        for alternativa in VOCES:
            modelo = _cargar_voz(alternativa)
            if modelo is not None:
                break
    if modelo is None:
        return None

    syn_config = _config_sintesis(ritmo, VOCES[voz].get("hablante"))

    partes: list[np.ndarray] = []
    sr_base = 22050
    silencio = None
    for frase in _frases(texto):
        audio, sr = _piper(modelo, frase, syn_config)
        if audio is None:
            continue
        sr_base = sr
        if silencio is None:
            silencio = np.zeros(int(PAUSA_FRASE.get(ritmo, 0.3) * sr), dtype=np.int16)
        if partes:
            partes.append(silencio)
        partes.append(audio)

    if not partes:
        return None
    return _wav_bytes(np.concatenate(partes), sr_base)


def estado_voz() -> dict:
    return {
        "voces": voces_disponibles(),
        "ritmos": [{"id": k, "nombre": v["nombre"]} for k, v in RITMOS.items()],
        "voz_por_defecto": VOZ_POR_DEFECTO,
        "ritmo_por_defecto": RITMO_POR_DEFECTO,
        "piper": {"disponible": hay_alguna_voz(), "carpeta": str(config.VOCES_DIR)},
    }
