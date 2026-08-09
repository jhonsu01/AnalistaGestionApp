# Recomendaciones para publicar datos que una IA pueda leer

Dirigido a entidades públicas que ya publican información y quieren que sea
realmente utilizable —por sistemas de IA, por periodistas de datos, por
investigadores y por cualquier ciudadano con curiosidad.

No son opiniones. Cada punto sale de un problema concreto encontrado al
procesar **8.820 documentos reales** del empalme del gobierno colombiano
2022-2026. Donde hay una cifra, está medida.

---

## Resumen para quien tiene prisa

| Prioridad | Recomendación | Por qué |
| --- | --- | --- |
| **Alta** | PDF con texto, nunca escaneados | El 8% de los documentos necesitó OCR |
| **Alta** | Publicar los datos en CSV además del PDF | Las cifras dentro de tablas en PDF se pierden |
| **Alta** | Una API documentada, aunque sea mínima | La del portal actual está sin documentar |
| **Media** | Nombres de archivo estables y descriptivos | Se detectaron miles de duplicados por hash |
| **Media** | Metadatos junto al documento | Sin ellos hay que adivinar entidad y sector |
| **Media** | Estructura de carpetas uniforme | Una carpeta distinta inventó 6 entidades falsas |
| **Baja** | Índice legible por máquina (sitemap, manifiesto) | Evita rastrear a ciegas |

---

## 1. Que los PDF tengan texto

**El problema.** De los 8.820 documentos procesados, **698 eran imágenes
escaneadas**: páginas fotografiadas dentro de un PDF. Para una máquina —y para
un lector de pantalla— están en blanco. Hubo que pasarles OCR, un proceso lento
y que introduce errores justo donde más duelen: en los números.

**Qué hacer.** Exportar el PDF desde el documento original (Word, Excel) en vez
de imprimir y escanear. Si hay que digitalizar papel, aplicar OCR antes de
publicar y guardar el PDF con la capa de texto incluida.

**Cómo comprobarlo en diez segundos:** abre el PDF y prueba a seleccionar un
párrafo con el ratón. Si no puedes, es una imagen.

> Esto no es solo por la IA: un PDF escaneado es **inaccesible para personas
> ciegas**. Es un requisito de accesibilidad antes que uno técnico.

---

## 2. Los datos, en formato de datos

**El problema.** Las cifras importantes vivían dentro de tablas maquetadas en
PDF y presentaciones. Al extraerlas se pierden las columnas, los totales se
mezclan con los encabezados y un número puede acabar asociado a la fila
equivocada. Además, 102 archivos de Excel ocupaban **4,16 GB de filas vacías**:
hojas con formato aplicado a un millón de filas sin contenido.

**Qué hacer.** Junto a cada informe en PDF, publicar los datos que lo sustentan
en **CSV o Excel limpio**:

- Una fila por registro, una columna por variable.
- Encabezados en la primera fila y nada más arriba (ni logos, ni títulos
  fusionados, ni filas en blanco de adorno).
- Sin celdas combinadas.
- Números como números, no como texto: `1234567.89`, sin separador de miles.
- Las unidades en el nombre de la columna: `presupuesto_cop`, `avance_pct`.
- Fechas en formato ISO: `2024-03-15`.
- Un archivo por tabla. No varias tablas en la misma hoja.

**El PDF es para leer; el CSV es para calcular.** Publicar solo el PDF obliga a
todo el mundo a copiar cifras a mano, que es justo donde se cometen errores.

---

## 3. Una API, aunque sea sencilla

**El problema.** El portal de origen es una aplicación web moderna: el HTML
llega vacío y el contenido lo trae JavaScript. No hay enlaces que seguir. Hubo
que abrir las herramientas de desarrollo del navegador e ir deduciendo las
peticiones internas una por una. Funciona, pero es ingeniería inversa sobre una
API **no documentada**, que puede cambiar sin aviso y romper a todo el que
dependa de ella.

**Qué hacer.** Documentar la API que el portal **ya tiene**. No hay que
construir nada nuevo: solo publicar qué rutas existen, qué parámetros aceptan y
qué devuelven. Una página con ejemplos de `curl` basta.

Y por favor:

- **Un endpoint que liste todo** (con paginación), no solo búsqueda.
- **Enlaces directos y permanentes** a cada archivo.
- **Sin exigir sesión** para material que ya es público. Dos entidades tenían
  sus anexos tras un inicio de sesión pese a ser información pública; hubo que
  descargarlos a mano.
- **CORS abierto** para lectura. Permite construir visualizaciones sin montar
  un servidor intermedio.

---

## 4. Metadatos junto al documento

**El problema.** Al procesar un archivo suelto no hay forma de saber de qué
entidad es, de qué año, ni qué tipo de documento es. Hay que deducirlo del
nombre del archivo y de la carpeta, y ahí es donde se cuelan los errores.

**Qué hacer.** Acompañar cada documento de un pequeño JSON —o incluir estos
campos en el listado de la API:

```json
{
  "titulo": "Informe de empalme 2022-2026",
  "entidad": "Instituto Colombiano de Bienestar Familiar",
  "codigo_entidad": "ICBF",
  "sector": "Inclusión Social",
  "vigencia": "2022-2026",
  "tipo": "informe_empalme",
  "fecha_publicacion": "2026-07-31",
  "version": "2",
  "url": "https://.../informe.pdf",
  "licencia": "CC-BY-4.0",
  "hash_sha256": "..."
}
```

El `hash` permite saber si un archivo cambió sin descargarlo entero. La
`licencia` explícita evita que nadie tenga que suponer si puede reutilizar el
material.

---

## 5. Nombres y estructura estables

**El problema.** El mismo documento aparecía repetido con nombres distintos,
cada copia con un hash pegado al final (`informe_a3f2b1.pdf`,
`informe_9c4d7e.pdf`). Sin agrupar esas copias, una sola consulta devolvía ocho
veces el mismo estado financiero y dejaba fuera lo que de verdad respondía.

Peor: **una carpeta con estructura distinta al resto hizo que el proceso
inventara seis entidades que no existen**, etiquetando mal 19.221 fragmentos.
Se descubrió porque el total pasó de 200 a 206 entidades. Nada dio error.

**Qué hacer.**

- Nombres **descriptivos y estables**: `icbf-informe-empalme-2026.pdf`. Sin
  hashes, sin `copia_final_v2_DEFINITIVO`.
- La misma jerarquía **para todos**: `<sector>/<entidad>/<tipo>/<archivo>`.
  Si una entidad publica distinto, todo lo automático se rompe con ella.
- Si hay versiones, un campo `version` en los metadatos y una URL que apunte
  siempre a la vigente.
- Sin caracteres problemáticos en los nombres: nada de acentos, `#`, `%`, `&`
  ni rutas larguísimas (Windows corta en 260 caracteres, y ahí se pierden
  archivos).

---

## 6. Escribir pensando en que se va a trocear

Un sistema de IA no lee un documento entero: lo parte en fragmentos y busca el
que responde. Lo que ayuda a eso también ayuda a cualquier lector humano con
prisa.

- **Títulos de sección reales**, con la jerarquía del documento (no texto en
  negrita haciendo de título).
- **Cada tabla con su título y sus unidades encima.** Una tabla titulada
  "Tabla 18. Gastos de funcionamiento 2022-2026 (millones de pesos)" se
  entiende suelta; una titulada "Tabla 18" no.
- **Evitar "ver cuadro anterior"**: cuando el fragmento viaja solo, esa
  referencia deja de existir.
- **Las cifras, con su unidad y su periodo al lado.** "$ 380.816" no dice nada;
  "$ 380.816 millones (apropiación vigente 2025)" lo dice todo.
- **Un glosario de siglas** al principio o al final. Los informes públicos
  están llenos de acrónimos que solo conoce quien los escribió.

---

## 7. Lo que ya se hace bien y conviene mantener

Para ser justos, el portal de origen acierta en cosas importantes:

- **Publica de verdad**, sin registro y sin trámite, en formatos abiertos.
- **Organiza por entidad y sector**, lo que permite filtrar con sentido.
- **Incluye los anexos**, no solo el informe resumen. Ahí están los datos.
- **Mantiene los documentos accesibles** pasado el periodo de gobierno.

Nada de este documento cuestiona ese trabajo. Las recomendaciones son para que
ese esfuerzo, que ya está hecho, **rinda mucho más**.

---

## Por qué importa

Cuando los datos públicos son legibles por máquinas, cualquiera puede
preguntarles cosas: un periodista, una veeduría ciudadana, un estudiante, un
funcionario de otra entidad. La información deja de ser un archivo que hay que
leer entero y pasa a ser algo a lo que se le pregunta.

Este proyecto es la demostración de que se puede hacer con las herramientas de
hoy, en el equipo de una persona, sin presupuesto y sin servicios de pago. Lo
único que hizo falta fue que los datos estuvieran publicados.

Con las mejoras de esta guía, lo que costó semanas costaría días.
