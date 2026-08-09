"""API del Analista de Gestion + servidor de la app web.

Starlette puro, sin FastAPI: una capa menos y menos dependencias que empotrar
en el instalador.

El mismo servidor atiende al navegador del PC y al cliente Android por WiFi,
por eso escucha en 0.0.0.0.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import AsyncIterator

import anyio
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
    StreamingResponse,
)
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import config, history, llm, rag

# La voz es opcional: depende de onnxruntime y de que haya modelos descargados.
# Si falta, la app funciona igual, solo sin lectura en voz alta.
try:
    from . import tts
    VOZ_DISPONIBLE = True
except Exception:  # noqa: BLE001
    tts = None
    VOZ_DISPONIBLE = False

WEB = config.ROOT / "web"


def _sse(evento: str, datos: dict) -> str:
    return f"event: {evento}\ndata: {json.dumps(datos, ensure_ascii=False)}\n\n"


def _error(mensaje: str, codigo: int = 400) -> JSONResponse:
    return JSONResponse({"detail": mensaje}, status_code=codigo)


# --------------------------------------------------------------------------
# Estado
# --------------------------------------------------------------------------
async def vivo(request: Request) -> JSONResponse:
    """Responde sin tocar nada pesado: sirve para que el movil detecte el PC."""
    return JSONResponse({"ok": True, "app": "AnalistaGestion"})


async def estado(request: Request) -> JSONResponse:
    voz = {"disponible": False, "voces": [], "motivo": "sin onnxruntime"}
    if VOZ_DISPONIBLE:
        try:
            voz = {"disponible": True, **tts.estado_voz()}
        except Exception as exc:  # noqa: BLE001
            voz = {"disponible": False, "motivo": str(exc)[:120]}

    return JSONResponse(
        {
            "corpus": rag.estado(),
            "modelo": llm.diagnostico(),
            "historial": history.estadisticas(),
            "ajustes": config.leer_ajustes(),
            "voz": voz,
            "version": (config.ROOT / "VERSION").read_text(encoding="utf-8").strip()
            if (config.ROOT / "VERSION").exists() else "0.0.0",
        }
    )


async def listar_modelos(request: Request) -> JSONResponse:
    """Modelos cargados en el servidor, para elegirlos sin escribirlos a mano."""
    disponibles = llm.modelos()
    # Los de embeddings se distinguen por el nombre; no es infalible pero
    # acierta con los habituales (nomic, bge, e5, minilm, gte...).
    patron = ("embed", "bge", "e5-", "minilm", "gte-", "nomic")
    return JSONResponse(
        {
            "todos": disponibles,
            "chat": [m for m in disponibles if not any(p in m.lower() for p in patron)],
            "embeddings": [m for m in disponibles if any(p in m.lower() for p in patron)],
        }
    )


async def probar(request: Request) -> JSONResponse:
    """Prueba real de extremo a extremo antes de guardar los ajustes."""
    return JSONResponse(await anyio.to_thread.run_sync(llm.probar_conexion))


async def voces(request: Request) -> JSONResponse:
    """Voces instaladas y catalogo de las que se pueden descargar.

    Las voces NO viajan en el instalador (pesan entre 28 y 114 MB cada una),
    pero el usuario no deberia tener que abrir una consola para conseguirlas:
    se descargan desde Ajustes con un clic.
    """
    if not VOZ_DISPONIBLE:
        return JSONResponse(
            {"disponible": False, "voces": [], "catalogo": [],
             "detalle": "Falta onnxruntime en este equipo."}
        )

    datos = {"disponible": True, "catalogo": []}
    try:
        datos.update(tts.estado_voz())
    except Exception as exc:  # noqa: BLE001
        datos["detalle"] = str(exc)[:150]

    # `estado_voz()` devuelve el CATALOGO de voces que la app sabe usar, cada
    # una con su campo `instalada`. Solo las instaladas pueden sonar: si se
    # ofrecen todas, el usuario elige una, pulsa Escuchar y no pasa nada.
    todas = datos.get("voces", [])
    datos["voces"] = [v for v in todas if v.get("instalada")]
    datos["conocidas"] = len(todas)

    # Que se puede descargar y cuanto pesa
    try:
        import instalar_voz

        archivos = (
            [p.name for p in config.VOCES_DIR.glob("*.onnx")]
            if config.VOCES_DIR.exists() else []
        )
        datos["catalogo"] = [
            {
                "clave": clave,
                "descripcion": desc,
                "mb": mb,
                # El nombre del fichero descargado es el ultimo tramo de la ruta
                "instalada": Path(ruta).name in archivos,
            }
            for clave, (ruta, mb, desc) in instalar_voz.CATALOGO.items()
        ]
    except Exception as exc:  # noqa: BLE001
        datos["catalogo_error"] = str(exc)[:150]

    return JSONResponse(datos)


async def descargar_voz(request: Request) -> Response:
    """Descarga una voz del catalogo, informando del avance en directo.

    Va por SSE porque una voz son decenas de MB: con una peticion normal el
    usuario se queda mirando un boton bloqueado sin saber si avanza.
    """
    clave = (request.query_params.get("voz") or "").strip()
    if not clave:
        return _error("Falta indicar la voz")

    async def flujo() -> AsyncIterator[str]:
        try:
            import instalar_voz
        except Exception as exc:  # noqa: BLE001
            yield _sse("error", {"detalle": f"No encuentro el instalador: {exc}"})
            return

        if clave not in instalar_voz.CATALOGO:
            yield _sse("error", {"detalle": f"La voz '{clave}' no esta en el catalogo."})
            return

        _ruta, mb, desc = instalar_voz.CATALOGO[clave]
        yield _sse("fase", {"t": f"Descargando {desc} ({mb} MB)…"})

        ok = await anyio.to_thread.run_sync(lambda: instalar_voz.instalar(clave))
        if not ok:
            yield _sse("error", {"detalle": "La descarga falló. Revisa tu conexión."})
            return

        # La voz acaba de aparecer en disco: hay que tirar la cache de modelos
        # o el proceso seguira creyendo que no existe y el audio fallara hasta
        # reiniciar la aplicacion.
        if VOZ_DISPONIBLE:
            tts.olvidar_cache()

        # Releer el catalogo de voces ya instaladas
        try:
            estado_voz = tts.estado_voz() if VOZ_DISPONIBLE else {}
        except Exception:  # noqa: BLE001
            estado_voz = {}
        instaladas = [v for v in estado_voz.get("voces", []) if v.get("instalada")]
        yield _sse("fin", {"voces": instaladas})

    return StreamingResponse(
        flujo(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# `audio` esta definida mas abajo, en la seccion de lectura en voz alta.


async def guardar_ajustes(request: Request) -> JSONResponse:
    try:
        datos = await request.json()
    except Exception:
        return _error("Cuerpo JSON invalido")
    nuevos = config.guardar_ajustes(datos)
    # Cambiar de corpus obliga a releer el indice.
    rag.cargar(forzar=True)
    return JSONResponse({"ok": True, "ajustes": nuevos, "corpus": rag.estado()})


async def catalogo(request: Request) -> JSONResponse:
    """Sectores y entidades disponibles, para los filtros de la interfaz."""
    sector = request.query_params.get("sector", "")
    return JSONResponse(
        {"sectores": rag.sectores(), "entidades": rag.entidades(sector)}
    )


# --------------------------------------------------------------------------
# Consulta
# --------------------------------------------------------------------------
async def consultar(request: Request) -> Response:
    """Busca, responde en streaming y guarda la consulta al terminar."""
    pregunta = (request.query_params.get("q") or "").strip()
    if not pregunta:
        return _error("Falta la pregunta")

    sector = request.query_params.get("sector", "").strip()
    entidad = request.query_params.get("entidad", "").strip()
    aj = config.leer_ajustes()
    try:
        k = max(1, min(60, int(request.query_params.get("k", aj.get("fragmentos", 20)))))
    except ValueError:
        k = config.FRAGMENTOS

    async def flujo() -> AsyncIterator[str]:
        if not rag.cargar():
            yield _sse("error", {"detalle": "No hay corpus configurado. Indica la carpeta en Ajustes."})
            return

        yield _sse("fase", {"t": "Interpretando la pregunta..."})
        vector = await anyio.to_thread.run_sync(llm.embeder, pregunta)
        if vector is None:
            yield _sse("error", {"detalle": "El servidor de modelos no respondio. Revisa la URL en Ajustes."})
            return

        yield _sse("fase", {"t": "Buscando en los informes..."})
        fragmentos = await anyio.to_thread.run_sync(
            lambda: rag.buscar(
                vector, k, sector, entidad,
                int(aj.get("max_por_documento", config.MAX_POR_DOCUMENTO)),
                pregunta,
            )
        )
        if not fragmentos:
            yield _sse("error", {"detalle": "Sin resultados para esos filtros."})
            return

        # Si el material no cabe en la ventana del modelo, se comprime ANTES de
        # enviarlo. Descubrirlo por un error significaria perder la consulta.
        chars = int(aj.get("chars_por_fragmento", config.CHARS_POR_FRAGMENTO))
        tope = int(aj.get("contexto_maximo", config.CONTEXTO_MAXIMO))
        fragmentos, tokens, nota = llm.comprimir(fragmentos, chars, tope)
        if nota:
            yield _sse("aviso", {"t": nota})

        fuentes = [
            {
                "entidad": f["entidad"], "sector": f["sector"],
                "archivo": f["archivo"], "seccion": f["seccion"],
                "similitud": f["similitud"], "tipo": f["tipo"],
            }
            for f in fragmentos
        ]
        yield _sse("fuentes", {"lista": fuentes, "tokens": tokens})
        yield _sse("fase", {"t": "Redactando la respuesta..."})

        partes: list[str] = []
        cola: list[str] = []
        fin = anyio.Event()

        def producir() -> None:
            try:
                for trozo in llm.responder_stream(pregunta, fragmentos, chars):
                    cola.append(trozo)
            except Exception as exc:  # noqa: BLE001
                cola.append(f"\n\n[Error del modelo: {exc}]")
            finally:
                fin.set()

        async with anyio.create_task_group() as tg:
            tg.start_soon(anyio.to_thread.run_sync, producir)
            while True:
                while cola:
                    trozo = cola.pop(0)
                    partes.append(trozo)
                    yield _sse("texto", {"t": trozo})
                if fin.is_set() and not cola:
                    break
                await anyio.sleep(0.05)

        respuesta = "".join(partes).strip()
        consulta_id = await anyio.to_thread.run_sync(
            lambda: history.guardar(pregunta, respuesta, fuentes, sector, entidad)
        )
        yield _sse("fin", {"id": consulta_id})

    return StreamingResponse(
        flujo(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --------------------------------------------------------------------------
# Historial
# --------------------------------------------------------------------------
async def historial(request: Request) -> JSONResponse:
    p = request.query_params
    texto = (p.get("buscar") or "").strip()
    if texto:
        return JSONResponse({"consultas": history.buscar(texto)})
    try:
        limite = max(1, min(200, int(p.get("limite", 40))))
        desde = max(0, int(p.get("desde", 0)))
    except ValueError:
        limite, desde = 40, 0
    return JSONResponse(
        {
            "consultas": history.listar(
                limite, desde,
                p.get("favoritas") == "1", p.get("fijadas") == "1",
            )
        }
    )


async def ver_consulta(request: Request) -> JSONResponse:
    datos = history.obtener(int(request.path_params["cid"]))
    return JSONResponse(datos) if datos else _error("No existe", 404)


async def exportar(request: Request) -> Response:
    texto = history.exportar_markdown(int(request.path_params["cid"]))
    if not texto:
        return _error("No existe", 404)
    return PlainTextResponse(
        texto,
        headers={
            "Content-Disposition": f'attachment; filename="consulta-{request.path_params["cid"]}.md"'
        },
    )


async def favorita(request: Request) -> JSONResponse:
    return JSONResponse({"favorita": history.alternar_favorita(int(request.path_params["cid"]))})


async def fijada(request: Request) -> JSONResponse:
    return JSONResponse({"fijada": history.alternar_fijada(int(request.path_params["cid"]))})


async def borrar(request: Request) -> JSONResponse:
    history.borrar(int(request.path_params["cid"]))
    return JSONResponse({"ok": True})


# --------------------------------------------------------------------------
# Lectura en voz alta
# --------------------------------------------------------------------------
# `voces` y `descargar_voz` estan definidas mas arriba, junto al resto de la
# configuracion. Aqui solo queda la sintesis.


async def audio(request: Request) -> Response:
    """Devuelve un WAV con la respuesta leida en voz alta."""
    if not VOZ_DISPONIBLE:
        return _error("La síntesis de voz no está disponible", 503)

    datos = history.obtener(int(request.path_params["cid"]))
    if not datos:
        return _error("No existe", 404)

    aj = config.leer_ajustes()
    voz = request.query_params.get("voz") or aj.get("voz_activa") or ""
    if not voz:
        return _error("No hay ninguna voz seleccionada. Elige una en Ajustes.", 400)

    try:
        wav = await anyio.to_thread.run_sync(
            lambda: tts.sintetizar(datos["respuesta"], voz)
        )
    except Exception as exc:  # noqa: BLE001
        return _error(f"No se pudo sintetizar: {str(exc)[:150]}", 500)

    if not wav:
        return _error("La síntesis no devolvió audio", 500)

    return Response(
        wav,
        media_type="audio/wav",
        headers={"Cache-Control": "no-store"},
    )


# --------------------------------------------------------------------------
# Web
# --------------------------------------------------------------------------
async def raiz(request: Request) -> Response:
    indice = WEB / "index.html"
    if not indice.exists():
        return PlainTextResponse("Falta web/index.html", status_code=500)
    return FileResponse(indice)


def crear_app() -> Starlette:
    history.iniciar()
    rutas = [
        Route("/api/vivo", vivo),
        Route("/api/estado", estado),
        Route("/api/ajustes", guardar_ajustes, methods=["POST"]),
        Route("/api/modelos", listar_modelos),
        Route("/api/probar", probar, methods=["POST"]),
        Route("/api/voces", voces),
        Route("/api/voces/descargar", descargar_voz),
        Route("/api/catalogo", catalogo),
        Route("/api/consultar", consultar),
        Route("/api/historial", historial),
        Route("/api/consulta/{cid:int}", ver_consulta),
        Route("/api/consulta/{cid:int}/markdown", exportar),
        Route("/api/consulta/{cid:int}/audio", audio),
        Route("/api/consulta/{cid:int}/favorita", favorita, methods=["POST"]),
        Route("/api/consulta/{cid:int}/fijada", fijada, methods=["POST"]),
        Route("/api/consulta/{cid:int}", borrar, methods=["DELETE"]),
        Route("/", raiz),
    ]
    if WEB.exists():
        rutas.append(Mount("/", app=StaticFiles(directory=str(WEB), html=True)))
    return Starlette(routes=rutas)


app = crear_app()
