"""Arranca el Analista de Gestion.  Uso:  python run_analista.py"""
from __future__ import annotations

import os
import socket
import sys
import threading
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "execution"))


def _preparar_salida() -> None:
    """Garantiza que existan sys.stdout y sys.stderr.

    `pythonw.exe` lanzado sin consola los deja en None, y uvicorn llama a
    `sys.stdout.isatty()` al configurar su registro: sin esto el servidor
    muere al arrancar desde el acceso directo, sin dejar rastro del motivo.
    """
    if sys.stdout is None or sys.stderr is None:
        destino = Path(__file__).resolve().parent / ".tmp" / "servidor.log"
        destino.parent.mkdir(parents=True, exist_ok=True)
        registro = open(destino, "a", encoding="utf-8", errors="replace", buffering=1)
        if sys.stdout is None:
            sys.stdout = registro
        if sys.stderr is None:
            sys.stderr = registro

    # La consola de Windows usa cp1252 y revienta con acentos: forzamos UTF-8.
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _ip_local() -> str:
    """IP de esta maquina en la red, para conectar el movil."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def main() -> int:
    _preparar_salida()

    from analista import config  # noqa: E402

    import uvicorn

    puerto = config.PUERTO
    url = f"http://127.0.0.1:{puerto}"

    print("=" * 62)
    print("  Analista de Gestion Publica")
    print("=" * 62)
    print(f"  En este equipo : {url}")
    print(f"  Desde el movil : http://{_ip_local()}:{puerto}")
    print(f"  Datos          : {config.DATOS}")
    print("=" * 62)

    if os.environ.get("ANALISTA_SIN_NAVEGADOR") != "1":
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    uvicorn.run(
        "analista.server:app",
        host=config.HOST,
        port=puerto,
        log_level="warning",
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
