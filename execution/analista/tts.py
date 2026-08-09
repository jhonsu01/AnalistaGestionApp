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
    # Muy lento y con mucha separacion entre silabas: para apuntar cifras
    # mientras se escucha, que es cuando un numero mal oido cuesta caro.
    "respuesta": {
        "nombre": "Dictado (para anotar cifras)",
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


# --------------------------------------------------------------------------
# Preparar el texto para que suene a persona, no a volcado de Markdown
# --------------------------------------------------------------------------
_UNIDADES = ["", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete",
             "ocho", "nueve", "diez", "once", "doce", "trece", "catorce",
             "quince", "dieciséis", "diecisiete", "dieciocho", "diecinueve",
             "veinte", "veintiuno", "veintidós", "veintitrés", "veinticuatro",
             "veinticinco", "veintiséis", "veintisiete", "veintiocho",
             "veintinueve"]
_DECENAS = ["", "", "", "treinta", "cuarenta", "cincuenta", "sesenta",
            "setenta", "ochenta", "noventa"]
_CENTENAS = ["", "ciento", "doscientos", "trescientos", "cuatrocientos",
             "quinientos", "seiscientos", "setecientos", "ochocientos",
             "novecientos"]


def _hasta_999(n: int) -> str:
    if n == 0:
        return ""
    if n == 100:
        return "cien"
    partes = []
    if n >= 100:
        partes.append(_CENTENAS[n // 100])
        n %= 100
    if n:
        if n < 30:
            partes.append(_UNIDADES[n])
        else:
            palabra = _DECENAS[n // 10]
            if n % 10:
                palabra += f" y {_UNIDADES[n % 10]}"
            partes.append(palabra)
    return " ".join(partes)


def _apocope(texto: str) -> str:
    """"uno" pasa a "un" delante de una magnitud: sesenta y un millones."""
    if texto.endswith("veintiuno"):
        return texto[:-len("veintiuno")] + "veintiún"
    if texto.endswith("uno"):
        return texto[:-3] + "un"
    return texto


def numero_a_palabras(n: int) -> str:
    """Entero a palabras en español, con la escala larga que se usa aquí.

    Importa acertar la magnitud: en español un billón son un millón de millones,
    no mil millones. Leer "22.261.110.622" como "veintidós millones" cambia el
    dato por un factor de mil, que en cifras de gasto público no es un detalle.
    """
    if n == 0:
        return "cero"
    if n < 0:
        return f"menos {numero_a_palabras(-n)}"

    escalas = [
        (10 ** 12, "billón", "billones"),
        (10 ** 6, "millón", "millones"),
        (10 ** 3, "mil", "mil"),
    ]
    partes = []
    for valor, singular, plural in escalas:
        if n >= valor:
            cuantos, n = divmod(n, valor)
            if valor == 10 ** 3:
                # "mil", no "uno mil"; y "veintitrés mil"
                partes.append("mil" if cuantos == 1
                              else f"{_apocope(numero_a_palabras(cuantos))} mil")
            elif cuantos == 1:
                partes.append(f"un {singular}")
            else:
                partes.append(f"{_apocope(numero_a_palabras(cuantos))} {plural}")
    if n:
        partes.append(_hasta_999(n))
    return " ".join(p for p in partes if p)


# En este país el punto separa los miles y la coma los decimales: 1.234.567,89
_NUM = r"(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d+))?"
_MAGNITUD = r"(?:\s*(miles de millones|mil millones|millones|millón|billones|billón))?"

# "$ 380.816 millones" -> hay que leer la magnitud ANTES de decir "de pesos".
# La cola opcional se captura para NO repetirla: el modelo a veces ya escribe
# "$ 2 billones de pesos", y sin esto sonaba "dos billones de pesos de pesos".
_RE_DINERO = re.compile(
    rf"\$\s*{_NUM}{_MAGNITUD}(\s+de\s+pesos|\s+pesos)?", re.IGNORECASE)
# Cifras largas sueltas, sin símbolo de moneda delante
_RE_CIFRA = re.compile(rf"(?<![\w.,$]){_NUM}")


def _en_palabras(entero: str, decimales: str | None) -> str:
    texto = numero_a_palabras(int(entero.replace(".", "")))
    if decimales:
        limpio = decimales.rstrip("0")
        if limpio:
            texto += f" coma {numero_a_palabras(int(limpio))}"
    return texto


def _leer_dinero(m: re.Match) -> str:
    cifra = _en_palabras(m.group(1), m.group(2))
    magnitud = (m.group(3) or "").lower()
    return f"{cifra} {magnitud} de pesos" if magnitud else f"{cifra} pesos"


def _leer_cifra(m: re.Match) -> str:
    # Solo las largas: los años y los números pequeños ya se leen bien solos.
    if "." not in m.group(1) and len(m.group(1)) <= 4 and not m.group(2):
        return m.group(0)
    return _en_palabras(m.group(1), m.group(2))


def limpiar_para_voz(texto: str) -> str:
    """Quita la notación que solo tiene sentido escrita y lee bien las cifras.

    Nace de escuchar la app: leía literalmente "asterisco asterisco" en cada
    negrita, decía "dólar" ante un símbolo que aquí siempre son pesos, y las
    cifras largas salían ininteligibles.
    """
    t = texto

    # Markdown: se va entero, incluidas las viñetas y los separadores.
    t = re.sub(r"```.*?```", " ", t, flags=re.DOTALL)      # bloques de código
    t = re.sub(r"^\s{0,3}#{1,6}\s*", "", t, flags=re.MULTILINE)   # títulos
    t = re.sub(r"^\s*[-*•]\s+", "", t, flags=re.MULTILINE)        # viñetas
    t = re.sub(r"^\s*[-*_]{3,}\s*$", "", t, flags=re.MULTILINE)   # separadores
    t = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", t)        # **negrita**, *cursiva*
    t = re.sub(r"[*_`]", "", t)                            # símbolos sueltos
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)        # [texto](enlace)
    t = t.replace("|", ", ")                               # tablas

    # El dinero primero: "$" nunca es "dólar" aquí, y la palabra "pesos" va
    # DESPUÉS de la magnitud ("380.816 millones de pesos", no "pesos 380.816").
    t = _RE_DINERO.sub(_leer_dinero, t)
    t = _RE_CIFRA.sub(_leer_cifra, t)
    t = t.replace("$", " pesos ")   # símbolos sueltos que quedaran por ahí

    t = t.replace("%", " por ciento")
    t = re.sub(r"[ \t]{2,}", " ", t)
    return t.strip()


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

    # Sin sustituciones a escondidas: si la voz pedida no esta, se devuelve
    # None y quien llama avisa. Antes se caia en la primera voz instalada y el
    # usuario elegia una voz femenina argentina y le respondia un hombre
    # espanol, sin explicacion ninguna.
    modelo = _cargar_voz(voz)
    if modelo is None:
        return None

    syn_config = _config_sintesis(ritmo, VOCES[voz].get("hablante"))

    partes: list[np.ndarray] = []
    sr_base = 22050
    silencio = None
    for frase in _frases(limpiar_para_voz(texto)):
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
