"""[DET] Arranca la app como lo hace un acceso directo y comprueba que responde.

Existe por un fallo concreto: probe `Cuentero.exe` lanzandolo desde una consola,
heredo un stdout valido y funciono. Desde el acceso directo NO hay consola,
`sys.stdout` queda en None, uvicorn llama a `sys.stdout.isatty()` al configurar
su registro y el servidor moria. La app se publico rota en dos equipos.

Por eso este guion lanza el ejecutable con `Win32_Process.Create`, que no pasa
ninguna consola al hijo, igual que Explorer. Es la unica forma de que la prueba
se parezca a lo que hace el usuario.

Uso:  python execution/probar_arranque.py <carpeta_instalada>
"""
from __future__ import annotations

import subprocess
import sys
import time
import urllib.request
from pathlib import Path

PUERTO = 8770
ESPERA = 90


def _powershell(guion: str) -> str:
    r = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", guion],
        capture_output=True, text=True, errors="replace",
    )
    return (r.stdout or "") + (r.stderr or "")


def _matar() -> None:
    # OJO: nunca incluir `python` aqui. Este guion corre en python.exe y se
    # mataba a si mismo (salia con 255 sin decir nada). El servidor de la app
    # corre en pythonw.exe, que es el que hay que cerrar.
    _powershell("Get-Process Cuentero,pythonw,llama-server "
                "-EA SilentlyContinue | Stop-Process -Force")
    time.sleep(2)


def _responde() -> bool:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{PUERTO}/api/vivo", timeout=3
        ) as r:
            return r.status == 200
    except Exception:
        return False


def probar(carpeta: Path) -> bool:
    exe = carpeta / "Cuentero.exe"
    if not exe.exists():
        print(f"  [X] no encuentro {exe}")
        return False

    _matar()
    print("  [>] lanzando SIN consola (como el acceso directo)...")
    salida = _powershell(
        "$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
        f"-Arguments @{{ CommandLine = '\"{exe}\"'; "
        f"CurrentDirectory = '{carpeta}' }}; $r.ReturnValue"
    )
    if "0" not in salida:
        print(f"  [X] no se pudo lanzar: {salida.strip()[:200]}")
        return False

    for segundo in range(ESPERA):
        if _responde():
            print(f"  [OK] el servidor respondio en {segundo + 1}s")
            break
        time.sleep(1)
    else:
        print(f"  [X] el servidor NO respondio en {ESPERA}s")
        for nombre in (".tmp/arranque.log", ".tmp/servidor.log"):
            reg = carpeta / nombre
            if reg.exists():
                print(f"  --- {nombre} ---")
                for linea in reg.read_text(encoding="utf-8",
                                           errors="replace").splitlines()[-25:]:
                    print(f"    {linea}")
        _matar()
        return False

    # La ventana aparece despues de que el puerto responda: hay que darle
    # unos segundos o se comprueba antes de que exista.
    for _ in range(20):
        ventana = _powershell(
            "(Get-Process Cuentero -EA SilentlyContinue).MainWindowTitle")
        if "Cuentero Infinito" in ventana:
            print("  [OK] ventana 'Cuentero Infinito' abierta")
            break
        time.sleep(1)
    else:
        print(f"  [X] no hay ventana (titulo: {ventana.strip()!r})")
        _matar()
        return False

    consolas = _powershell(
        "$t = Get-CimInstance Win32_Process; "
        "$app = ($t | Where-Object Name -eq 'Cuentero.exe').ProcessId; "
        "($t | Where-Object { $_.Name -in @('cmd.exe','conhost.exe') -and "
        "$app -contains $_.ParentProcessId } | Measure-Object).Count")
    if consolas.strip() != "0":
        print(f"  [X] la app abrio {consolas.strip()} consola(s)")
        _matar()
        return False
    print("  [OK] ninguna ventana de consola")

    _matar()
    return True


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    carpeta = Path(sys.argv[1]).resolve()
    print(f"[*] Probando {carpeta}")
    ok = probar(carpeta)
    print("[OK] arranque verificado" if ok else "[X] arranque ROTO")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
