package com.jhonsu01.analista

import android.annotation.SuppressLint
import android.app.DownloadManager
import android.content.Context
import android.content.SharedPreferences
import android.graphics.Color
import android.os.Build
import android.os.Bundle
import android.view.View
import android.view.WindowManager
import android.webkit.PermissionRequest
import android.webkit.URLUtil
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.*
import androidx.appcompat.app.AppCompatActivity
import kotlinx.coroutines.*
import java.net.HttpURLConnection
import java.net.Inet4Address
import java.net.NetworkInterface
import java.net.URL

/**
 * Cliente del Analista de Gestión.
 *
 * Esta app NO genera consultas: los pide al ordenador de la red local que ejecuta
 * el servidor. Por eso lo primero que hace es encontrarlo, y solo despues abre
 * la interfaz dentro de un WebView.
 */
class MainActivity : AppCompatActivity() {

    private lateinit var prefs: SharedPreferences
    private lateinit var web: WebView
    private lateinit var panelConexion: LinearLayout
    private lateinit var estado: TextView
    private lateinit var campoIp: EditText
    private val alcance = CoroutineScope(Dispatchers.Main + SupervisorJob())

    companion object {
        const val PUERTO = 8756
        const val CLAVE_SERVIDOR = "servidor"
        const val TIEMPO_SONDEO = 350        // ms por host al escanear la red
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = getSharedPreferences("analista", Context.MODE_PRIVATE)

        // La pantalla no debe apagarse mientras se escucha una consulta.
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        construirVista()

        val guardado = prefs.getString(CLAVE_SERVIDOR, null)
        if (guardado != null) conectar(guardado) else buscarServidor()
    }

    // ── Interfaz ────────────────────────────────────────────────────────────
    @SuppressLint("SetJavaScriptEnabled")
    private fun construirVista() {
        val raiz = FrameLayout(this).apply { setBackgroundColor(Color.parseColor("#0f172a")) }

        web = WebView(this).apply {
            visibility = View.GONE
            settings.apply {
                javaScriptEnabled = true
                domStorageEnabled = true
                mediaPlaybackRequiresUserGesture = false   // el audio arranca solo
                cacheMode = WebSettings.LOAD_DEFAULT
            }
            webChromeClient = object : WebChromeClient() {
                override fun onPermissionRequest(request: PermissionRequest) {
                    // El microfono lo pide la propia pagina para dictar la consulta.
                    runOnUiThread { request.grant(request.resources) }
                }
            }
            webViewClient = object : WebViewClient() {
                override fun onReceivedError(
                    v: WebView?, req: WebResourceRequest?, err: WebResourceError?
                ) {
                    if (req?.isForMainFrame == true) mostrarPanel("Se perdió la conexión con el Analista.")
                }
            }

            // Sin esto, "Exportar" no hace absolutamente nada: un WebView
            // ignora las respuestas con Content-Disposition y no descarga.
            // El servidor respondia correctamente y el archivo se perdia.
            setDownloadListener { url, agente, disposicion, tipo, _ ->
                descargar(url, agente, disposicion, tipo)
            }
        }
        raiz.addView(web, FrameLayout.LayoutParams(-1, -1))

        estado = TextView(this).apply {
            textSize = 16f
            setTextColor(Color.parseColor("#a9a19a"))
            gravity = android.view.Gravity.CENTER
        }
        campoIp = EditText(this).apply {
            hint = "192.168.1.50"
            setTextColor(Color.parseColor("#efe9e1"))
            setHintTextColor(Color.parseColor("#7d756f"))
            inputType = android.text.InputType.TYPE_CLASS_TEXT
        }
        val botonBuscar = Button(this).apply {
            text = "Buscar en mi red"
            setOnClickListener { buscarServidor() }
        }
        val botonManual = Button(this).apply {
            text = "Conectar a esta dirección"
            setOnClickListener {
                val ip = campoIp.text.toString().trim()
                if (ip.isNotEmpty()) probarYConectar(ip)
            }
        }

        panelConexion = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(64, 96, 64, 64)
            gravity = android.view.Gravity.CENTER
            addView(TextView(this@MainActivity).apply {
                text = "Analista de Gestión"
                textSize = 28f
                setTextColor(Color.parseColor("#efe9e1"))
                gravity = android.view.Gravity.CENTER
            })
            addView(TextView(this@MainActivity).apply {
                text = "Esta app se conecta al ordenador donde corre el Analista.\n" +
                        "Los dos tienen que estar en la misma red: da igual que el " +
                        "ordenador vaya por cable y el móvil por WiFi."
                textSize = 14f
                setTextColor(Color.parseColor("#7d756f"))
                gravity = android.view.Gravity.CENTER
                setPadding(0, 24, 0, 40)
            })
            addView(estado)
            addView(botonBuscar)
            addView(TextView(this@MainActivity).apply {
                text = "\n¿No lo encuentra? Escribe la IP del ordenador:"
                textSize = 13f
                setTextColor(Color.parseColor("#7d756f"))
            })
            addView(campoIp)
            addView(botonManual)
            addView(TextView(this@MainActivity).apply {
                text = "☕ Apoyar el proyecto"
                textSize = 12f
                setTextColor(Color.parseColor("#7d756f"))
                gravity = android.view.Gravity.CENTER
                setPadding(0, 56, 0, 0)
                setOnClickListener {
                    startActivity(
                        android.content.Intent(
                            android.content.Intent.ACTION_VIEW,
                            android.net.Uri.parse("https://ko-fi.com/V7V81LV7GX")
                        )
                    )
                }
            })
        }
        raiz.addView(panelConexion, FrameLayout.LayoutParams(-1, -1))
        setContentView(raiz)
    }

    /** Guarda en Descargas lo que el WebView no sabe descargar por su cuenta. */
    private fun descargar(url: String, agente: String?, disposicion: String?, tipo: String?) {
        try {
            val nombre = URLUtil.guessFileName(url, disposicion, tipo)
            val peticion = DownloadManager.Request(android.net.Uri.parse(url)).apply {
                setTitle(nombre)
                setDescription("Analista de Gestión")
                if (agente != null) addRequestHeader("User-Agent", agente)
                setNotificationVisibility(
                    DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                setDestinationInExternalPublicDir(
                    android.os.Environment.DIRECTORY_DOWNLOADS, nombre)
            }
            (getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager).enqueue(peticion)
            Toast.makeText(this, "Guardando $nombre en Descargas", Toast.LENGTH_SHORT).show()
        } catch (e: Exception) {
            Toast.makeText(this, "No pude guardar el archivo: ${e.message}",
                Toast.LENGTH_LONG).show()
        }
    }

    private fun mostrarPanel(mensaje: String) {
        panelConexion.visibility = View.VISIBLE
        web.visibility = View.GONE
        estado.text = mensaje
    }

    // ── Descubrimiento ──────────────────────────────────────────────────────
    /**
     * Recorre la subred del telefono buscando quien responda `/api/estado`.
     * Es fuerza bruta sobre 254 direcciones, pero en paralelo tarda ~2 s y
     * evita pedirle al usuario que sepa su IP.
     */
    private fun buscarServidor() {
        estado.text = "Buscando el Analista en tu red…"
        alcance.launch {
            val bases = withContext(Dispatchers.IO) { prefijosRed() }
            if (bases.isEmpty()) {
                estado.text = "No estás conectado a ninguna red local. " +
                    "Conéctate al mismo router que el ordenador."
                return@launch
            }
            val encontrado = withContext(Dispatchers.IO) {
                // Barremos todas las subredes a la vez: el ordenador puede
                // estar por cable y el telefono por WiFi.
                val trabajos = bases.flatMap { base ->
                    (1..254).map { i ->
                        async { if (responde("$base$i", TIEMPO_SONDEO)) "$base$i" else null }
                    }
                }
                trabajos.awaitAll().firstOrNull { it != null }
            }
            if (encontrado != null) {
                prefs.edit().putString(CLAVE_SERVIDOR, encontrado).apply()
                conectar(encontrado)
            } else {
                estado.text = "No lo encontré. ¿Está el Analista abierto en el ordenador?"
            }
        }
    }

    private fun probarYConectar(ip: String) {
        estado.text = "Probando $ip…"
        alcance.launch {
            val ok = withContext(Dispatchers.IO) { responde(ip, 2500) }
            if (ok) {
                prefs.edit().putString(CLAVE_SERVIDOR, ip).apply()
                conectar(ip)
            } else {
                estado.text = "No responde en $ip:$PUERTO."
            }
        }
    }

    /**
     * Sondea `/api/vivo`, NO `/api/estado`.
     *
     * `/api/estado` consulta el servidor de modelos por la red y tarda lo suyo.
     * Al barrer 254 direcciones se le mandaban 254 peticiones caras al propio
     * ordenador, que se quedaba atascado atendiendolas y la app se veia
     * congelada. `/api/vivo` responde al instante y no toca nada pesado.
     */
    private fun responde(ip: String, tiempo: Int): Boolean = try {
        val con = URL("http://$ip:$PUERTO/api/vivo").openConnection() as HttpURLConnection
        con.connectTimeout = tiempo
        con.readTimeout = tiempo
        con.requestMethod = "GET"
        val codigo = con.responseCode
        con.disconnect()
        codigo == 200
    } catch (_: Exception) {
        false
    }

    /**
     * Devuelve TODAS las subredes locales del telefono, como "192.168.1.".
     *
     * Antes se cogia solo la primera IPv4 no-loopback. Con los datos moviles
     * encendidos esa podia ser la interfaz del operador, asi que se barria la
     * subred de la operadora y el ordenador no aparecia jamas. Ahora se miran
     * todas las interfaces (WiFi, cable por USB/Ethernet, tethering) y se
     * descartan las que no son de red local.
     */
    private fun prefijosRed(): List<String> = try {
        NetworkInterface.getNetworkInterfaces().toList()
            .filter { it.isUp && !it.isLoopback }
            .flatMap { it.inetAddresses.toList() }
            .filterIsInstance<Inet4Address>()
            .filter { !it.isLoopbackAddress && it.isSiteLocalAddress }
            .mapNotNull { it.hostAddress?.substringBeforeLast('.')?.plus(".") }
            .distinct()
    } catch (_: Exception) {
        emptyList()
    }

    private fun conectar(ip: String) {
        panelConexion.visibility = View.GONE
        web.visibility = View.VISIBLE
        web.loadUrl("http://$ip:$PUERTO/")
    }

    // ── Navegación ──────────────────────────────────────────────────────────
    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        // En modo peque no debe poder salirse con el boton atras.
        if (web.visibility == View.VISIBLE && web.canGoBack()) web.goBack()
        else super.onBackPressed()
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) pantallaCompleta()
    }

    private fun pantallaCompleta() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            window.insetsController?.hide(android.view.WindowInsets.Type.systemBars())
        } else {
            @Suppress("DEPRECATION")
            window.decorView.systemUiVisibility =
                View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY or
                        View.SYSTEM_UI_FLAG_FULLSCREEN or
                        View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
        }
    }

    override fun onDestroy() {
        alcance.cancel()
        super.onDestroy()
    }
}
