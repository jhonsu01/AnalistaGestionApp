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


def responder_stream(
    pregunta: str, fragmentos: list[dict], chars: int = 4000
) -> Iterator[str]:
    """Emite la respuesta por trozos, para que se vea escribirse en pantalla.

    OJO CON max_tokens: los modelos tipo gemma razonan antes de responder y ese
    razonamiento gasta del MISMO cupo (~1.200 tokens). Si se agota, la API
    devuelve contenido VACIO con finish_reason "length" y ningun error, y
    parece que el modelo no contesto. Por eso se pide con holgura.
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
            "max_tokens": config.MAX_TOKENS,
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
        yield (
            "El modelo no devolvio texto. Suele deberse a que agoto su cupo de "
            "tokens razonando antes de responder: prueba con menos fragmentos "
            "o revisa que el modelo cargado admita el contexto solicitado."
        )
