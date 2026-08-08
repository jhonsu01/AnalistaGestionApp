"""[DET] Deja una copia autocontenida de Python dentro de la app.

Lo ejecuta el workflow al construir el MSI. Sin esto habria que pedirle al
usuario que instalase Python a mano, que es exactamente lo que no queremos: en
un equipo sin Python el instalador se quedaba parado dando instrucciones.

La distribucion "embeddable" de python.org son 11 MB, no toca el registro ni el
PATH, y trae `pythonw.exe`, que ejecuta sin abrir ninguna ventana de consola.

Uso:  python execution/empotrar_python.py [destino]
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

VERSION = "3.13.2"
EMBEBIBLE = f"https://www.python.org/ftp/python/{VERSION}/python-{VERSION}-embed-amd64.zip"
GET_PIP = "https://bootstrap.pypa.io/get-pip.py"

# Las versiones salen de requirements.txt, clavadas. Antes se instalaba "lo
# ultimo": el MSI de la 0.1.11 llevaba pandas 3.0.5 y starlette 1.3.1 mientras
# el desarrollo iba con 2.3.3 y 1.2.1, asi que nadie habia ejecutado nunca lo
# que recibia el usuario.
REQUISITOS = Path(__file__).resolve().parent.parent / "requirements.txt"
# Lo que la app necesita para arrancar. `turbovec` queda fuera a proposito:
# solo acelera la busqueda y el motor cae a numpy si no esta, asi que su
# ausencia no debe tumbar la construccion del instalador.
IMPORTS = ["starlette", "uvicorn", "anyio", "numpy"]


def empotrar(destino: Path) -> bool:
    destino.mkdir(parents=True, exist_ok=True)

    print(f"  [>] Python {VERSION} embebible...", end="", flush=True)
    try:
        with urllib.request.urlopen(EMBEBIBLE, timeout=300) as r:
            datos = r.read()
    except Exception as exc:
        print(f"  [X] {exc}")
        return False
    zipfile.ZipFile(io.BytesIO(datos)).extractall(destino)
    print(f"  {len(datos) / 1e6:.1f} MB")

    # El embebible ignora site-packages hasta que se descomenta `import site`.
    pth = next(destino.glob("python*._pth"), None)
    if pth is None:
        print("  [X] no encuentro el fichero ._pth")
        return False
    lineas = pth.read_text(encoding="utf-8").replace("#import site", "import site")
    if "site-packages" not in lineas:
        lineas = lineas.rstrip() + "\n" + str(Path("Lib") / "site-packages") + "\n"
    pth.write_text(lineas, encoding="utf-8")

    print("  [>] get-pip...", end="", flush=True)
    try:
        with urllib.request.urlopen(GET_PIP, timeout=300) as r:
            (destino / "get-pip.py").write_bytes(r.read())
    except Exception as exc:
        print(f"  [X] {exc}")
        return False
    print("  ok")

    python = destino / "python.exe"
    entorno = {**os.environ, "PYTHONNOUSERSITE": "1"}

    print("  [>] instalando pip...", end="", flush=True)
    r = subprocess.run([str(python), str(destino / "get-pip.py"), "-q",
                        "--no-warn-script-location"], env=entorno)
    if r.returncode != 0:
        print("  [X] pip no se instalo")
        return False
    print("  ok")

    # Las dependencias viajan DENTRO del instalador. Si se instalasen en el
    # equipo del usuario, el primer arranque dependeria de que pip funcione y
    # de que haya red: justo los dos puntos donde fallaba antes.
    print("  [>] dependencias clavadas...", end="", flush=True)
    r = subprocess.run([str(python), "-m", "pip", "install", "-q",
                        "--no-warn-script-location", "-r", str(REQUISITOS)],
                       env=entorno)
    if r.returncode != 0:
        print("  [X] fallo la instalacion de dependencias")
        return False
    print("  ok")

    esperados = ["python.exe", "pythonw.exe"]
    faltan = [e for e in esperados if not (destino / e).exists()]
    if faltan:
        print(f"  [X] falta: {', '.join(faltan)}")
        return False

    # Comprobamos que el interprete empotrado importa TODO lo que necesita.
    # Este es el "verificar dentro del instalador": si algo falta, la release
    # ni se publica, en vez de descubrirlo el usuario en su equipo.
    prueba = "import " + ", ".join(IMPORTS) + "; print('ok')"
    r = subprocess.run([str(python), "-c", prueba], env=entorno,
                       capture_output=True, text=True)
    if r.returncode != 0 or "ok" not in r.stdout:
        print(f"  [X] el Python empotrado no puede importar: {r.stderr.strip()[:300]}")
        return False
    print(f"  [OK] importa: {', '.join(IMPORTS)}")

    # Y que sean EXACTAMENTE las versiones clavadas, no las que pip prefiera.
    r = subprocess.run([str(python), "-m", "pip", "freeze"], env=entorno,
                       capture_output=True, text=True)
    puestas = {l.split("==")[0].lower(): l.split("==")[1]
               for l in r.stdout.splitlines() if "==" in l}
    for linea in REQUISITOS.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "==" not in linea:
            continue
        nombre, esperada = linea.split("==")
        tiene = puestas.get(nombre.lower())
        if tiene != esperada:
            print(f"  [X] {nombre}: esperaba {esperada}, hay {tiene}")
            return False
        print(f"  [OK] {nombre} {esperada}")

    total = sum(f.stat().st_size for f in destino.rglob("*") if f.is_file())
    print(f"  [OK] Python empotrado y listo: {total / 1e6:.0f} MB")
    return True


def main() -> int:
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    destino = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("dist/app/python")
    return 0 if empotrar(destino) else 1


if __name__ == "__main__":
    raise SystemExit(main())
