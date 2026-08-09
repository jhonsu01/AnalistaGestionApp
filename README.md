# Analista de Gestión Pública

![versión](https://img.shields.io/badge/versión-0.1.7-38bdf8)
![licencia](https://img.shields.io/badge/licencia-MIT-64748b)
![plataformas](https://img.shields.io/badge/Windows%20%7C%20Android-0f172a)

Pregunta en lenguaje natural sobre los **informes de empalme del gobierno
colombiano** y obtén respuestas con la fuente citada: entidad, documento y
sección exacta. Todo se ejecuta en tu equipo.

> No es un buscador de palabras: entiende la pregunta. Puedes preguntar
> *"¿qué quedó pendiente en la red vial?"* y encuentra el párrafo correcto
> aunque no contenga esas palabras.

---

## Descarga

Los instaladores están en la [última release](../../releases/latest).

| Plataforma | Archivo | Requisitos |
| ---------- | ------- | ---------- |
| Windows 10/11 | `AnalistaGestion-x.y.z.msi` | Ninguno: instala sin permisos de administrador |
| Android 8+ | `analista-gestion-x.y.z.apk` | Un PC en la misma red WiFi ejecutando la app |

El APK está firmado en modo depuración, así que Android pedirá permitir
*orígenes desconocidos* la primera vez.

---

## Qué necesitas además

La aplicación **no incluye ni el modelo ni los datos**, y es a propósito: el
corpus pesa varios GB y el modelo otros tantos. Necesitas dos cosas:

**1. Un servidor de modelos local.** Cualquiera con API compatible con OpenAI:

- [LM Studio](https://lmstudio.ai) — el más sencillo, con interfaz gráfica
- [Ollama](https://ollama.com) — línea de comandos
- `llama-server` de llama.cpp

Hacen falta dos modelos: uno de **chat** (para redactar) y uno de
**embeddings** (para buscar). Con `gemma-4` y `nomic-embed-text` funciona bien.

**2. El índice del corpus**, una carpeta con:

```
embeddings.npy     matriz de vectores
metadatos.jsonl    una línea por vector
corpus.jsonl       el texto de cada fragmento
indice.tv          (opcional) índice comprimido, búsquedas más rápidas
```

Ese índice se genera con el pipeline de datos a partir de los documentos
públicos del [portal Datálogo del DNP](https://datalogo.dnp.gov.co/informe-empalme).

---

## Primer arranque

1. Abre la aplicación y pulsa **Ajustes**.
2. Indica la **carpeta del índice** y la **URL de tu servidor de modelos**
   (por defecto `http://localhost:1234/v1`).
3. Guarda. Si todo está bien verás cuántos fragmentos quedaron cargados.

Para usar el móvil, abre la app de escritorio en el PC y luego el APK: el
cliente busca el servidor solo en la red local. Si no lo encuentra, puedes
escribir la IP a mano.

---

## Qué hace por dentro

```
pregunta ─→ embedding ─→ búsqueda vectorial ─→ fragmentos ─→ modelo ─→ respuesta citada
                                                                 └─→ historial (SQLite)
```

Cada consulta queda guardada con su respuesta y sus fuentes, así que puedes
releerla, buscarla meses después y exportarla a Markdown para citarla.

**El sistema admite no saber.** Si los fragmentos recuperados no contienen la
respuesta, lo dice en vez de rellenar con conocimiento general. Un dato
inventado sobre presupuesto público es peor que un "no aparece en los
documentos".

---

## Compilar desde el código

```bash
python -m venv .venv && .venv\Scripts\pip install -r requirements.txt
```

```bash
python run_analista.py
```

Para los instaladores:

```bash
cargo build --release --manifest-path packaging/ventana/Cargo.toml
```

```bash
cd android && gradle assembleDebug
```

Los iconos de todas las densidades se regeneran con:

```bash
python execution/generar_iconos.py
```

---

## Advertencia sobre las fuentes

Los informes de empalme son **autorreportados** por cada entidad saliente:
reflejan lo que cada una dice de su propia gestión, no una auditoría
independiente. Trátalos como fuente primaria de lo que el Estado declara de sí
mismo, y verifica contra el documento original cuando algo sea decisivo. Cada
respuesta trae el archivo exacto para poder hacerlo.

## Licencia

MIT. Los documentos consultados son públicos, amparados por la Ley 1712 de
2014 de Transparencia y Acceso a la Información Pública de Colombia.
