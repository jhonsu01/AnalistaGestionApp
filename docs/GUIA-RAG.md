# Cómo construir tu propio RAG con este proyecto

Esta guía explica cómo pasar de un montón de documentos públicos a una
aplicación que responde preguntas sobre ellos citando la fuente. El caso de
referencia son los informes de empalme del gobierno colombiano —**8.820
documentos, 430.043 fragmentos, unos 354 millones de tokens**— pero el proceso
sirve para cualquier corpus: actas de un concejo municipal, contratos de una
entidad, la documentación interna de tu empresa.

Todo corre en tu equipo. No hay servicios de pago ni claves de API externas.

---

## Índice

1. [Qué vas a construir](#1-qué-vas-a-construir)
2. [Hardware recomendado](#2-hardware-recomendado)
3. [Qué modelos usar](#3-qué-modelos-usar)
4. [El paso a paso](#4-el-paso-a-paso)
5. [El formato de datos](#5-el-formato-de-datos-el-contrato)
6. [Adaptarlo a tu propio corpus](#6-adaptarlo-a-tu-propio-corpus)
7. [Errores que cuestan horas](#7-errores-que-cuestan-horas)

---

## 1. Qué vas a construir

```
Documentos          Texto              Fragmentos         Vectores
(PDF, XLSX, ...) -> (Markdown)      -> (corpus.jsonl)  -> (embeddings.npy)
                                                              |
                                          Tu pregunta --------+
                                                              v
                                                    los 12 fragmentos
                                                    más parecidos
                                                              |
                                                              v
                                                     modelo local -> respuesta
                                                                     con fuentes
```

La idea de fondo: **el modelo no memoriza tus documentos, los lee en el
momento**. Cuando preguntas, se buscan los fragmentos más relacionados por
significado y se le entregan como contexto. Por eso responde con datos
verificables y por eso puedes cambiar de modelo sin rehacer nada.

Esto es distinto de *entrenar* un modelo. Entrenar es caro, lento y hay que
repetirlo cada vez que cambian los datos. Un RAG se actualiza añadiendo
documentos.

---

## 2. Hardware recomendado

Lo que de verdad manda es la **memoria de vídeo (VRAM)**, porque determina qué
modelo puedes cargar y con cuánta ventana de contexto.

| Nivel | Hardware | Qué puedes hacer |
| --- | --- | --- |
| **Mínimo** | 16 GB de RAM, sin GPU dedicada | Corpus pequeño (< 50.000 fragmentos). Modelos de 2-4B en CPU. Las respuestas tardan minutos. |
| **Recomendado** | 16 GB RAM + GPU de 8-12 GB VRAM | Corpus completo. Modelo de 2-8B con contexto amplio. Respuestas en segundos. |
| **Cómodo** | 32 GB RAM + GPU de 16-24 GB VRAM | Modelos de 26-30B con ventanas grandes, sin recortar material. |

**Disco:** el índice del corpus de referencia ocupa unos **1,6 GB** (1,3 GB de
vectores + 150 MB de metadatos), más el corpus de texto. Reserva 3-4 GB por
cada 400.000 fragmentos. Los documentos originales son aparte: en este caso,
12,4 GB.

**Un detalle que sorprende:** el índice se carga con `mmap`, así que **no
necesitas 1,3 GB de RAM libres** para tenerlo abierto — el sistema lee del
disco lo que hace falta. Un SSD ayuda bastante aquí.

### Modelo grande no es modelo mejor

Este es el hallazgo más útil de todo el proyecto, y va contra la intuición:

| Modelo | Ventana usable | Memoria | Resultado |
| --- | --- | --- | --- |
| `gemma-4-26b-a4b` | 8.192 (lo que cabía) | ~18 GB | Recortaba material en casi cada consulta |
| **`gemma-4-e2b`** | **131.072** | **mucho menos** | **Mejor respuesta, sin recortes** |

Un modelo pequeño al que le **caben los 12 fragmentos enteros** responde mejor
que uno grande al que hay que recortarle el material para que entre. En un RAG,
el trabajo de análisis lo hizo la búsqueda al elegir los fragmentos; al modelo
le queda leerlos y redactar, que es una tarea mucho más fácil de lo que parece.

**Recomendación: empieza por el modelo pequeño con contexto amplio.** Sube de
tamaño solo si notas que la redacción se queda corta.

---

## 3. Qué modelos usar

Hacen falta **dos**, y cumplen funciones distintas:

| | Para qué | Sugerencia | Tamaño |
| --- | --- | --- | --- |
| **Embeddings** | Convertir texto en vectores para buscar | `nomic-embed-text-v1.5` | ~270 MB |
| **Chat** | Leer los fragmentos y redactar | `gemma-4-e2b` | ~2 GB |

Sirve cualquier servidor con API compatible con OpenAI:
[LM Studio](https://lmstudio.ai) (el más sencillo, con interfaz),
[Ollama](https://ollama.com), o `llama-server` de llama.cpp.

### Ajustes que importan en LM Studio

- **Context Length**: súbelo. Es *el* parámetro que decide la calidad. Con
  8.192 se recorta material en cada consulta; con 32.768 o más, no.
- **Idle TTL**: si lo dejas en 60 minutos, el modelo se descarga solo y al
  recargarse vuelve a su configuración por defecto. Amplíalo o desactívalo.
- Guarda la configuración **como predeterminada del modelo**, o cada recarga
  automática ignorará lo que ajustaste a mano.

> **Modelos que "razonan"**: si el tuyo lo hace, pídele que no lo haga
> (`reasoning_effort: "low"`). Medido aquí: **304 s con razonamiento frente a
> 62 s sin él, y la respuesta sin razonar era más larga y mejor**. El
> razonamiento consume del mismo presupuesto que la respuesta, y al agotarlo
> la API devuelve texto vacío sin ningún error. La aplicación ya lo pide sola.

---

## 4. El paso a paso

### Paso 1 — Reunir los documentos

Descárgalos como pueda ser: portal abierto, API, o a mano. Guarda **de dónde
salió cada uno**: esa referencia es lo que después permite verificar una cifra.

Si el portal es una aplicación web moderna y no encuentras los enlaces en el
HTML, abre las herramientas de desarrollo del navegador (F12), pestaña **Red**,
y mira qué peticiones hace al cargar. Ahí suele estar la API real.

### Paso 2 — Convertir a texto

De PDF, Excel, Word o PowerPoint a Markdown. Herramientas locales que funcionan
bien: [`firecrawl/anydoc`](https://github.com/firecrawl/anydoc) y
[`pdf-inspector`](https://github.com/firecrawl/pdf-inspector).

**Los PDF escaneados no tienen texto**: son imágenes. Necesitan OCR
([Tesseract](https://github.com/tesseract-ocr/tesseract) con el paquete de
español). En este proyecto fueron 698 de 8.820 documentos — un 8% que se habría
perdido en silencio.

### Paso 3 — Trocear en fragmentos

Un documento entero no cabe en el contexto del modelo, así que se parte.

- **Tamaño**: 2.000-4.000 caracteres. Suficiente para que un fragmento tenga
  sentido por sí solo.
- **Corta por secciones**, no por número de caracteres, cuando el documento
  tenga títulos. Un fragmento que empieza a mitad de una frase confunde.
- **Solapa** unos 200 caracteres entre fragmentos contiguos, para no partir en
  dos una cifra y su explicación.
- **Conserva las tablas juntas.** Es donde están las cifras.

Cada fragmento va como una línea JSON en `corpus.jsonl` (formato en la sección
5).

### Paso 4 — Generar el índice

Con tu servidor de embeddings corriendo:

```bash
python execution/construir_indice.py --corpus datos/corpus.jsonl --salida datos/indice
```

Empieza con `--limite 500` para comprobar que todo encaja antes de lanzar el
corpus completo. El proceso **se puede interrumpir**: guarda bloques parciales y
al relanzarlo sigue donde lo dejó.

Referencia de tiempo: 430.043 fragmentos tardaron **unas 3,5 horas** con lotes
de 32 contra LM Studio.

### Paso 5 — Apuntar la aplicación al índice

Abre el Analista → **Ajustes** → *Carpeta del corpus*, e indica la carpeta con
`embeddings.npy`. Copia también tu `corpus.jsonl` ahí (o en la carpeta padre):
de ahí sale el texto que se le entrega al modelo.

Pulsa **Probar conexión**: comprueba de verdad que el modelo de chat responde,
que el de embeddings responde, y con qué ventana de contexto está cargado.

---

## 5. El formato de datos (el contrato)

Si respetas estos tres archivos, la aplicación funciona con tus datos.

### `corpus.jsonl` — el texto

Una línea JSON por fragmento:

```json
{
  "chunk_id": "83b44a350035d199",
  "texto": "El presupuesto de funcionamiento para 2024 fue de $ 22.261 millones...",
  "entidad": "Autoridad Nacional de Acuicultura y Pesca",
  "sector": "Agropecuario, Pesquero y Desarrollo Rural",
  "archivo_origen": "informe_empalme_2024.pdf",
  "seccion": "3.2 Ejecución presupuestal",
  "titulo": "Informe de empalme AUNAP",
  "documento_id": "61ed3f0added7b5c",
  "tipo_documento": "informe",
  "fuente": "https://ejemplo.gov.co/informe.pdf"
}
```

| Campo | ¿Obligatorio? | Para qué sirve |
| --- | --- | --- |
| `chunk_id` | **Sí** | Une el vector con su texto. Único. |
| `texto` | **Sí** | Lo que lee el modelo. |
| `entidad` | Recomendado | Se muestra al citar y permite filtrar. |
| `sector` | Recomendado | Filtro de primer nivel. |
| `archivo_origen` | Recomendado | Para poder volver al documento. |
| `seccion` | Recomendado | Cita precisa dentro del documento. |
| `titulo`, `documento_id`, `tipo_documento`, `fuente` | Opcionales | Contexto adicional. |

### `metadatos.jsonl` — las etiquetas

Lo genera el script. Una línea por vector, **en el mismo orden que
`embeddings.npy`**. Lleva las etiquetas pero **no el texto**: duplicarlo
multiplicaría por varios GB un archivo que se carga entero en memoria.

### `embeddings.npy` — los vectores

Matriz `(N, dimensiones)` de `float32`, **normalizados** (norma 1). Al estarlo,
el producto escalar *es* la similitud del coseno, y buscar entre 430.000
vectores es una sola multiplicación de matrices.

> **La regla de oro:** la fila *i* de `embeddings.npy` debe corresponder a la
> línea *i* de `metadatos.jsonl`. Si se desalinean, cada respuesta citará el
> documento equivocado **y nada dará error**. El script lo verifica y se niega
> a consolidar si no cuadra.

---

## 6. Adaptarlo a tu propio corpus

Lo único que necesitas es producir un `corpus.jsonl` con tus documentos. El
resto —índice, búsqueda, interfaz, historial, voz, exportación— ya está hecho.

Casos donde encaja bien:

- **Actas y presupuestos municipales**: preguntar "¿en qué se gastó el rubro de
  obras en 2024?" sobre cientos de PDF.
- **Normativa interna**: resoluciones, circulares, manuales de procedimiento.
- **Contratos y pliegos**: buscar cláusulas por significado, no por palabra.
- **Documentación técnica**: manuales, informes de laboratorio, históricos.

Qué tocar en el código si quieres cambiar el comportamiento:

| Quiero... | Dónde |
| --- | --- |
| Cambiar el papel del asistente | `SISTEMA` en `execution/analista/llm.py` |
| Ajustar cuántos fragmentos se usan | `FRAGMENTOS` en `execution/analista/config.py` |
| Cambiar cómo se ordenan los resultados | `reordenar()` en `execution/analista/rag.py` |
| Cambiar los filtros de la interfaz | `web/index.html` y `web/app.js` |

---

## 7. Errores que cuestan horas

Todos estos ocurrieron de verdad construyendo este proyecto. **Los tres peores
no lanzaron ninguna excepción**: el proceso terminaba con código 0 y un informe
de aspecto normal. Cada uno se descubrió por un número que no cuadraba.

**Una velocidad imposible es un fallo, no un éxito.** 520 PDF "procesados" en
dos minutos: la biblioteca de PDF no era segura entre hilos y fallaba al
instante en cada uno. Si un lote termina sospechosamente rápido, revisa los
resultados antes de celebrarlo.

**Cuenta las entidades al final.** El corpus pasó de 200 a 206 entidades sin
que nadie tocara nada: una carpeta con estructura distinta hizo que el
conversor inventara entidades a partir de nombres de carpeta. 19.221 fragmentos
mal etiquetados. Comprueba siempre los totales contra el catálogo original.

**Los archivos que parecen corruptos a veces no son archivos.** Unos 1.300
"documentos ilegibles" resultaron ser metadatos de macOS (`._*`). Se detectan
por su firma binaria, no por el nombre.

**Recortar "a ojo" no es recortar.** Si el contexto no cabe y decides acortar,
*mide el resultado*. Recortar a 1.500 caracteres por fragmento con 24
fragmentos sigue siendo 36.000 caracteres: no cabía igual.

**El texto para leer en voz alta no es el texto para leer con los ojos.** Hay
que quitar el Markdown y convertir las cifras a palabras antes de sintetizar, o
el lector dirá "asterisco asterisco" y "dólar". Ojo con la escala: en español un
**billón** es un millón de millones. Leer 22.261.110.622 como "veintidós
millones" cambia el dato por un factor de mil.

**No caches los fallos.** Si guardas en caché que algo "no está disponible",
seguirá sin estarlo cuando aparezca. Cachea solo los aciertos.

---

## Y si quieres ir más allá

El mismo corpus sirve para **afinar un modelo** (fine-tuning): con los
fragmentos se pueden generar pares de pregunta y respuesta, y entrenar sobre
ellos. Aquí se generaron cerca de 100.000 pares.

Pero antes de meterte en eso: **prueba primero el RAG**. Es más rápido de
montar, se actualiza añadiendo documentos, responde citando la fuente y permite
verificar cada dato. El fine-tuning tiene sentido cuando quieres cambiar el
*estilo* o el *formato* de las respuestas, no cuando quieres que el modelo
*sepa* algo nuevo.
