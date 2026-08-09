"""[HYB] Cliente para servidores de modelos compatibles con OpenAI.

Funciona con LM Studio, Ollama (modo /v1), llama-server y cualquier otro que
exponga /v1/chat/completions y /v1/embeddings. La app no incrusta ningun
modelo: se conecta al que el usuario ya tenga corriendo.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Iterator, Optional

import numpy as np

from . import config

SISTEMA = (
    "Eres un analista de gestion publica colombiana. Respondes preguntas sobre "
    "los informes de empalme 2022-2026 del DNP usando UNICAMENTE los fragmentos "
    "que se te entregan.\n\n"
    "REGLAS:\n"
    "- Si los fragmentos no contienen la respuesta, dilo claramente. NUNCA "
    "inventes cifras, fechas ni nombres.\n"
    "- Cita la entidad de la que proviene cada dato.\n"
    "- Si hay cifras, reproducelas exactamente como aparecen.\n"
    "- Se concreto y directo."
)


def _ajustes() -> dict:
    return config.leer_ajustes()


def _cabeceras(clave: str) -> dict:
    cab = {"Content-Type": "application/json"}
    if clave:
        cab["Authorization"] = f"Bearer {clave}"
    return cab


def _pedir(ruta: str, cuerpo: dict, timeout: int = 300) -> Optional[dict]:
    aj = _ajustes()
    url = (aj.get("llm_url") or config.LLM_URL_DEFECTO).rstrip("/")
    req = urllib.request.Request(
        f"{url}{ruta}",
        data=json.dumps(cuerpo).encode("utf-8"),
        headers=_cabeceras(aj.get("llm_api_key", "")),
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


# --------------------------------------------------------------------------
# Estado del servidor
# --------------------------------------------------------------------------
def modelos() -> list[str]:
    aj = _ajustes()
    url = (aj.get("llm_url") or config.LLM_URL_DEFECTO).rstrip("/")
    req = urllib.request.Request(
        f"{url}/models", headers=_cabeceras(aj.get("llm_api_key", ""))
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            datos = json.loads(r.read().decode("utf-8"))
        return [m["id"] for m in datos.get("data", [])]
    except Exception:
        return []


def diagnostico() -> dict:
    aj = _ajustes()
    disponibles = modelos()
    return {
        "url": aj.get("llm_url", ""),
        "vivo": bool(disponibles),
        "modelos": disponibles,
        "modelo_activo": aj.get("llm_modelo") or (disponibles[0] if disponibles else ""),
        "embed_modelo": aj.get("embed_modelo", ""),
    }


# --------------------------------------------------------------------------
# Embeddings
# --------------------------------------------------------------------------
def embeder(texto: str) -> Optional[np.ndarray]:
    """Vector normalizado de la pregunta, listo para comparar por coseno."""
    aj = _ajustes()
    try:
        datos = _pedir(
            "/embeddings",
            {"model": aj.get("embed_modelo") or config.EMBED_MODELO_DEFECTO,
             "input": [texto]},
            timeout=120,
        )
    except Exception:
        return None
    if not datos or not datos.get("data"):
        return None
    v = np.asarray(datos["data"][0]["embedding"], dtype=np.float32)
    norma = float(np.linalg.norm(v))
    return v / (norma if norma else 1.0)


# --------------------------------------------------------------------------
# Respuesta
# --------------------------------------------------------------------------
def _prompt(pregunta: str, fragmentos: list[dict], chars: int) -> str:
    bloques = []
    for f in fragmentos:
        bloques.append(
            f"[{f.get('entidad')} — {f.get('sector')}]\n"
            f"Documento: {f.get('archivo')}\n"
            f"{(f.get('texto') or '')[:chars]}"
        )
    contexto = "\n\n---\n\n".join(bloques)
    return (
        f"FRAGMENTOS DE LOS INFORMES DE EMPALME:\n\n{contexto}\n\n"
        f"{'=' * 60}\n\nPREGUNTA: {pregunta}\n\n"
        "Responde usando solo los fragmentos anteriores."
    )


def comprimir(
    fragmentos: list[dict], chars: int, tope_tokens: int
) -> tuple[list[dict], int, str]:
    """Ajusta los fragmentos para que quepan en la ventana de contexto.

    Antes de enviar nada se estima cuanto ocupa el material. Si se pasa del
    tope, se comprime en dos escalones en vez de fallar:

      1. RECORTAR cada fragmento (menos texto por documento, mismos documentos)
      2. si aun no cabe, DESCARTAR los fragmentos menos parecidos

    Se recorta antes de descartar porque conservar muchas fuentes distintas da
    mejores respuestas que conservar pocas muy largas.
    """
    if not fragmentos:
        return fragmentos, 0, ""

    def coste(lista: list[dict], corte: int) -> int:
        total = sum(min(len(f.get("texto") or ""), corte) + 120 for f in lista)
        return total // config.CHARS_POR_TOKEN

    actual = coste(fragmentos, chars)
    if actual <= tope_tokens:
        return fragmentos, actual, ""

    # 1) Recortar el texto de cada fragmento
    for nuevo_corte in (3000, 2200, 1500, 1000, 700):
        if nuevo_corte >= chars:
            continue
        if coste(fragmentos, nuevo_corte) <= tope_tokens:
            return (
                fragmentos,
                coste(fragmentos, nuevo_corte),
                f"contexto comprimido: {chars} → {nuevo_corte} caracteres por fragmento",
            )
        chars_final = nuevo_corte

    # 2) Descartar los menos relevantes, manteniendo el recorte minimo
    corte = 700
    recortados = list(fragmentos)
    while len(recortados) > 3 and coste(recortados, corte) > tope_tokens:
        recortados.pop()  # vienen ordenados por similitud descendente
    return (
        recortados,
        coste(recortados, corte),
        f"contexto comprimido: {len(fragmentos)} → {len(recortados)} fragmentos "
        f"y {corte} caracteres cada uno",
    )


def responder_stream(
    pregunta: str, fragmentos: list[dict], chars: int = 4000
) -> Iterator[str]:
    """Emite la respuesta por trozos, para que se vea escribirse en pantalla.

    OJO CON max_tokens: los modelos tipo gemma razonan antes de responder y ese
    razonamiento gasta del MISMO cupo. Con 20 fragmentos el razonamiento se
    alarga y un tope de 3.500 se agotaba antes de escribir una sola palabra:
    la API devolvia contenido VACIO con finish_reason "length" y sin error, de
    modo que parecia que el corpus no tenia la informacion. Por eso el cupo va
    holgado y es configurable.
    """
    aj = _ajustes()
    url = (aj.get("llm_url") or config.LLM_URL_DEFECTO).rstrip("/")
    modelo = aj.get("llm_modelo") or ""
    if not modelo:
        disponibles = modelos()
        modelo = disponibles[0] if disponibles else "local-model"

    cuerpo = json.dumps(
        {
            "model": modelo,
            "messages": [
                {"role": "system", "content": SISTEMA},
                {"role": "user", "content": _prompt(pregunta, fragmentos, chars)},
            ],
            "temperature": config.TEMPERATURA,
            "max_tokens": int(aj.get("max_tokens") or config.MAX_TOKENS),
            "stream": True,
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        f"{url}/chat/completions",
        data=cuerpo,
        headers=_cabeceras(aj.get("llm_api_key", "")),
        method="POST",
    )

    emitido = False
    with urllib.request.urlopen(req, timeout=600) as respuesta:
        for linea_bytes in respuesta:
            linea = linea_bytes.decode("utf-8", errors="replace").strip()
            if not linea.startswith("data:"):
                continue
            datos = linea[5:].strip()
            if datos == "[DONE]":
                break
            try:
                trozo = json.loads(datos)
            except json.JSONDecodeError:
                continue
            eleccion = (trozo.get("choices") or [{}])[0]
            texto = (eleccion.get("delta") or {}).get("content")
            if texto:
                emitido = True
                yield texto

    if not emitido:
        # Sintoma tipico de cupo agotado por el razonamiento interno.
        cupo = int(aj.get("max_tokens") or config.MAX_TOKENS)
        yield (
            f"El modelo no devolvió texto (cupo actual: {cupo} tokens de salida).\n\n"
            "Casi siempre es porque agotó ese cupo razonando antes de escribir. "
            "No significa que el corpus no tenga la información.\n\n"
            "Qué probar, en este orden:\n"
            "- Subir **Tokens de respuesta** en Ajustes (16000 suele bastar).\n"
            "- Reducir **Fragmentos** a 8 o 10.\n"
            "- Comprobar en Ajustes que el modelo cargado sea el correcto."
        )


def probar_conexion() -> dict:
    """Comprueba de verdad que el servidor responde y que el modelo contesta.

    No basta con listar modelos: LM Studio puede listar uno que luego falla al
    generar. Aqui se le pide una respuesta minima de verdad.
    """
    aj = _ajustes()
    disponibles = modelos()
    if not disponibles:
        return {
            "ok": False,
            "detalle": "El servidor no responde. Revisa la URL y, si tu servidor "
                       "exige clave, que la clave sea correcta.",
        }

    modelo = aj.get("llm_modelo") or disponibles[0]
    if modelo not in disponibles:
        return {
            "ok": False,
            "modelos": disponibles,
            "detalle": f"El modelo '{modelo}' no está cargado en el servidor.",
        }

    # 1) El modelo de chat responde
    try:
        datos = _pedir(
            "/chat/completions",
            {
                "model": modelo,
                "messages": [{"role": "user", "content": "Responde solo: listo"}],
                "max_tokens": 2000,
                "temperature": 0,
            },
            timeout=120,
        )
        contenido = (datos["choices"][0]["message"].get("content") or "").strip()
        if not contenido:
            return {
                "ok": False,
                "modelos": disponibles,
                "detalle": "El modelo respondió vacío: agota su cupo razonando. "
                           "Sube 'Tokens de respuesta'.",
            }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "modelos": disponibles,
                "detalle": f"El modelo no respondió: {str(exc)[:120]}"}

    # 2) El modelo de embeddings responde y con cuantas dimensiones
    vector = embeder("prueba")
    if vector is None:
        return {
            "ok": False,
            "modelos": disponibles,
            "detalle": f"El modelo de embeddings '{aj.get('embed_modelo')}' no responde. "
                       "Cárgalo en el servidor o corrige su nombre.",
        }

    return {
        "ok": True,
        "modelos": disponibles,
        "detalle": f"Todo correcto. Chat: {modelo}. "
                   f"Embeddings: {len(vector)} dimensiones.",
        "dimensiones": len(vector),
    }
