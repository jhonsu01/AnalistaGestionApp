"""[DET] Descarga voces Piper en espanol a voices/. Idempotente.

Uso:
    python execution/instalar_voz.py --todas          # las 6 recomendadas
    python execution/instalar_voz.py --voz mx_claude  # una concreta
    python execution/instalar_voz.py --listar

De donde salen: del repositorio oficial de voces de Piper
(https://huggingface.co/rhasspy/piper-voices), que publica los modelos ONNX ya
entrenados y listos para sintetizar.

Nota: OpenSLR es un archivo de CORPUS de audio (grabaciones + transcripciones),
util para ENTRENAR una voz, no para usarla. Varias de estas voces se entrenaron
con corpus de ahi, pero el modelo listo para hablar es el .onnx de Piper.
"""
from __future__ import annotations

import argparse
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from analista import config

BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main/es"

# clave -> (ruta en el repo, MB aprox, descripcion)
CATALOGO = {
    "es_davefx":   ("es_ES/davefx/medium/es_ES-davefx-medium.onnx", 63, "Espana, calidad media"),
    "es_sharvard": ("es_ES/sharvard/medium/es_ES-sharvard-medium.onnx", 77, "Espana, DOS voces (M y F)"),
    "es_carlfm":   ("es_ES/carlfm/x_low/es_ES-carlfm-x_low.onnx", 28, "Espana, ligera"),
    "mx_claude":   ("es_MX/claude/high/es_MX-claude-high.onnx", 63, "Mexico, calidad alta"),
    "mx_ald":      ("es_MX/ald/medium/es_MX-ald-medium.onnx", 63, "Mexico, calidad media"),
    "ar_daniela":  ("es_AR/daniela/high/es_AR-daniela-high.onnx", 114, "Argentina, calidad alta"),
}

RECOMENDADAS = list(CATALOGO)


def descargar(url: str, destino: Path) -> bool:
    if destino.exists() and destino.stat().st_size > 0:
        print(f"    [=] {destino.name} (ya estaba)")
        return True
    print(f"    [>] {destino.name} ...", end="", flush=True)
    try:
        with urllib.request.urlopen(url, timeout=300) as r, open(destino, "wb") as fh:
            fh.write(r.read())
    except Exception as exc:
        print(f"  FALLO: {exc}")
        destino.unlink(missing_ok=True)
        return False
    print(f"  {destino.stat().st_size / 1e6:.1f} MB")
    return True


def instalar(clave: str) -> bool:
    # La carpeta se crea AQUI, no solo en main(): la app descarga voces desde
    # Ajustes llamando directamente a esta funcion, y sin el directorio la
    # descarga fallaba con "No such file or directory" aunque el archivo
    # estuviera perfectamente disponible en el servidor.
    config.asegurar_dirs()
    config.VOCES_DIR.mkdir(parents=True, exist_ok=True)

    rel, _, _ = CATALOGO[clave]
    nombre = Path(rel).name
    ok = descargar(f"{BASE}/{rel}", config.VOCES_DIR / nombre)
    ok = descargar(f"{BASE}/{rel}.json", config.VOCES_DIR / f"{nombre}.json") and ok
    return ok


def main() -> int:
    for flujo in (sys.stdout, sys.stderr):   # banderas en consola cp1252
        try:
            flujo.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--voz", choices=sorted(CATALOGO))
    ap.add_argument("--todas", action="store_true")
    ap.add_argument("--listar", action="store_true")
    args = ap.parse_args()

    if args.listar:
        print("Voces disponibles:")
        for k, (rel, mb, desc) in CATALOGO.items():
            existe = (config.VOCES_DIR / Path(rel).name).exists()
            print(f"  {'[x]' if existe else '[ ]'} {k:<12} {mb:>4} MB  {desc}")
        return 0

    config.asegurar_dirs()
    claves = [args.voz] if args.voz else RECOMENDADAS
    if not args.voz and not args.todas:
        claves = RECOMENDADAS

    total = sum(CATALOGO[k][1] for k in claves)
    print(f"Descargando {len(claves)} voces (~{total} MB) en {config.VOCES_DIR}")
    fallos = [k for k in claves if not instalar(k)]

    print("\nComprobando el motor de sintesis...")
    try:
        from analista import tts

        for v in tts.voces_disponibles():
            marca = "[x]" if v["instalada"] else "[ ]"
            print(f"  {marca} {v['bandera']} {v['nombre']:<22} {v['descripcion']}")
    except Exception as exc:
        print(f"  [!] {exc}")

    if fallos:
        print(f"\n[!] No se pudieron descargar: {', '.join(fallos)}")
        return 1
    print("\n[OK] Voces listas. Reinicia el Analista para verlas en el selector.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
