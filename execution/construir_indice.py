"""[DET] Construye el índice de búsqueda (RAG) a partir de un corpus.jsonl.

Toma un archivo con un fragmento de texto por línea y produce los tres archivos
que la aplicación necesita para buscar por significado:

    embeddings.npy    matriz (N, dims) de vectores normalizados
    metadatos.jsonl   una línea por vector, EN EL MISMO ORDEN
    info.json         modelo, dimensiones y número de vectores

No depende de nada más que numpy y la biblioteca estándar. Sirve para cualquier
corpus, no solo para los informes de empalme: si tienes tus documentos en el
formato de entrada, esto te da un índice consultable.

Uso:
    python execution/construir_indice.py --corpus mis_datos/corpus.jsonl --salida mi_indice
    python execution/construir_indice.py --corpus c.jsonl --salida idx --limite 500   # prueba
    python execution/construir_indice.py --corpus c.jsonl --salida idx --consolidar   # solo unir

Formato de entrada (una línea JSON por fragmento):

    {"chunk_id": "a1b2c3", "texto": "...", "entidad": "...", "sector": "...",
     "archivo_origen": "informe.pdf", "seccion": "3.2 Presupuesto"}

`chunk_id` y `texto` son obligatorios. El resto son etiquetas que la aplicación
muestra al citar la fuente y usa para filtrar; si faltan, quedan vacías.
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

# Campos que viajan a metadatos.jsonl. El texto NO va aquí: se queda en el
# corpus.jsonl, que la aplicación lee aparte. Duplicarlo multiplicaría por
# varios GB un archivo que se carga entero en memoria.
ETIQUETAS = ("chunk_id", "documento_id", "sector", "entidad", "titulo",
             "seccion", "archivo_origen", "tipo_documento")

# Cuánto texto se manda a embeder por fragmento. nomic-embed admite 2.048
# tokens (~8.000 caracteres). Quedarse corto aquí es el error más caro y más
# silencioso del proceso: el vector representa solo el principio del fragmento
# y las búsquedas fallan sin que nada avise.
MAX_CHARS = 7000

# Medido contra LM Studio: el tamaño de lote lo cambia todo.
#     lote de  1 ->  0,8 fragmentos/s
#     lote de  8 -> 27,9 fragmentos/s
#     lote de 32 -> 31,1 fragmentos/s   <- se satura aquí
LOTE = 32
BLOQUE = 3000          # vectores por archivo parcial (permite reanudar)


def embeder_lote(url: str, clave: str, modelo: str,
                 textos: list[str], timeout: int = 300) -> np.ndarray | None:
    cuerpo = json.dumps({"model": modelo, "input": textos}).encode("utf-8")
    cabeceras = {"Content-Type": "application/json"}
    if clave:
        cabeceras["Authorization"] = f"Bearer {clave}"
    peticion = urllib.request.Request(
        f"{url}/embeddings", data=cuerpo, headers=cabeceras, method="POST")
    try:
        with urllib.request.urlopen(peticion, timeout=timeout) as r:
            datos = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None
    filas = datos.get("data") or []
    if len(filas) != len(textos):
        return None                      # respuesta incompleta: lote inválido
    return np.asarray([f["embedding"] for f in filas], dtype=np.float32)


def normalizar(matriz: np.ndarray) -> np.ndarray:
    """Vectores de norma 1: así el producto escalar ES el coseno."""
    normas = np.linalg.norm(matriz, axis=1, keepdims=True)
    normas[normas == 0] = 1.0
    return matriz / normas


def leer_corpus(ruta: Path, limite: int = 0) -> list[dict]:
    registros = []
    with ruta.open(encoding="utf-8") as fh:
        for linea in fh:
            if not linea.strip():
                continue
            try:
                r = json.loads(linea)
            except json.JSONDecodeError:
                continue
            if r.get("chunk_id") and r.get("texto"):
                registros.append(r)
                if limite and len(registros) >= limite:
                    break
    return registros


def consolidar(partes: Path, salida: Path, registros: list[dict],
               modelo: str) -> int:
    """Une los bloques parciales en un único índice y verifica que cuadra."""
    archivos = sorted(partes.glob("bloque_*.npy"),
                      key=lambda p: int(p.stem.split("_")[1]))
    if not archivos:
        print("No hay bloques que consolidar.")
        return 1

    matriz = np.vstack([np.load(p) for p in archivos])

    # INVARIANTE: un vector por fragmento, en el mismo orden. Si esto no cuadra,
    # cada respuesta citaría el documento equivocado y nada daría error. En este
    # proyecto pasó: un lote se desbordó del límite del bloque y hubo 3.004
    # vectores para 3.000 fragmentos.
    if len(matriz) != len(registros):
        print(f"[!] DESAJUSTE: {len(matriz)} vectores para {len(registros)} "
              f"fragmentos. El índice citaría fuentes equivocadas.")
        print("    Borra la carpeta de partes y vuelve a generarlo.")
        return 1

    salida.mkdir(parents=True, exist_ok=True)
    np.save(salida / "embeddings.npy", matriz)

    with (salida / "metadatos.jsonl").open("w", encoding="utf-8") as fh:
        for r in registros:
            fila = {c: r.get(c, "") for c in ETIQUETAS}
            fh.write(json.dumps(fila, ensure_ascii=False) + "\n")

    (salida / "info.json").write_text(json.dumps({
        "generado": datetime.now(timezone.utc).isoformat(),
        "modelo": modelo,
        "vectores": int(len(matriz)),
        "dimensiones": int(matriz.shape[1]),
        "normalizados": True,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nÍndice listo en {salida}")
    print(f"  {len(matriz):,} vectores de {matriz.shape[1]} dimensiones"
          .replace(",", "."))
    print(f"  {(salida / 'embeddings.npy').stat().st_size / 1e6:.0f} MB")
    print("\nCopia también tu corpus.jsonl junto al índice: la aplicación lee")
    print("de ahí el texto de cada fragmento para dárselo al modelo.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", required=True, type=Path,
                    help="corpus.jsonl de entrada")
    ap.add_argument("--salida", required=True, type=Path,
                    help="carpeta donde dejar el índice")
    ap.add_argument("--url", default=os.environ.get(
        "LLM_URL", "http://localhost:1234/v1"),
        help="servidor de embeddings compatible con OpenAI")
    ap.add_argument("--clave", default=os.environ.get("LLM_API_KEY", ""))
    ap.add_argument("--modelo", default=os.environ.get(
        "EMBED_MODEL", "text-embedding-nomic-embed-text-v1.5"))
    ap.add_argument("--lote", type=int, default=LOTE)
    ap.add_argument("--limite", type=int, default=0,
                    help="procesar solo los primeros N fragmentos (para probar)")
    ap.add_argument("--consolidar", action="store_true",
                    help="no embeder: solo unir los bloques ya generados")
    args = ap.parse_args()

    url = args.url.rstrip("/")
    partes = args.salida / "partes"
    partes.mkdir(parents=True, exist_ok=True)

    registros = leer_corpus(args.corpus, args.limite)
    if not registros:
        print(f"No encontré fragmentos válidos en {args.corpus}.")
        print("Cada línea necesita al menos 'chunk_id' y 'texto'.")
        return 1
    print(f"{len(registros):,} fragmentos en {args.corpus}".replace(",", "."))

    if args.consolidar:
        return consolidar(partes, args.salida, registros, args.modelo)

    total = len(registros)
    inicio_global = time.time()
    for arranque in range(0, total, BLOQUE):
        fin = min(arranque + BLOQUE, total)
        destino = partes / f"bloque_{arranque:08d}.npy"
        if destino.exists():
            continue                                     # ya estaba: se salta

        vectores = []
        for i in range(arranque, fin, args.lote):
            # min(...) es imprescindible: sin él, el último lote se desborda
            # del bloque y genera más vectores que fragmentos. Los vectores
            # dejan de corresponder con sus metadatos y las respuestas citan
            # documentos que no son, sin que nada falle.
            corte = min(i + args.lote, fin)
            textos = [r["texto"][:MAX_CHARS] for r in registros[i:corte]]

            matriz = None
            for intento, espera in enumerate((0, 4, 12)):
                if espera:
                    time.sleep(espera)
                matriz = embeder_lote(url, args.clave, args.modelo, textos)
                if matriz is not None:
                    break
                print(f"  reintento {intento + 1} en el lote {i}")
            if matriz is None:
                print(f"\n[!] El lote {i} falló tres veces. El servidor de "
                      f"embeddings no responde o el modelo no está cargado.")
                print("    Lo hecho hasta aquí está guardado: vuelve a lanzarlo "
                      "y seguirá donde lo dejó.")
                return 1
            vectores.append(matriz)

        bloque = normalizar(np.vstack(vectores))
        np.save(destino, bloque)

        hechos = fin
        ritmo = hechos / max(1e-6, time.time() - inicio_global)
        queda = (total - hechos) / max(1e-6, ritmo)
        print(f"  {hechos:>7,}/{total:,}  {ritmo:5.1f}/s  "
              f"faltan {queda / 60:5.1f} min".replace(",", "."))

    return consolidar(partes, args.salida, registros, args.modelo)


if __name__ == "__main__":
    raise SystemExit(main())
