"""API del Analista de Gestion + servidor de la app web.

Starlette puro, sin FastAPI: una capa menos y menos dependencias que empotrar
en el instalador.

El mismo servidor atiende al navegador del PC y al cliente Android por WiFi,
por eso escucha en 0.0.0.0.
"""
from __future__ import annotations

import json
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
    return JSONResponse(
        {
            "corpus": rag.estado(),
            "modelo": llm.diagnostico(),
            "historial": history.estadisticas(),
            "ajustes": config.leer_ajustes(),
            "version": (config.ROOT / "VERSION").read_text(encoding="utf-8").strip()
            if (config.ROOT / "VERSION").exists() else "0.0.0",
        }
    )


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
            )
        )
        if not fragmentos:
            yield _sse("error", {"detalle": "Sin resultados para esos filtros."})
            return

        fuentes = [
            {
                "entidad": f["entidad"], "sector": f["sector"],
                "archivo": f["archivo"], "seccion": f["seccion"],
                "similitud": f["similitud"], "tipo": f["tipo"],
            }
            for f in fragmentos
        ]
        yield _sse("fuentes", {"lista": fuentes})
        yield _sse("fase", {"t": "Redactando la respuesta..."})

        partes: list[str] = []
        cola: list[str] = []
        fin = anyio.Event()

        def producir() -> None:
            try:
                for trozo in llm.responder_stream(
                    pregunta, fragmentos, int(aj.get("chars_por_fragmento", config.CHARS_POR_FRAGMENTO))
                ):
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
        Route("/api/catalogo", catalogo),
        Route("/api/consultar", consultar),
        Route("/api/historial", historial),
        Route("/api/consulta/{cid:int}", ver_consulta),
        Route("/api/consulta/{cid:int}/markdown", exportar),
        Route("/api/consulta/{cid:int}/favorita", favorita, methods=["POST"]),
        Route("/api/consulta/{cid:int}/fijada", fijada, methods=["POST"]),
        Route("/api/consulta/{cid:int}", borrar, methods=["DELETE"]),
        Route("/", raiz),
    ]
    if WEB.exists():
        rutas.append(Mount("/", app=StaticFiles(directory=str(WEB), html=True)))
    return Starlette(routes=rutas)


app = crear_app()
