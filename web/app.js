/* Analista de Gestión Pública — interfaz de consulta.
   Sin frameworks: la app se empotra en un WebView y en un APK, y cada
   dependencia extra es peso que viaja en el instalador. */

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const estado = {
  consultaActual: null,
  filtro: 'todas',
  enCurso: false,
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

/* Markdown mínimo: negritas, cursivas, listas y saltos. Suficiente para lo
   que devuelve el modelo, y sin traerse una librería entera. */
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
    .replace(/(^|\W)\*(?!\s)(.+?)(?<!\s)\*(?=\W|$)/g, '$1<em>$2</em>')
    .replace(/`(.+?)`/g, '<code>$1</code>');
}

function fechaCorta(iso) {
  if (!iso) return '';
  const f = new Date(iso);
  return Number.isNaN(f.getTime()) ? iso.slice(0, 16).replace('T', ' ')
    : f.toLocaleString('es-CO', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
}

/* ------------------------------------------------------------------- arranque */

async function cargarEstado() {
  try {
    const r = await fetch('/api/estado');
    const d = await r.json();

    $('#version-app').textContent = 'v' + (d.version || '0.0.0');

    if (!d.corpus?.listo) {
      aviso('Sin corpus configurado. Abre Ajustes e indica la carpeta con el índice.', 'atencion');
    } else if (!d.modelo?.vivo) {
      aviso('No encuentro el servidor de modelos. Revisa la URL en Ajustes.', 'atencion');
    } else {
      const n = (d.corpus.vectores || 0).toLocaleString('es-CO');
      aviso(`Listo · ${n} fragmentos indexados · ${d.modelo.modelo_activo || 'modelo local'}`, 'ok');
      setTimeout(() => aviso(''), 4000);
    }

    const aj = d.ajustes || {};
    $('#aj-corpus').value = aj.corpus_dir || '';
    $('#aj-url').value = aj.llm_url || '';
    $('#aj-clave').value = aj.llm_api_key || '';
    $('#aj-modelo').value = aj.llm_modelo || '';
    $('#aj-embed').value = aj.embed_modelo || '';
    $('#num-k').value = aj.fragmentos || 20;

    if (d.corpus?.listo) cargarCatalogo();
  } catch {
    aviso('No puedo hablar con el servidor local.', 'error');
  }
}

async function cargarCatalogo() {
  try {
    const r = await fetch('/api/catalogo');
    const d = await r.json();
    const sel = $('#sel-sector');
    sel.innerHTML = '<option value="">Todos los sectores</option>' +
      (d.sectores || []).map((s) => `<option value="${escapar(s)}">${escapar(s)}</option>`).join('');
  } catch { /* el catálogo es opcional */ }
}

$('#sel-sector').addEventListener('change', async (e) => {
  const sector = e.target.value;
  try {
    const r = await fetch('/api/catalogo?sector=' + encodeURIComponent(sector));
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
  preguntar(b.textContent);
}));

function preguntar(texto) {
  if (!texto || estado.enCurso) return;
  estado.enCurso = true;
  estado.consultaActual = null;

  $('#zona-respuesta').classList.remove('oculto');
  $('#acciones').classList.add('oculto');
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

  fuente.addEventListener('fuentes', (ev) => {
    const lista = JSON.parse(ev.data).lista || [];
    $('#num-fuentes').textContent = `(${lista.length})`;
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
        <button class="item" data-id="${c.id}">
          <div class="item-preg">${escapar(c.pregunta)}</div>
          <div class="item-meta">
            ${c.fijada ? '<span title="Fijada">📌</span>' : ''}
            ${c.favorita ? '<span title="Favorita">★</span>' : ''}
            <span>${fechaCorta(c.creada_en)}</span>
            ${c.entidad ? `<span class="tenue">${escapar(c.entidad)}</span>` : ''}
          </div>
        </button>`).join('')
      : '<p class="vacio">Aún no hay consultas guardadas.</p>';

    $$('#lista-historial .item').forEach((b) =>
      b.addEventListener('click', () => abrirConsulta(b.dataset.id)));
  } catch { /* el historial es secundario */ }
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
    $('#btn-favorita').textContent = d.favorita ? '★ Favorita' : '☆ Favorita';
    $('#btn-fijar').textContent = d.fijada ? 'Desfijar' : 'Fijar';
    const fuentes = d.fuentes || [];
    $('#num-fuentes').textContent = `(${fuentes.length})`;
    $('#lista-fuentes').innerHTML = fuentes.map((f) => `
      <li>
        <strong>${escapar(f.entidad)}</strong>
        <div class="doc">${escapar(f.archivo)}${f.seccion ? ' · ' + escapar(f.seccion) : ''}</div>
      </li>`).join('');
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
  $('#pregunta').focus();
});

/* -------------------------------------------------------------------- acciones */

$('#btn-favorita').addEventListener('click', async () => {
  if (!estado.consultaActual) return;
  const r = await fetch(`/api/consulta/${estado.consultaActual}/favorita`, { method: 'POST' });
  const d = await r.json();
  $('#btn-favorita').textContent = d.favorita ? '★ Favorita' : '☆ Favorita';
  cargarHistorial();
});

$('#btn-fijar').addEventListener('click', async () => {
  if (!estado.consultaActual) return;
  const r = await fetch(`/api/consulta/${estado.consultaActual}/fijada`, { method: 'POST' });
  const d = await r.json();
  $('#btn-fijar').textContent = d.fijada ? 'Desfijar' : 'Fijar';
  cargarHistorial();
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

/* --------------------------------------------------------------------- ajustes */

$('#btn-ajustes').addEventListener('click', () => $('#dlg-ajustes').showModal());

$('#form-ajustes').addEventListener('submit', async (e) => {
  if (e.submitter?.value !== 'guardar') return;
  e.preventDefault();
  $('#diag-ajustes').textContent = 'Guardando…';
  try {
    const r = await fetch('/api/ajustes', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        corpus_dir: $('#aj-corpus').value.trim(),
        llm_url: $('#aj-url').value.trim(),
        llm_api_key: $('#aj-clave').value,
        llm_modelo: $('#aj-modelo').value.trim(),
        embed_modelo: $('#aj-embed').value.trim(),
        fragmentos: parseInt($('#num-k').value, 10) || 20,
      }),
    });
    const d = await r.json();
    $('#diag-ajustes').textContent = d.corpus?.listo
      ? `Corpus cargado: ${(d.corpus.vectores || 0).toLocaleString('es-CO')} fragmentos.`
      : 'Guardado, pero no encuentro el índice en esa carpeta.';
    cargarEstado();
  } catch {
    $('#diag-ajustes').textContent = 'No pude guardar los ajustes.';
  }
});

/* ---------------------------------------------------------------------- inicio */

cargarEstado();
cargarHistorial();
