/* Analista de Gestión Pública — interfaz de consulta.
   Sin frameworks: la app se empotra en un WebView y en un APK, y cada
   dependencia extra es peso que viaja en el instalador. */

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const estado = {
  consultaActual: null,
  filtro: 'todas',
  enCurso: false,
  hayVoz: false,
};

/* ---------------------------------------------------------------- utilidades */

function aviso(texto, tipo = 'info') {
  const barra = $('#barra-estado');
  if (!texto) { barra.classList.add('oculto'); return; }
  barra.textContent = texto;
  barra.className = `aviso ${tipo}`;
}

function escapar(t) {
  const d = document.createElement('div');
  d.textContent = t ?? '';
  return d.innerHTML;
}

/* Markdown mínimo. El texto se escapa ANTES de aplicar formato, así que no
   puede inyectarse HTML por mucho que el modelo lo devuelva. */
function markdown(texto) {
  const lineas = escapar(texto).split('\n');
  let html = '', enLista = false;
  for (const linea of lineas) {
    const item = linea.match(/^\s*[-*]\s+(.*)$/);
    if (item) {
      if (!enLista) { html += '<ul>'; enLista = true; }
      html += `<li>${item[1]}</li>`;
      continue;
    }
    if (enLista) { html += '</ul>'; enLista = false; }
    if (linea.trim()) html += `<p>${linea}</p>`;
  }
  if (enLista) html += '</ul>';
  return html
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`(.+?)`/g, '<code>$1</code>');
}

function fechaCorta(iso) {
  if (!iso) return '';
  const f = new Date(iso);
  return Number.isNaN(f.getTime()) ? iso.slice(0, 16).replace('T', ' ')
    : f.toLocaleString('es-CO', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
}

/* --------------------------------------------------------- lateral en móvil */

function abrirLateral(abrir) {
  document.body.classList.toggle('lateral-abierto', abrir);
  $('#velo').hidden = !abrir;
  $('#btn-menu').setAttribute('aria-expanded', String(abrir));
}

$('#btn-menu').addEventListener('click', () => abrirLateral(true));
$('#btn-cerrar-lateral').addEventListener('click', () => abrirLateral(false));
$('#velo').addEventListener('click', () => abrirLateral(false));

/* ------------------------------------------------------------------- estado */

async function cargarEstado() {
  try {
    const r = await fetch('/api/estado');
    const d = await r.json();

    $('#version-app').textContent = 'v' + (d.version || '0.0.0');

    const aj = d.ajustes || {};
    $('#aj-corpus').value = aj.corpus_dir || '';
    $('#aj-url').value = aj.llm_url || '';
    $('#aj-clave').value = aj.llm_api_key || '';
    $('#aj-modelo').value = aj.llm_modelo || '';
    $('#aj-embed').value = aj.embed_modelo || '';
    $('#num-k').value = aj.fragmentos || 12;
    $('#aj-maxtok').value = aj.max_tokens || 8000;
    $('#aj-ctx').value = aj.contexto_maximo || 32000;
    $('#aj-chars').value = aj.chars_por_fragmento || 4000;

    // Voz
    estado.hayVoz = !!(d.voz?.disponible && (d.voz.voces || []).length);
    pintarVoces(d.voz, aj.voz_activa);
    $('#btn-escuchar').classList.toggle('oculto', !estado.hayVoz);

    if (!d.corpus?.listo) {
      aviso('Sin corpus configurado. Abre Ajustes e indica la carpeta del índice.', 'atencion');
    } else if (!d.modelo?.vivo) {
      aviso('No encuentro el servidor de modelos. Revísalo en Ajustes.', 'atencion');
    } else {
      const n = (d.corpus.vectores || 0).toLocaleString('es-CO');
      aviso(`Listo · ${n} fragmentos · ${d.modelo.modelo_activo || 'modelo local'}`, 'ok');
      setTimeout(() => aviso(''), 4000);
      cargarCatalogo();
    }
  } catch {
    aviso('No puedo hablar con el servidor local.', 'error');
  }
}

function pintarVoces(voz, activa) {
  const sel = $('#aj-voz');
  const lista = voz?.voces || [];
  sel.innerHTML = '<option value="">Sin lectura</option>' + lista.map((v) => {
    const id = v.clave ?? v.id ?? v;
    const nombre = v.nombre ?? id;
    const desc = v.descripcion ? ` — ${v.descripcion}` : '';
    return `<option value="${escapar(id)}">${escapar(nombre + desc)}</option>`;
  }).join('');
  if (activa) sel.value = activa;

  $('#aviso-voz').textContent = voz?.disponible
    ? (lista.length ? `${lista.length} voz(ces) instalada(s).`
                    : 'Aún no hay voces. Descarga una de la lista de abajo.')
    : (voz?.detalle || voz?.motivo || 'Síntesis de voz no disponible en este equipo.');

  // Catálogo descargable: sin consolas, un botón por voz
  const cat = voz?.catalogo || [];
  $('#lista-voces').innerHTML = cat.length ? cat.map((v) => `
    <li>
      <span class="voz-nombre">${escapar(v.descripcion)}</span>
      <span class="tenue">${v.mb} MB</span>
      ${v.instalada
        ? '<span class="ok-marca">instalada</span>'
        : `<button type="button" class="plano bajar-voz" data-voz="${escapar(v.clave)}">Descargar</button>`}
    </li>`).join('') : '<li class="tenue">Catálogo no disponible.</li>';

  $$('.bajar-voz').forEach((b) =>
    b.addEventListener('click', () => descargarVoz(b.dataset.voz, b)));
}

function descargarVoz(clave, boton) {
  if (!clave) return;
  boton.disabled = true;
  boton.textContent = 'Descargando…';
  const prog = $('#progreso-voz');
  prog.className = 'diag';
  prog.textContent = 'Preparando…';

  const fuente = new EventSource('/api/voces/descargar?voz=' + encodeURIComponent(clave));

  fuente.addEventListener('fase', (ev) => { prog.textContent = JSON.parse(ev.data).t; });

  fuente.addEventListener('error', (ev) => {
    let detalle = 'La descarga falló.';
    try { detalle = JSON.parse(ev.data).detalle || detalle; } catch { /* SSE cortado */ }
    prog.className = 'diag error';
    prog.textContent = detalle;
    boton.disabled = false;
    boton.textContent = 'Reintentar';
    fuente.close();
  });

  fuente.addEventListener('fin', async () => {
    prog.className = 'diag ok';
    prog.textContent = 'Voz instalada y lista para usar.';
    fuente.close();
    await cargarEstado();   // repinta el selector con la voz ya disponible

    // Si no habia ninguna voz activa, se activa esta: quien acaba de esperar
    // una descarga de decenas de MB quiere oirla, no buscarla en un desplegable.
    const sel = $('#aj-voz');
    if (!sel.value) {
      // Las claves del catalogo y las del selector no siempre coinciden:
      // sharvard trae dos hablantes (es_sharvard_m / es_sharvard_f) en un
      // unico fichero descargable (es_sharvard).
      const opcion = [...sel.options].find(
        (o) => o.value === clave || o.value.startsWith(clave));
      if (opcion) {
        sel.value = opcion.value;
        await guardarAjustes();
        prog.textContent = `Voz instalada y activada: ${opcion.textContent.trim()}`;
      }
    }
  });
}

async function cargarCatalogo() {
  try {
    const r = await fetch('/api/catalogo');
    const d = await r.json();
    $('#sel-sector').innerHTML = '<option value="">Todos los sectores</option>' +
      (d.sectores || []).map((s) => `<option value="${escapar(s)}">${escapar(s)}</option>`).join('');
  } catch { /* el catálogo es opcional */ }
}

$('#sel-sector').addEventListener('change', async (e) => {
  try {
    const r = await fetch('/api/catalogo?sector=' + encodeURIComponent(e.target.value));
    const d = await r.json();
    $('#sel-entidad').innerHTML = '<option value="">Todas las entidades</option>' +
      (d.entidades || []).map((x) => `<option value="${escapar(x)}">${escapar(x)}</option>`).join('');
  } catch { /* ignorado */ }
});

/* ------------------------------------------------------------------- consulta */

$('#form-consulta').addEventListener('submit', (e) => {
  e.preventDefault();
  preguntar($('#pregunta').value.trim());
});

$$('.sugerencia').forEach((b) => b.addEventListener('click', () => {
  $('#pregunta').value = b.textContent;
  $('#caja-sugerencias').open = false;
  preguntar(b.textContent);
}));

function preguntar(texto) {
  if (!texto || estado.enCurso) return;
  estado.enCurso = true;
  estado.consultaActual = null;
  abrirLateral(false);

  $('#zona-respuesta').classList.remove('oculto');
  $('#acciones').classList.add('oculto');
  $('#reproductor').classList.add('oculto');
  $('#respuesta').innerHTML = '<p class="pensando">Buscando…</p>';
  $('#lista-fuentes').innerHTML = '';
  $('#num-fuentes').textContent = '';
  $('#btn-preguntar').disabled = true;

  const params = new URLSearchParams({
    q: texto,
    sector: $('#sel-sector').value,
    entidad: $('#sel-entidad').value,
    k: $('#num-k').value,
  });

  const fuente = new EventSource('/api/consultar?' + params);
  let acumulado = '';

  fuente.addEventListener('fase', (ev) => {
    if (!acumulado) $('#respuesta').innerHTML = `<p class="pensando">${escapar(JSON.parse(ev.data).t)}</p>`;
  });

  fuente.addEventListener('aviso', (ev) => {
    aviso(JSON.parse(ev.data).t, 'atencion');
    setTimeout(() => aviso(''), 6000);
  });

  fuente.addEventListener('fuentes', (ev) => {
    const d = JSON.parse(ev.data);
    const lista = d.lista || [];
    $('#num-fuentes').textContent = `(${lista.length}${d.tokens ? ` · ~${d.tokens.toLocaleString('es-CO')} tokens` : ''})`;
    $('#lista-fuentes').innerHTML = lista.map((f) => `
      <li>
        <strong>${escapar(f.entidad)}</strong>
        <span class="tenue">${escapar(f.sector)}</span>
        <div class="doc">${escapar(f.archivo)}${f.seccion ? ' · ' + escapar(f.seccion) : ''}</div>
        <span class="sim">${(f.similitud * 100).toFixed(1)}%</span>
      </li>`).join('');
  });

  fuente.addEventListener('texto', (ev) => {
    acumulado += JSON.parse(ev.data).t;
    $('#respuesta').innerHTML = markdown(acumulado);
  });

  fuente.addEventListener('error', (ev) => {
    let detalle = 'Error de conexión con el servidor.';
    try { detalle = JSON.parse(ev.data).detalle || detalle; } catch { /* SSE cortado */ }
    $('#respuesta').innerHTML = `<p class="error">${escapar(detalle)}</p>`;
    cerrar();
  });

  fuente.addEventListener('fin', (ev) => {
    estado.consultaActual = JSON.parse(ev.data).id;
    $('#acciones').classList.remove('oculto');
    cargarHistorial();
    cerrar();
  });

  function cerrar() {
    fuente.close();
    estado.enCurso = false;
    $('#btn-preguntar').disabled = false;
  }
}

/* ------------------------------------------------------------------ historial */

async function cargarHistorial() {
  const buscar = $('#buscar-historial').value.trim();
  let url = '/api/historial?limite=60';
  if (buscar) url += '&buscar=' + encodeURIComponent(buscar);
  else if (estado.filtro === 'favoritas') url += '&favoritas=1';
  else if (estado.filtro === 'fijadas') url += '&fijadas=1';

  try {
    const r = await fetch(url);
    const d = await r.json();
    const lista = d.consultas || [];
    $('#lista-historial').innerHTML = lista.length
      ? lista.map((c) => `
        <div class="item-fila">
          <button class="item" data-id="${c.id}">
            <div class="item-preg">${escapar(c.pregunta)}</div>
            <div class="item-meta">
              ${c.fijada ? '<span title="Fijada">📌</span>' : ''}
              ${c.favorita ? '<span title="Favorita">★</span>' : ''}
              <span>${fechaCorta(c.creada_en)}</span>
            </div>
          </button>
          <button class="borrar-item" data-borrar="${c.id}"
                  title="Eliminar" aria-label="Eliminar consulta">✕</button>
        </div>`).join('')
      : '<p class="vacio">Aún no hay consultas guardadas.</p>';

    $$('#lista-historial .item').forEach((b) =>
      b.addEventListener('click', () => abrirConsulta(b.dataset.id)));
    $$('#lista-historial .borrar-item').forEach((b) =>
      b.addEventListener('click', (e) => {
        e.stopPropagation();
        borrarConsulta(b.dataset.borrar);
      }));
  } catch { /* el historial es secundario */ }
}

async function borrarConsulta(id) {
  if (!confirm('¿Eliminar esta consulta del historial?')) return;
  try {
    await fetch('/api/consulta/' + id, { method: 'DELETE' });
    if (String(estado.consultaActual) === String(id)) {
      estado.consultaActual = null;
      $('#zona-respuesta').classList.add('oculto');
    }
    cargarHistorial();
    aviso('Consulta eliminada.', 'ok');
    setTimeout(() => aviso(''), 2000);
  } catch { aviso('No pude eliminarla.', 'error'); }
}

async function abrirConsulta(id) {
  try {
    const r = await fetch('/api/consulta/' + id);
    const d = await r.json();
    estado.consultaActual = d.id;
    $('#pregunta').value = d.pregunta;
    $('#zona-respuesta').classList.remove('oculto');
    $('#respuesta').innerHTML = markdown(d.respuesta);
    $('#acciones').classList.remove('oculto');
    $('#reproductor').classList.add('oculto');
    $('#btn-favorita').textContent = d.favorita ? '★ Favorita' : '☆ Favorita';
    $('#btn-fijar').textContent = d.fijada ? 'Desfijar' : 'Fijar';
    const fuentes = d.fuentes || [];
    $('#num-fuentes').textContent = `(${fuentes.length})`;
    $('#lista-fuentes').innerHTML = fuentes.map((f) => `
      <li>
        <strong>${escapar(f.entidad)}</strong>
        <div class="doc">${escapar(f.archivo)}${f.seccion ? ' · ' + escapar(f.seccion) : ''}</div>
      </li>`).join('');
    abrirLateral(false);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  } catch { aviso('No pude abrir esa consulta.', 'error'); }
}

$('#buscar-historial').addEventListener('input', () => {
  clearTimeout($('#buscar-historial')._t);
  $('#buscar-historial')._t = setTimeout(cargarHistorial, 250);
});

$$('.chip').forEach((c) => c.addEventListener('click', () => {
  $$('.chip').forEach((x) => x.classList.remove('activo'));
  c.classList.add('activo');
  estado.filtro = c.dataset.filtro;
  cargarHistorial();
}));

$('#btn-nueva').addEventListener('click', () => {
  $('#pregunta').value = '';
  $('#zona-respuesta').classList.add('oculto');
  estado.consultaActual = null;
  abrirLateral(false);
  $('#pregunta').focus();
});

/* -------------------------------------------------------------------- acciones */

$('#btn-favorita').addEventListener('click', async () => {
  if (!estado.consultaActual) return;
  const r = await fetch(`/api/consulta/${estado.consultaActual}/favorita`, { method: 'POST' });
  $('#btn-favorita').textContent = (await r.json()).favorita ? '★ Favorita' : '☆ Favorita';
  cargarHistorial();
});

$('#btn-fijar').addEventListener('click', async () => {
  if (!estado.consultaActual) return;
  const r = await fetch(`/api/consulta/${estado.consultaActual}/fijada`, { method: 'POST' });
  $('#btn-fijar').textContent = (await r.json()).fijada ? 'Desfijar' : 'Fijar';
  cargarHistorial();
});

$('#btn-escuchar').addEventListener('click', async () => {
  if (!estado.consultaActual) return;
  const audio = $('#reproductor');
  $('#btn-escuchar').disabled = true;
  $('#btn-escuchar').textContent = 'Generando…';
  try {
    audio.src = `/api/consulta/${estado.consultaActual}/audio`;
    audio.classList.remove('oculto');
    await audio.play();
  } catch {
    aviso('No pude generar el audio. Revisa la voz en Ajustes.', 'error');
  } finally {
    $('#btn-escuchar').disabled = false;
    $('#btn-escuchar').textContent = '▶ Escuchar';
  }
});

$('#btn-copiar').addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText($('#respuesta').innerText);
    aviso('Respuesta copiada.', 'ok');
    setTimeout(() => aviso(''), 2000);
  } catch { aviso('No pude copiar.', 'error'); }
});

$('#btn-exportar').addEventListener('click', () => {
  if (estado.consultaActual) location.href = `/api/consulta/${estado.consultaActual}/markdown`;
});

$('#btn-borrar').addEventListener('click', () => {
  if (estado.consultaActual) borrarConsulta(estado.consultaActual);
});

/* --------------------------------------------------------------------- ajustes */

function abrirAjustes() { $('#dlg-ajustes').showModal(); }
$('#btn-ajustes').addEventListener('click', abrirAjustes);
$('#btn-ajustes-movil').addEventListener('click', abrirAjustes);

/* Buscar modelos en el servidor, para no tener que escribirlos a mano */
$('#btn-buscar-modelos').addEventListener('click', async () => {
  $('#diag-ajustes').textContent = 'Buscando modelos…';
  // Se guarda primero la URL y la clave: si no, se buscarían con los valores viejos.
  await guardarAjustes(true);
  try {
    const r = await fetch('/api/modelos');
    const d = await r.json();
    if (!(d.todos || []).length) {
      $('#diag-ajustes').textContent = 'El servidor no devolvió modelos. Revisa URL y clave.';
      return;
    }
    const opciones = (lista) => '<option value="">— elegir —</option>' +
      lista.map((m) => `<option value="${escapar(m)}">${escapar(m)}</option>`).join('');
    $('#aj-modelo-sel').innerHTML = opciones(d.chat.length ? d.chat : d.todos);
    $('#aj-embed-sel').innerHTML = opciones(d.embeddings.length ? d.embeddings : d.todos);
    if ($('#aj-modelo').value) $('#aj-modelo-sel').value = $('#aj-modelo').value;
    if ($('#aj-embed').value) $('#aj-embed-sel').value = $('#aj-embed').value;
    $('#diag-ajustes').textContent =
      `${d.todos.length} modelos encontrados (${d.chat.length} de chat, ${d.embeddings.length} de embeddings).`;
  } catch {
    $('#diag-ajustes').textContent = 'No pude consultar el servidor.';
  }
});

$('#aj-modelo-sel').addEventListener('change', (e) => {
  if (e.target.value) $('#aj-modelo').value = e.target.value;
});
$('#aj-embed-sel').addEventListener('change', (e) => {
  if (e.target.value) $('#aj-embed').value = e.target.value;
});

$('#btn-probar').addEventListener('click', async () => {
  $('#diag-ajustes').textContent = 'Probando…';
  await guardarAjustes(true);
  try {
    const r = await fetch('/api/probar', { method: 'POST' });
    const d = await r.json();
    $('#diag-ajustes').textContent = (d.ok ? '✓ ' : '✗ ') + (d.detalle || '');
    $('#diag-ajustes').className = 'diag ' + (d.ok ? 'ok' : 'error');
  } catch {
    $('#diag-ajustes').textContent = 'No pude probar la conexión.';
  }
});

async function guardarAjustes(silencioso = false) {
  const cuerpo = {
    corpus_dir: $('#aj-corpus').value.trim(),
    llm_url: $('#aj-url').value.trim(),
    llm_api_key: $('#aj-clave').value,
    llm_modelo: $('#aj-modelo').value.trim(),
    embed_modelo: $('#aj-embed').value.trim(),
    fragmentos: parseInt($('#num-k').value, 10) || 12,
    max_tokens: parseInt($('#aj-maxtok').value, 10) || 8000,
    contexto_maximo: parseInt($('#aj-ctx').value, 10) || 32000,
    chars_por_fragmento: parseInt($('#aj-chars').value, 10) || 4000,
    voz_activa: $('#aj-voz').value,
  };
  const r = await fetch('/api/ajustes', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(cuerpo),
  });
  const d = await r.json();
  if (!silencioso) {
    $('#diag-ajustes').className = 'diag';
    $('#diag-ajustes').textContent = d.corpus?.listo
      ? `Corpus cargado: ${(d.corpus.vectores || 0).toLocaleString('es-CO')} fragmentos.`
      : 'Guardado, pero no encuentro el índice en esa carpeta.';
    cargarEstado();
  }
  return d;
}

$('#form-ajustes').addEventListener('submit', async (e) => {
  if (e.submitter?.value !== 'guardar') return;
  e.preventDefault();
  $('#diag-ajustes').textContent = 'Guardando…';
  try { await guardarAjustes(); }
  catch { $('#diag-ajustes').textContent = 'No pude guardar los ajustes.'; }
});

/* ---------------------------------------------------------------------- inicio */

cargarEstado();
cargarHistorial();
