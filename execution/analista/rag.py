"""[DET] Busqueda semantica sobre el corpus indexado.

Carga el indice de vectores que el usuario genero aparte y recupera los
fragmentos mas parecidos a la pregunta. La app NO trae los datos: apunta a la
carpeta que el usuario indique en los ajustes.

La carpeta del corpus debe contener:
    embeddings.npy    matriz (N, dims) de vectores YA normalizados
    metadatos.jsonl   una linea por vector, en el MISMO orden que la matriz
    indice.tv         (opcional) indice comprimido turbovec, mas rapido
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Optional

import numpy as np

from . import config

# El indice se carga una vez y se reutiliza: son cientos de MB.
_MATRIZ = None
_METADATOS: list[dict] = []
_TEXTOS: dict[str, str] = {}
_RUTA_CARGADA: Optional[Path] = None
_LOCK = threading.Lock()

# Sufijo que se anade al acortar nombres largos: distintas copias del MISMO
# documento acaban con hashes distintos y parecen archivos diferentes.
RE_SUFIJO_HASH = re.compile(r"_[0-9a-f]{8,12}(?=\.[A-Za-z0-9]+$|$)")


def base_documento(nombre: str) -> str:
    return RE_SUFIJO_HASH.sub("", nombre or "").strip().lower()


def estado() -> dict:
    carpeta = config.corpus_dir()
    if not carpeta:
        return {"listo": False, "motivo": "sin_carpeta"}
    if not (carpeta / "embeddings.npy").exists():
        return {"listo": False, "motivo": "falta_embeddings", "carpeta": str(carpeta)}
    info = {}
    if (carpeta / "info.json").exists():
        try:
            info = json.loads((carpeta / "info.json").read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "listo": True,
        "carpeta": str(carpeta),
        "vectores": info.get("vectores") or (_MATRIZ.shape[0] if _MATRIZ is not None else 0),
        "modelo": info.get("modelo", ""),
        "cargado": _MATRIZ is not None,
        "turbovec": (carpeta / "indice.tv").exists(),
        # Sin textos la app "funciona" pero responde siempre que no sabe:
        # conviene que la interfaz pueda avisarlo.
        "textos": len(_TEXTOS),
    }


def cargar(forzar: bool = False) -> bool:
    """Carga el indice en memoria. Devuelve True si quedo listo."""
    global _MATRIZ, _METADATOS, _TEXTOS, _RUTA_CARGADA

    carpeta = config.corpus_dir()
    if not carpeta or not (carpeta / "embeddings.npy").exists():
        return False

    with _LOCK:
        if _MATRIZ is not None and _RUTA_CARGADA == carpeta and not forzar:
            return True

        # mmap: no carga 1,2 GB en RAM de golpe, los lee del disco a demanda.
        _MATRIZ = np.load(carpeta / "embeddings.npy", mmap_mode="r")

        _METADATOS = []
        ruta_meta = carpeta / "metadatos.jsonl"
        if ruta_meta.exists():
            with ruta_meta.open(encoding="utf-8") as fh:
                for linea in fh:
                    if linea.strip():
                        _METADATOS.append(json.loads(linea))

        # El texto de cada fragmento NO esta en metadatos.jsonl (solo van las
        # etiquetas): vive en corpus.jsonl, que suele quedar en la carpeta
        # hermana del dataset. Sin el, el modelo recibe nombres de archivo sin
        # contenido y responde "no tengo esa informacion" con el dato delante.
        _TEXTOS = {}
        for candidata in (
            carpeta / "corpus.jsonl",
            carpeta.parent / "dataset" / "corpus.jsonl",
            carpeta.parent / "corpus.jsonl",
        ):
            if not candidata.exists():
                continue
            with candidata.open(encoding="utf-8") as fh:
                for linea in fh:
                    if not linea.strip():
                        continue
                    try:
                        r = json.loads(linea)
                    except json.JSONDecodeError:
                        continue
                    if r.get("chunk_id"):
                        _TEXTOS[r["chunk_id"]] = r.get("texto", "")
            break

        _RUTA_CARGADA = carpeta
        return True


def _diversificar(
    candidatos: list[tuple[int, float]], k: int, max_por_documento: int
) -> list[tuple[int, float]]:
    """Impide que un solo documento copie toda la respuesta.

    El corpus repite archivos entre paquetes y cada copia lleva un hash
    distinto en el nombre. Sin agruparlas, una consulta puede devolver ocho
    veces el mismo estado financiero y dejar fuera lo que de verdad responde.
    """
    por_documento: dict[str, int] = {}
    vistas: set[tuple[str, str]] = set()
    salida: list[tuple[int, float]] = []

    for pos, sim in candidatos:
        if pos >= len(_METADATOS):
            continue
        m = _METADATOS[pos]
        clave = f"{m.get('entidad')}|{base_documento(m.get('archivo_origen', ''))}"
        firma = (clave, (m.get("seccion") or "").strip().lower())
        if firma in vistas or por_documento.get(clave, 0) >= max_por_documento:
            continue
        por_documento[clave] = por_documento.get(clave, 0) + 1
        vistas.add(firma)
        salida.append((pos, sim))
        if len(salida) >= k:
            break
    return salida


def buscar(
    vector: np.ndarray,
    k: int = 20,
    sector: str = "",
    entidad: str = "",
    max_por_documento: int = 2,
) -> list[dict]:
    """Devuelve los fragmentos mas parecidos, ya diversificados."""
    if not cargar():
        return []

    n = min(len(_METADATOS), _MATRIZ.shape[0])
    amplio = max(k * 8, 60)

    permitidos = None
    if sector or entidad:
        permitidos = np.array(
            [
                i for i in range(n)
                if (not sector or sector.lower() in (_METADATOS[i].get("sector") or "").lower())
                and (not entidad or entidad.lower() in (_METADATOS[i].get("entidad") or "").lower())
            ],
            dtype=np.int64,
        )
        if permitidos.size == 0:
            return []

    if permitidos is not None:
        sub = np.asarray(_MATRIZ[permitidos])
        puntajes = sub @ vector
        mejores = np.argsort(-puntajes)[:amplio]
        candidatos = [(int(permitidos[j]), float(puntajes[j])) for j in mejores]
    else:
        candidatos = _buscar_global(vector, amplio, n)

    resultados = []
    for pos, sim in _diversificar(candidatos, k, max_por_documento):
        m = _METADATOS[pos]
        resultados.append(
            {
                "posicion": pos,
                "similitud": round(sim, 4),
                "entidad": m.get("entidad", ""),
                "sector": m.get("sector", ""),
                "archivo": m.get("archivo_origen", ""),
                "seccion": m.get("seccion", ""),
                "tipo": m.get("tipo_documento", ""),
                "texto": _TEXTOS.get(m.get("chunk_id"), ""),
            }
        )
    return resultados


def _buscar_global(vector: np.ndarray, amplio: int, n: int) -> list[tuple[int, float]]:
    """Busqueda sin filtros: usa turbovec si esta, si no numpy."""
    carpeta = config.corpus_dir()
    indice_tv = carpeta / "indice.tv" if carpeta else None

    if indice_tv and indice_tv.exists():
        try:
            import turbovec

            idx = turbovec.TurboQuantIndex.load(str(indice_tv))
            puntajes, posiciones = idx.search(vector.reshape(1, -1), k=amplio)
            return [
                (int(p), float(s))
                for p, s in zip(
                    np.asarray(posiciones).ravel(), np.asarray(puntajes).ravel()
                )
                if 0 <= int(p) < n
            ]
        except Exception:
            pass  # cualquier fallo del indice comprimido cae a numpy

    puntajes = np.asarray(_MATRIZ[:n]) @ vector
    mejores = np.argsort(-puntajes)[:amplio]
    return [(int(i), float(puntajes[i])) for i in mejores]


def sectores() -> list[str]:
    if not cargar():
        return []
    return sorted({m.get("sector", "") for m in _METADATOS if m.get("sector")})


def entidades(sector: str = "") -> list[str]:
    if not cargar():
        return []
    return sorted(
        {
            m.get("entidad", "")
            for m in _METADATOS
            if m.get("entidad")
            and (not sector or sector.lower() in (m.get("sector") or "").lower())
        }
    )
