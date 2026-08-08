// Mete el icono y los datos de version dentro del .exe, para que Windows lo
// muestre igual en el escritorio, en la barra de tareas y en Alt+Tab.
fn main() {
    #[cfg(windows)]
    {
        let icono = std::path::Path::new("../windows/analista.ico");
        if icono.exists() {
            let mut recursos = winresource::WindowsResource::new();
            recursos
                .set_icon(icono.to_str().unwrap())
                .set("ProductName", "Analista de Gestion Publica")
                .set("FileDescription", "Analista de Gestion Publica")
                .set("LegalCopyright", "MIT");
            if let Err(e) = recursos.compile() {
                println!("cargo:warning=sin icono empotrado: {e}");
            }
        }
        println!("cargo:rerun-if-changed=../windows/analista.ico");
    }
}
