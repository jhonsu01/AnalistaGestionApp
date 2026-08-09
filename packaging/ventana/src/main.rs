// Analista de Gestion Publica — ventana de escritorio.
//
// Es la unica cosa que el usuario ejecuta. Se encarga de tres cosas:
//   1. levantar el servidor local con `pythonw.exe`, que no abre consola;
//   2. esperar a que el puerto responda;
//   3. mostrarlo en una ventana propia, sin barra de direcciones ni pestanas.
//
// Usa WebView2, que ya viene con Windows 10 y 11, asi que el ejecutable pesa
// unos pocos MB en vez de arrastrar un navegador entero.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::process::{Child, Command};
use std::time::{Duration, Instant};

use tao::dpi::LogicalSize;
use tao::event::{Event, WindowEvent};
use tao::event_loop::{ControlFlow, EventLoopBuilder};
use tao::window::{Fullscreen, WindowBuilder};
use wry::WebViewBuilder;

const PUERTO: u16 = 8756;
const URL: &str = "http://127.0.0.1:8756";
const ESPERA_MAXIMA: Duration = Duration::from_secs(90);

#[cfg(windows)]
const SIN_VENTANA: u32 = 0x0800_0000; // CREATE_NO_WINDOW

/// Carpeta de instalacion: la del propio ejecutable.
fn raiz() -> PathBuf {
    std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(Path::to_path_buf))
        .unwrap_or_else(|| PathBuf::from("."))
}

/// Abre una direccion en el navegador del sistema.
///
/// Los enlaces de fuera (Ko-fi, el sitio de contacto) no deben cargarse dentro
/// de esta ventana: no tiene barra de direcciones ni boton de atras, asi que el
/// usuario se quedaria atrapado en una pagina ajena sin forma de volver.
fn abrir_fuera(url: &str) {
    // Solo http y https. Sin este filtro, una pagina podria pedir que se
    // ejecutara cualquier cosa a traves del shell.
    if !(url.starts_with("http://") || url.starts_with("https://")) {
        return;
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        // El argumento vacio es el TITULO de la ventana: sin el, `start` toma
        // la URL como titulo y no abre nada.
        let _ = Command::new("cmd")
            .args(["/C", "start", "", url])
            .creation_flags(SIN_VENTANA)
            .spawn();
    }
    #[cfg(not(windows))]
    {
        let _ = Command::new("xdg-open").arg(url).spawn();
    }
}

/// Carpeta Descargas del usuario, para dejar ahi lo que exporte.
///
/// Se arma desde USERPROFILE en vez de preguntar por la ruta real de la
/// biblioteca: no merece la pena arrastrar una dependencia de Windows entera
/// por esto, y si la carpeta no existe se deja que WebView2 decida.
fn carpeta_descargas() -> Option<PathBuf> {
    let destino = PathBuf::from(std::env::var("USERPROFILE").ok()?).join("Downloads");
    destino.is_dir().then_some(destino)
}

/// Donde queda escrito lo que dijo el servidor al arrancar.
fn registro(raiz: &Path) -> PathBuf {
    raiz.join(".tmp").join("arranque.log")
}

fn puerto_responde() -> bool {
    TcpStream::connect_timeout(
        &([127, 0, 0, 1], PUERTO).into(),
        Duration::from_millis(400),
    )
    .is_ok()
}

/// Arranca el servidor con el Python que viaja dentro de la carpeta.
///
/// Devuelve `None` si ya habia uno escuchando: asi abrir la app dos veces no
/// deja dos servidores peleandose por el puerto.
fn arrancar_servidor(raiz: &Path) -> Option<Child> {
    if puerto_responde() {
        return None;
    }

    let python = raiz.join("python").join("pythonw.exe");
    let guion = raiz.join("run_analista.py");
    let interprete = if python.exists() {
        python
    } else {
        PathBuf::from("pythonw.exe") // por si se ejecuta desde el repo
    };

    let mut orden = Command::new(interprete);
    orden
        .arg(guion)
        .arg("--sin-navegador")
        .current_dir(raiz)
        // El Python empotrado no debe ver los paquetes del usuario: si el
        // equipo tiene otro Python, sus site-packages lo romperian.
        .env("PYTHONNOUSERSITE", "1")
        .env("PYTHONUTF8", "1")
        // En un portatil antiguo, la OpenBLAS que numpy trae dentro fallaba al
        // inicializarse (error 1114) al montar su pool de hilos. La leemos
        // ANTES de que cargue la DLL, que es el unico momento en que sirve.
        // No perdemos nada: nuestro unico calculo es un producto escalar de
        // 6886x384, que en un hilo tarda milisegundos.
        .env("OPENBLAS_NUM_THREADS", "1")
        .env("OMP_NUM_THREADS", "1")
        // Esta ventana YA muestra la interfaz. Sin esta marca el servidor
        // abria ademas el navegador y el usuario acababa con la aplicacion
        // duplicada: una en su ventana y otra en una pestana suelta.
        .env("ANALISTA_VENTANA", "1");

    // Al servidor le damos SIEMPRE un stdout y un stderr de verdad. Lanzado
    // desde un acceso directo no hay consola que heredar, y uvicorn llama a
    // `sys.stdout.isatty()` al configurar su registro: sin esto el servidor
    // moria al arrancar y solo se veia un aviso generico. Ademas, asi queda
    // por escrito lo que paso.
    let _ = std::fs::create_dir_all(raiz.join(".tmp"));
    if let Ok(log) = std::fs::File::create(registro(raiz)) {
        if let Ok(copia) = log.try_clone() {
            orden.stdout(log).stderr(copia);
        }
    }

    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        orden.creation_flags(SIN_VENTANA);
    }

    orden.spawn().ok()
}

/// Espera a que el servidor levante. Devuelve false si nunca llego.
fn esperar_servidor() -> bool {
    let limite = Instant::now() + ESPERA_MAXIMA;
    while Instant::now() < limite {
        if puerto_responde() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(250));
    }
    false
}

#[cfg(windows)]
unsafe extern "system" {
    fn MessageBoxW(hwnd: *mut u8, texto: *const u16, titulo: *const u16, tipo: u32) -> i32;
}

#[cfg(windows)]
fn avisar(texto: &str) {
    // Sin consola donde quejarse, el unico canal honesto es un cuadro de dialogo.
    let mut cuerpo: Vec<u16> = texto.encode_utf16().collect();
    cuerpo.push(0);
    let mut titulo: Vec<u16> = "Analista de Gestion Publica".encode_utf16().collect();
    titulo.push(0);
    unsafe {
        MessageBoxW(std::ptr::null_mut(), cuerpo.as_ptr(), titulo.as_ptr(), 0x10);
    }
}

#[cfg(not(windows))]
fn avisar(texto: &str) {
    eprintln!("{texto}");
}

fn main() -> wry::Result<()> {
    let peque = std::env::args().any(|a| a == "--peque");
    let raiz = raiz();

    let mut servidor = arrancar_servidor(&raiz);

    if !esperar_servidor() {
        // Un "reinicia el equipo" no sirve de nada si no decimos que fallo:
        // adjuntamos las ultimas lineas del registro, que es lo unico que
        // permite arreglarlo de verdad.
        let cola = std::fs::read_to_string(registro(&raiz))
            .map(|t| {
                let lineas: Vec<&str> = t.lines().filter(|l| !l.trim().is_empty()).collect();
                lineas[lineas.len().saturating_sub(6)..].join("\n")
            })
            .unwrap_or_default();
        let detalle = if cola.is_empty() {
            "El servidor no dejo ningun mensaje.".to_string()
        } else {
            cola
        };
        avisar(&format!(
            "No he conseguido arrancar el Analista.\n\n{detalle}\n\n\
             El registro completo esta en:\n{}",
            registro(&raiz).display()
        ));
        if let Some(p) = servidor.as_mut() {
            let _ = p.kill();
        }
        return Ok(());
    }

    let evento = EventLoopBuilder::new().build();
    let mut constructor = WindowBuilder::new()
        .with_title("Analista de Gestion Publica")
        .with_inner_size(LogicalSize::new(1180.0, 820.0))
        .with_min_inner_size(LogicalSize::new(420.0, 560.0));

    if peque {
        // Modo peque: a pantalla completa y sin marco, para que no haya nada
        // que tocar por accidente.
        constructor = constructor
            .with_fullscreen(Some(Fullscreen::Borderless(None)))
            .with_decorations(false);
    }

    let ventana = constructor.build(&evento).unwrap();

    let destino = if peque {
        format!("{URL}/?peque=1")
    } else {
        URL.to_string()
    };

    let _webview = WebViewBuilder::new()
        .with_url(destino)
        // La respuesta se lee en voz alta en cuanto se pide: sin esto el audio
        // queda mudo esperando un clic que nadie va a dar.
        .with_autoplay(true)
        .with_background_color((26, 24, 22, 255))
        // Sin este manejador, WebView2 descarta las descargas en silencio y
        // el boton "Exportar" no hacia nada: el servidor entregaba el archivo
        // y se perdia. Aqui se acepta y se manda a la carpeta Descargas.
        .with_download_started_handler(|_uri, ruta| {
            if let Some(descargas) = carpeta_descargas() {
                if let Some(nombre) = ruta.file_name().map(|n| n.to_os_string()) {
                    *ruta = descargas.join(nombre);
                }
            }
            true
        })
        // Lo que no sea la propia aplicacion se abre fuera. Devolver false
        // cancela la navegacion dentro de la ventana.
        .with_navigation_handler(|url| {
            if url.starts_with(URL) || url.starts_with("about:") {
                return true;
            }
            abrir_fuera(&url);
            false
        })
        .build(&ventana)?;

    evento.run(move |evt, _, control| {
        *control = ControlFlow::Wait;
        if let Event::WindowEvent {
            event: WindowEvent::CloseRequested,
            ..
        } = evt
        {
            if let Some(p) = servidor.as_mut() {
                let _ = p.kill();
            }
            *control = ControlFlow::Exit;
        }
    });
}
