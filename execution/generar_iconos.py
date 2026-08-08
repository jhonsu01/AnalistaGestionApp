"""[DET] Genera todos los iconos desde una unica definicion vectorial.

Uso:  python execution/generar_iconos.py

Produce:
  packaging/windows/analista.ico       16..256 px en un solo fichero
  packaging/wix/analista.ico           copia para el instalador
  android/app/src/main/res/mipmap-*/   ic_launcher, _round y _foreground
  packaging/android/playstore.png      512x512
  web/icono.png                        para la pestana del navegador

Se dibuja a 1024 px y se reescala con LANCZOS: asi se ve nitido tanto en un
mdpi de 48 px como en la ficha de la Play Store.

EL DISENO ES DELIBERADAMENTE SIMPLE: a 48 px un icono con detalle se
convierte en una mancha. Un documento con tres barras de datos se reconoce
incluso a ese tamano.
"""
from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]

# Paleta: azul institucional sobre noche, con acento cian para los datos.
NOCHE_1 = (15, 23, 42)
NOCHE_2 = (30, 41, 59)
PAPEL = (241, 245, 249)
PAPEL_SOMBRA = (203, 213, 225)
CIAN = (56, 189, 248)
CIAN_HONDO = (14, 165, 233)
TINTA = (51, 65, 85)

# Android: densidad -> lado en px del icono clasico
DENSIDADES = {
    "mdpi": 48,
    "hdpi": 72,
    "xhdpi": 96,
    "xxhdpi": 144,
    "xxxhdpi": 192,
}


def _lienzo(lado: int, fondo: bool = True):
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (lado, lado), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if fondo:
        # Degradado vertical, barato de dibujar linea a linea.
        for y in range(lado):
            t = y / max(lado - 1, 1)
            c = tuple(int(NOCHE_1[i] + (NOCHE_2[i] - NOCHE_1[i]) * t) for i in range(3))
            d.line([(0, y), (lado, y)], fill=c + (255,))
    return img, d


def _documento(d, lado: int, escala: float = 1.0):
    """Hoja vertical con la esquina doblada y tres barras de datos."""
    u = lado / 100.0  # unidad relativa: el dibujo escala solo
    cx = lado / 2

    ancho = 46 * u * escala
    alto = 58 * u * escala
    x0 = cx - ancho / 2
    y0 = (lado - alto) / 2
    x1 = x0 + ancho
    y1 = y0 + alto
    dobl = 13 * u * escala  # tamano de la esquina doblada

    # Sombra suave para despegar la hoja del fondo
    d.rounded_rectangle(
        [x0 + 2.2 * u, y0 + 2.6 * u, x1 + 2.2 * u, y1 + 2.6 * u],
        radius=3 * u, fill=(0, 0, 0, 70),
    )

    # Cuerpo de la hoja: poligono con la esquina superior derecha recortada
    d.polygon(
        [
            (x0, y0), (x1 - dobl, y0), (x1, y0 + dobl),
            (x1, y1), (x0, y1),
        ],
        fill=PAPEL + (255,),
    )
    # El doblez, en tono mas oscuro
    d.polygon(
        [(x1 - dobl, y0), (x1, y0 + dobl), (x1 - dobl, y0 + dobl)],
        fill=PAPEL_SOMBRA + (255,),
    )

    # Tres barras ascendentes: la idea de "analisis" en su forma minima
    base = y1 - 9 * u * escala
    ancho_barra = 8 * u * escala
    hueco = 4 * u * escala
    alturas = [14 * u * escala, 22 * u * escala, 30 * u * escala]
    total = 3 * ancho_barra + 2 * hueco
    bx = cx - total / 2

    for i, altura in enumerate(alturas):
        color = CIAN if i < 2 else CIAN_HONDO
        d.rounded_rectangle(
            [bx, base - altura, bx + ancho_barra, base],
            radius=1.6 * u, fill=color + (255,),
        )
        bx += ancho_barra + hueco

    # Dos renglones de texto insinuados en la cabecera
    for i, ancho_linea in enumerate((26, 18)):
        ly = y0 + (11 + i * 7) * u * escala
        d.rounded_rectangle(
            [cx - 17 * u * escala, ly,
             cx - 17 * u * escala + ancho_linea * u * escala, ly + 2.6 * u * escala],
            radius=1.3 * u, fill=TINTA + (170,),
        )


def _icono(lado: int, con_fondo: bool = True, escala: float = 1.0, redondo: bool = False):
    from PIL import Image, ImageDraw

    # Se dibuja grande y se reduce: los bordes salen suaves sin antialias manual.
    trabajo = 1024
    img, d = _lienzo(trabajo, fondo=con_fondo)
    _documento(d, trabajo, escala)

    if redondo and con_fondo:
        mascara = Image.new("L", (trabajo, trabajo), 0)
        ImageDraw.Draw(mascara).ellipse([0, 0, trabajo, trabajo], fill=255)
        img.putalpha(mascara)
    elif con_fondo:
        # Esquinas redondeadas suaves para el icono clasico
        mascara = Image.new("L", (trabajo, trabajo), 0)
        ImageDraw.Draw(mascara).rounded_rectangle(
            [0, 0, trabajo, trabajo], radius=int(trabajo * 0.22), fill=255
        )
        img.putalpha(mascara)

    return img.resize((lado, lado), Image.LANCZOS)


def main() -> int:
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        print("Falta Pillow.  pip install Pillow", file=sys.stderr)
        return 1

    from PIL import Image, ImageDraw

    # ---- Windows: un .ico con todas las resoluciones ----
    for destino in (RAIZ / "packaging" / "windows", RAIZ / "packaging" / "wix"):
        destino.mkdir(parents=True, exist_ok=True)
        base = _icono(256)
        base.save(
            destino / "analista.ico",
            sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
        )
        print(f"  {destino.name}/analista.ico")

    # ---- Android: mipmaps en las cinco densidades ----
    res = RAIZ / "android" / "app" / "src" / "main" / "res"
    empaq = RAIZ / "packaging" / "android"
    for densidad, lado in DENSIDADES.items():
        for carpeta in (res / f"mipmap-{densidad}", empaq / f"mipmap-{densidad}"):
            carpeta.mkdir(parents=True, exist_ok=True)

            _icono(lado).save(carpeta / "ic_launcher.png")
            _icono(lado, redondo=True).save(carpeta / "ic_launcher_round.png")

            # Icono adaptativo: Android recorta el 33% exterior, asi que el
            # dibujo va al 60% del lienzo para que no se coma las barras.
            adaptativo = 108 * lado // 48
            _icono(adaptativo, con_fondo=False, escala=0.60).save(
                carpeta / "ic_launcher_foreground.png"
            )

            fondo = Image.new("RGBA", (adaptativo, adaptativo), NOCHE_1 + (255,))
            dd = ImageDraw.Draw(fondo)
            for y in range(adaptativo):
                t = y / max(adaptativo - 1, 1)
                c = tuple(int(NOCHE_1[i] + (NOCHE_2[i] - NOCHE_1[i]) * t) for i in range(3))
                dd.line([(0, y), (adaptativo, y)], fill=c + (255,))
            fondo.save(carpeta / "ic_launcher_background.png")
        print(f"  mipmap-{densidad} ({lado}px)")

    # ---- Play Store y web ----
    empaq.mkdir(parents=True, exist_ok=True)
    _icono(512).save(empaq / "playstore.png")
    print("  playstore.png (512px)")

    web = RAIZ / "web"
    web.mkdir(parents=True, exist_ok=True)
    _icono(256).save(web / "icono.png")
    print("  web/icono.png")

    print("\nIconos generados.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
