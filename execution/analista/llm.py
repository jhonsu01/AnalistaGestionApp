"""[HYB] Cliente para servidores de modelos compatibles con OpenAI.

Funciona con LM Studio, Ollama (modo /v1), llama-server y cualquier otro que
exponga /v1/chat/completions y /v1/embeddings. La app no incrusta ningun
modelo: se conecta al que el usuario ya tenga corriendo.
"""
from __future__ import annotations

import json
import re
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


# Se le pide al servidor que NO razone, y se pide SIEMPRE, no como remedio.
#
# Medido contra gemma-4-26b con 10 fragmentos (prompt de 5.184 tokens):
#
#   sin el parametro      304 s   2.524 tokens de razonamiento   1.210 chars
#   reasoning_effort low   62 s           0 tokens               1.944 chars
#
# Es cinco veces mas rapido Y responde mas. El razonamiento no aportaba nada
# aqui: el trabajo de analisis ya lo hizo la busqueda al elegir los fragmentos,
# al modelo solo le queda leerlos y redactar. Cuando el razonamiento se
# desbordaba, ademas, consumia todo el cupo y la respuesta llegaba VACIA.
SIN_RAZONAMIENTO = {"reasoning_effort": "low"}


class ContextoExcedido(Exception):
    """El prompt no cabe en la ventana con la que se cargo el modelo.

    Se detecta leyendo el stream, no por el codigo HTTP: LM Studio responde
    200 y mete el error DENTRO del SSE, en una linea `event: error`. Un lector
    que solo mire los `data:` con `choices` no ve nada y cree que el modelo se
    quedo mudo.
    """

    def __init__(self, n_ctx: int, n_prompt: int):
        self.n_ctx = n_ctx
        self.n_prompt = n_prompt
        super().__init__(f"prompt de {n_prompt} tokens en una ventana de {n_ctx}")


_RE_CONTEXTO = re.compile(
    r"exceeds the available context size|exceed_context_size", re.IGNORECASE
)


def _mirar_error(datos: str) -> None:
    """Levanta la excepcion adecuada si esta linea del stream trae un error."""
    try:
        cuerpo = json.loads(datos)
    except json.JSONDecodeError:
        return
    error = cuerpo.get("error")
    if not error:
        return
    mensaje = error.get("message", "") if isinstance(error, dict) else str(error)
    if _RE_CONTEXTO.search(mensaje):
        n_ctx = int((re.search(r'"n_ctx":\s*(\d+)', mensaje) or [0, 0])[1] or 0)
        n_prompt = int(
            (re.search(r'"n_prompt_tokens":\s*(\d+)', mensaje) or [0, 0])[1] or 0
        )
        raise ContextoExcedido(n_ctx, n_prompt)
    raise RuntimeError(mensaje[:300] or "el servidor devolvió un error sin detalle")


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


def ventana_cargada(modelo: str) -> tuple[int, int]:
    """(tokens con los que se cargo el modelo, tope que admite). (0, 0) si no se sabe.

    Es un endpoint propio de LM Studio, fuera de la API OpenAI. Merece la pena
    consultarlo porque un modelo que admite 262.144 tokens puede estar cargado
    con 8.192, y esa diferencia decide si el corpus cabe o no. El servidor que
    no lo tenga devuelve 404 y aqui no pasa nada.
    """
    aj = _ajustes()
    base = (aj.get("llm_url") or config.LLM_URL_DEFECTO).rstrip("/")
    raiz = base[:-3].rstrip("/") if base.endswith("/v1") else base
    try:
        req = urllib.request.Request(
            f"{raiz}/api/v0/models", headers=_cabeceras(aj.get("llm_api_key", ""))
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            datos = json.loads(r.read().decode("utf-8"))
    except Exception:
        return 0, 0
    for m in datos.get("data", []):
        if m.get("id") == modelo:
            return (int(m.get("loaded_context_length") or 0),
                    int(m.get("max_context_length") or 0))
    return 0, 0


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


def comprimir(
    fragmentos: list[dict], chars: int, tope_tokens: int
) -> tuple[list[dict], int, str]:
    """Ajusta los fragmentos para que quepan en la ventana de contexto.

    Antes de enviar nada se estima cuanto ocupa el material. Si se pasa del
    tope, se comprime en dos escalones en vez de fallar:

      1. RECORTAR cada fragmento (menos texto por documento, mismos documentos)
      2. si aun no cabe, DESCARTAR los fragmentos menos parecidos

    Se recorta antes de descartar porque conservar muchas fuentes distintas da
    mejores respuestas que conservar pocas muy largas.
    """
    if not fragmentos:
        return fragmentos, 0, ""

    def coste(lista: list[dict], corte: int) -> int:
        total = sum(min(len(f.get("texto") or ""), corte) + 120 for f in lista)
        return total // config.CHARS_POR_TOKEN

    actual = coste(fragmentos, chars)
    if actual <= tope_tokens:
        return fragmentos, actual, ""

    # 1) Recortar el texto de cada fragmento
    for nuevo_corte in (3000, 2200, 1500, 1000, 700):
        if nuevo_corte >= chars:
            continue
        if coste(fragmentos, nuevo_corte) <= tope_tokens:
            return (
                fragmentos,
                coste(fragmentos, nuevo_corte),
                f"contexto comprimido: {chars} → {nuevo_corte} caracteres por fragmento",
            )
        chars_final = nuevo_corte

    # 2) Descartar los menos relevantes, manteniendo el recorte minimo
    corte = 700
    recortados = list(fragmentos)
    while len(recortados) > 3 and coste(recortados, corte) > tope_tokens:
        recortados.pop()  # vienen ordenados por similitud descendente
    return (
        recortados,
        coste(recortados, corte),
        f"contexto comprimido: {len(fragmentos)} → {len(recortados)} fragmentos "
        f"y {corte} caracteres cada uno",
    )


def responder_stream(
    pregunta: str, fragmentos: list[dict], chars: int = 4000
) -> Iterator[str]:
    """Emite la respuesta por trozos, para que se vea escribirse en pantalla.

    Dos trampas conocidas, las dos silenciosas:

    - El razonamiento del modelo se descuenta del MISMO `max_tokens` que la
      respuesta. Al agotarse, la API devuelve contenido VACIO con
      finish_reason "length" y sin error. Por eso se pide no razonar.
    - Si el prompt no cabe en la ventana con la que se cargo el modelo, LM
      Studio responde HTTP 200 y mete el error dentro del stream. Aqui se lee
      y se reintenta recortando, en vez de devolver silencio.
    """
    aj = _ajustes()
    url = (aj.get("llm_url") or config.LLM_URL_DEFECTO).rstrip("/")
    modelo = aj.get("llm_modelo") or ""
    if not modelo:
        disponibles = modelos()
        modelo = disponibles[0] if disponibles else "local-model"

    prompt = _prompt(pregunta, fragmentos, chars)
    cupo = int(aj.get("max_tokens") or config.MAX_TOKENS)

    def intentar(
        tope: int, sin_razonar: bool = True, texto: str = ""
    ) -> tuple[list[str], list[str]]:
        """Lanza una peticion y devuelve (respuesta, razonamiento).

        Se recogen los dos por separado porque los modelos con razonamiento lo
        emiten en `reasoning_content`, un campo distinto de `content`. Leer solo
        `content` hacia que una respuesta larguisima pareciera silencio.
        """
        peticion = {
            "model": modelo,
            "messages": [
                {"role": "system", "content": SISTEMA},
                {"role": "user", "content": texto or prompt},
            ],
            "temperature": config.TEMPERATURA,
            "max_tokens": tope,
            "stream": True,
        }
        if sin_razonar:
            peticion.update(SIN_RAZONAMIENTO)

        req = urllib.request.Request(
            f"{url}/chat/completions",
            data=json.dumps(peticion).encode("utf-8"),
            headers=_cabeceras(aj.get("llm_api_key", "")),
            method="POST",
        )
        piezas: list[str] = []
        razonamiento: list[str] = []
        with urllib.request.urlopen(req, timeout=900) as respuesta:
            for linea_bytes in respuesta:
                linea = linea_bytes.decode("utf-8", errors="replace").strip()
                if not linea.startswith("data:"):
                    continue
                datos = linea[5:].strip()
                if datos == "[DONE]":
                    break
                _mirar_error(datos)   # el error viaja DENTRO del stream, con HTTP 200
                try:
                    trozo = json.loads(datos)
                except json.JSONDecodeError:
                    continue
                delta = ((trozo.get("choices") or [{}])[0].get("delta")) or {}
                if delta.get("content"):
                    piezas.append(delta["content"])
                for campo in ("reasoning_content", "reasoning"):
                    if delta.get(campo):
                        razonamiento.append(delta[campo])
        return piezas, razonamiento

    aviso_recorte = ""
    try:
        try:
            piezas, razonamiento = intentar(cupo)
        except urllib.error.HTTPError as exc:
            # Un servidor que no conozca `reasoning_effort` puede rechazar la
            # peticion entera. Se reintenta tal cual, sin ese parametro.
            if exc.code != 400:
                raise
            piezas, razonamiento = intentar(cupo, sin_razonar=False)
        except RuntimeError:
            # Fallo transitorio del motor (visto: "produced output that does not
            # match the expected peg-gemma4 format"). No depende de la pregunta
            # ni del contexto: la misma peticion suele salir bien a la segunda.
            piezas, razonamiento = intentar(cupo)
    except ContextoExcedido as exc:
        # El modelo esta cargado con una ventana mas pequena de lo que creiamos.
        # No hace falta molestar al usuario: se recorta a lo que de verdad cabe.
        # Se reserva una parte de la ventana para la respuesta, porque prompt y
        # respuesta comparten la misma ventana.
        ventana = exc.n_ctx or 8192
        salida = max(512, min(cupo, ventana // 4))
        hueco = max(1000, ventana - salida - 200)   # 200 de margen para plantilla
        antes = len(fragmentos)
        fragmentos, _tk, _nota = comprimir(fragmentos, chars, hueco)
        corte = min(chars, 1500)
        recortado = _prompt(pregunta, fragmentos, corte)

        # El separador de miles se arma aparte: aplicar un replace de comas al
        # mensaje entero se llevaba por delante las comas de la propia frase.
        miles = f"{ventana:,}".replace(",", ".")
        perdidos = antes - len(fragmentos)
        que_paso = (
            f"se descartaron {perdidos} de {antes} fragmentos"
            if perdidos else
            f"se acortó cada uno de los {antes} fragmentos a {corte} caracteres"
        )
        aviso_recorte = (
            f"\n\n---\n\n*Nota: tu modelo está cargado con una ventana de {miles} "
            f"tokens y el contexto no cabía, así que {que_paso}. Para aprovechar "
            f"el corpus completo, sube el «Context Length» del modelo en LM Studio.*"
        )
        piezas, razonamiento = intentar(salida, texto=recortado)

    # Si aun asi no escribio nada, casi siempre es que agoto el cupo. Se
    # reintenta con el doble en vez de pedirle al usuario que ajuste numeros.
    if not piezas and not aviso_recorte:
        piezas, razonamiento = intentar(cupo * 2, sin_razonar=False)

    if piezas:
        for p in piezas:
            yield p
        if aviso_recorte:
            yield aviso_recorte
        return

    # Ultimo recurso: el modelo razono pero nunca paso a redactar. Ese
    # razonamiento suele contener las cifras buscadas, asi que vale mas
    # entregarlo que devolver un mensaje de error vacio.
    borrador = "".join(razonamiento).strip()
    if borrador:
        yield (
            "El modelo agotó su presupuesto razonando y no llegó a redactar la "
            "respuesta final. Este es su razonamiento, que suele contener los "
            "datos buscados:\n\n---\n\n"
        )
        yield borrador
        return

    yield (
        f"El modelo no devolvió texto ni con {cupo * 2} tokens de cupo.\n\n"
        "**No significa que el corpus no tenga la información.**\n\n"
        "Qué probar:\n"
        "- Reducir **Fragmentos** a 8 o 10 en Filtros.\n"
        "- Usar un modelo sin modo de razonamiento.\n"
        "- Comprobar en Ajustes que el modelo cargado sea el correcto."
    )


def probar_conexion() -> dict:
    """Comprueba de verdad que el servidor responde y que el modelo contesta.

    No basta con listar modelos: LM Studio puede listar uno que luego falla al
    generar. Aqui se le pide una respuesta minima de verdad.
    """
    aj = _ajustes()
    disponibles = modelos()
    if not disponibles:
        return {
            "ok": False,
            "detalle": "El servidor no responde. Revisa la URL y, si tu servidor "
                       "exige clave, que la clave sea correcta.",
        }

    modelo = aj.get("llm_modelo") or disponibles[0]
    if modelo not in disponibles:
        return {
            "ok": False,
            "modelos": disponibles,
            "detalle": f"El modelo '{modelo}' no está cargado en el servidor.",
        }

    # 1) El modelo de chat responde
    def saludo(extra: dict) -> str:
        datos = _pedir(
            "/chat/completions",
            {
                "model": modelo,
                "messages": [{"role": "user", "content": "Responde solo: listo"}],
                "max_tokens": 2000,
                "temperature": 0,
                **extra,
            },
            timeout=120,
        )
        return (datos["choices"][0]["message"].get("content") or "").strip()

    try:
        try:
            # Sin esto el modelo razona incluso para saludar, y la prueba que
            # deberia tardar un segundo se va a varios minutos.
            contenido = saludo(SIN_RAZONAMIENTO)
        except urllib.error.HTTPError as exc:
            if exc.code != 400:
                raise
            contenido = saludo({})   # servidor que no conoce reasoning_effort
        if not contenido:
            return {
                "ok": False,
                "modelos": disponibles,
                "detalle": "El modelo respondió vacío: agota su cupo razonando. "
                           "Sube 'Tokens de respuesta'.",
            }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "modelos": disponibles,
                "detalle": f"El modelo no respondió: {str(exc)[:120]}"}

    # 2) El modelo de embeddings responde y con cuantas dimensiones
    vector = embeder("prueba")
    if vector is None:
        return {
            "ok": False,
            "modelos": disponibles,
            "detalle": f"El modelo de embeddings '{aj.get('embed_modelo')}' no responde. "
                       "Cárgalo en el servidor o corrige su nombre.",
        }

    # 3) Con que ventana se cargo el modelo. Es la causa mas comun de que una
    #    consulta grande falle, y no se ve por ningun lado hasta que falla.
    cargada, tope = ventana_cargada(modelo)
    nota = ""
    if cargada:
        miles = f"{cargada:,}".replace(",", ".")
        nota = f" Ventana cargada: {miles} tokens."
        # Con 12 fragmentos el prompt ronda los 6-8k; por debajo de 16k hay que
        # recortar a menudo y el usuario pierde contexto sin enterarse.
        if tope > cargada * 1.5 and cargada < 16000:
            nota += (f" El modelo admite hasta {f'{tope:,}'.replace(',', '.')}: "
                     f"súbele el «Context Length» en LM Studio para aprovechar "
                     f"el corpus completo.")

    return {
        "ok": True,
        "modelos": disponibles,
        "detalle": f"Todo correcto. Chat: {modelo}. "
                   f"Embeddings: {len(vector)} dimensiones.{nota}",
        "dimensiones": len(vector),
        "contexto_cargado": cargada,
        "contexto_maximo_modelo": tope,
    }
