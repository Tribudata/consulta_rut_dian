"""
Backend Python - Consulta Estado RUT DIAN (Muisca)
=====================================================
Selectores XPath e IDs exactos extraídos del HTML real del portal.

Instalación:
    pip install flask flask-cors requests beautifulsoup4

Ejecución:
    python backend_dian.py

Servidor: http://localhost:5000
Prueba directa (sin servidor Flask):
    python backend_dian.py --test 900123456
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
from datetime import datetime
import requests
from bs4 import BeautifulSoup
import logging
import re
import warnings

warnings.filterwarnings("ignore")  # suprimir advertencias SSL

app = Flask(__name__)

# ── CORS: acepta cualquier origen incluyendo iframes de Google Sites ──
@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"]  = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Requested-With"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Max-Age"]       = "86400"
    return response

# ── Preflight OPTIONS — debe responder 200 (no 204) para Google Sites ──
@app.route("/consultar-rut", methods=["OPTIONS"])
@app.route("/diagnostico",   methods=["OPTIONS"])
@app.route("/ping",          methods=["OPTIONS"])
def handle_options():
    from flask import Response
    r = Response("", status=200)
    r.headers["Access-Control-Allow-Origin"]  = "*"
    r.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Requested-With"
    r.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    r.headers["Access-Control-Max-Age"]       = "86400"
    return r

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────
# IMPORTANTE: usar http:// — NO https://
# El portal Muisca usa http internamente. Con https redirige al portal
# principal de la DIAN (www.dian.gov.co) y el scraping falla.
# ─────────────────────────────────────────────────────────────────────
URL_DIAN = "http://muisca.dian.gov.co/WebRutMuisca/DefConsultaEstadoRUT.faces"

# ─────────────────────────────────────────────────────────────────────
# SELECTORES — IDs exactos del HTML del portal Muisca
# Prefijo común: vistaConsultaEstadoRUT:formConsultaEstadoRUT:
# ─────────────────────────────────────────────────────────────────────
PREFIJO = "vistaConsultaEstadoRUT:formConsultaEstadoRUT:"

SEL = {
    # ── Entrada ──────────────────────────────────────────────────────
    "campo_nit":    f"{PREFIJO}numNit",           # input text donde se digita el NIT
    "boton_buscar": f"{PREFIJO}btnBuscar",         # input type=image que dispara la consulta

    # ── Campos comunes (persona natural y jurídica) ───────────────────
    "nit_resultado": f"{PREFIJO}numNit",           # input con el NIT consultado (value=)
    "dv":            f"{PREFIJO}dv",               # span con dígito de verificación
    "estado":        f"{PREFIJO}estado",           # span con estado del RUT

    # ── Solo persona NATURAL ─────────────────────────────────────────
    "primer_apellido":  f"{PREFIJO}primerApellido",
    "segundo_apellido": f"{PREFIJO}segundoApellido",
    "primer_nombre":    f"{PREFIJO}primerNombre",
    "otros_nombres":    f"{PREFIJO}otrosNombres",

    # ── Solo persona JURÍDICA ────────────────────────────────────────
    "razon_social": f"{PREFIJO}razonSocial",

    # ── Campos por clase CSS (fecha, registro activo) ────────────────
    "fecha_consulta":  "tipoFilaNormalVerde",       # td.tipoFilaNormalVerde
    "registro_activo": "fondoTituloLeftAjustado",   # td.fondoTituloLeftAjustado
}

# XPath equivalentes (para referencia y uso con lxml si se prefiere)
XPATH = {
    "campo_nit":         f'//input[@id="{SEL["campo_nit"]}"]',
    "boton_buscar":      f'//input[@id="{SEL["boton_buscar"]}"]',
    "dv":                f'//span[@id="{SEL["dv"]}"]',
    "estado":            f'//span[@id="{SEL["estado"]}"]',
    "primer_apellido":   f'//span[@id="{SEL["primer_apellido"]}"]',
    "segundo_apellido":  f'//span[@id="{SEL["segundo_apellido"]}"]',
    "primer_nombre":     f'//span[@id="{SEL["primer_nombre"]}"]',
    "otros_nombres":     f'//span[@id="{SEL["otros_nombres"]}"]',
    "razon_social":      f'//span[@id="{SEL["razon_social"]}"]',
    "fecha_consulta":    '//td[@class="tipoFilaNormalVerde"]',
    "registro_activo":   '//td[@class="fondoTituloLeftAjustado"]',
    "viewstate":         '//input[@name="javax.faces.ViewState"]',
}

HEADERS_BASE = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-CO,es;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Connection":      "keep-alive",
    "Cache-Control":   "no-cache",
    "Pragma":          "no-cache",
}


# ─────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────

def crear_sesion() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS_BASE)
    s.verify = False
    return s


def texto(soup: BeautifulSoup, id_: str) -> str:
    """Extrae texto de un elemento por su id exacto."""
    el = soup.find(id=id_)
    return el.get_text(strip=True) if el else ""


def attr(soup: BeautifulSoup, id_: str, atributo: str) -> str:
    """Extrae un atributo de un elemento por su id exacto."""
    el = soup.find(id=id_)
    return (el.get(atributo) or "").strip() if el else ""


def texto_clase(soup: BeautifulSoup, clase: str) -> str:
    """Extrae texto del primer elemento que tenga esa clase CSS."""
    el = soup.find(class_=clase)
    return el.get_text(strip=True) if el else ""


def obtener_viewstate(soup: BeautifulSoup) -> str:
    """Extrae javax.faces.ViewState del formulario JSF."""
    vs = soup.find("input", {"name": "javax.faces.ViewState"})
    if vs and vs.get("value"):
        return vs["value"]
    for script in soup.find_all("script"):
        m = re.search(r'javax\.faces\.ViewState["\s]*[=:]["\s]*([^"&\s;]+)', script.string or "")
        if m:
            return m.group(1)
    raise ValueError("No se encontró javax.faces.ViewState. La sesión no se inició correctamente.")


# ─────────────────────────────────────────────────────────────────────
# FLUJO PRINCIPAL
# ─────────────────────────────────────────────────────────────────────

def consultar_rut_dian(num_doc: str) -> dict:
    """
    1. GET  → iniciar sesión y capturar ViewState + cookies
    2. POST → enviar NIT usando los IDs exactos del formulario
    3. Parse→ extraer campos con los selectores definidos en SEL{}
    """
    session = crear_sesion()

    # ── 1. GET inicial ───────────────────────────────────────────────
    log.info(f"[GET] {URL_DIAN}")
    try:
        r_get = session.get(URL_DIAN, timeout=20)
    except requests.exceptions.ConnectionError as e:
        raise ConnectionError(f"No se pudo conectar al portal DIAN: {e}")

    url_final = r_get.url
    log.info(f"[GET] URL final: {url_final} | Status: {r_get.status_code}")

    # Detectar redirección no deseada al portal principal
    if "www.dian.gov.co" in url_final or "/Paginas/" in url_final:
        raise RuntimeError(
            "El servidor redirigió al portal principal de la DIAN. "
            "Asegúrese de usar http:// (no https://) y que Muisca esté disponible."
        )

    if r_get.status_code != 200:
        raise RuntimeError(f"GET devolvió status {r_get.status_code}")

    soup_get = BeautifulSoup(r_get.content, "html.parser", from_encoding="iso-8859-1")
    view_state = obtener_viewstate(soup_get)
    log.info(f"[GET] ViewState: {view_state[:40]}...")

    # Verificar que el campo NIT existe en el formulario
    campo_nit_el = soup_get.find(id=SEL["campo_nit"])
    if not campo_nit_el:
        log.warning(f"[GET] Campo NIT '{SEL['campo_nit']}' no encontrado — usando nombre directo.")

    # ── 2. POST con NIT ──────────────────────────────────────────────
    # El botón es type=image; JSF requiere enviar sus coordenadas de clic
    post_data = {
        SEL["campo_nit"]:           num_doc,
        SEL["boton_buscar"] + ".x": "1",    # coordenada X del clic en la imagen
        SEL["boton_buscar"] + ".y": "1",    # coordenada Y del clic en la imagen
        "javax.faces.ViewState":    view_state,
        # Incluir el id del form como lo hace JSF
        f"{PREFIJO[:-1]}":          f"{PREFIJO[:-1]}",
    }

    post_headers = {
        **HEADERS_BASE,
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer":       URL_DIAN,
        "Origin":        "http://muisca.dian.gov.co",
    }

    log.info(f"[POST] NIT: {num_doc} | campos: {list(post_data.keys())}")

    r_post = session.post(
        URL_DIAN,
        data=post_data,
        headers=post_headers,
        timeout=25,
        allow_redirects=False,   # detectar redirección inesperada
    )

    # Seguir redirección solo si es dentro de Muisca
    if r_post.status_code in (301, 302, 303, 307, 308):
        loc = r_post.headers.get("Location", "")
        log.warning(f"[POST] Redirigido a: {loc}")
        if "muisca.dian.gov.co" in loc or loc.startswith("/"):
            url_redir = loc if loc.startswith("http") else "http://muisca.dian.gov.co" + loc
            r_post = session.get(url_redir, headers=post_headers, timeout=20)
        else:
            raise RuntimeError(
                f"Redirección externa no esperada: {loc}. "
                "El portal puede estar en mantenimiento."
            )

    if r_post.status_code != 200:
        raise RuntimeError(f"POST devolvió status {r_post.status_code}")

    # ── 3. Parsear con selectores exactos ───────────────────────────
    soup_res = BeautifulSoup(r_post.content, "html.parser", from_encoding="iso-8859-1")
    return parsear_resultado(soup_res, num_doc)


def parsear_resultado(soup: BeautifulSoup, num_doc: str) -> dict:
    """
    Extrae todos los campos usando los IDs exactos definidos en SEL{}.

    Detecta el tipo de persona según si existe razonSocial o primerNombre.
    """

    # ── Campos comunes ───────────────────────────────────────────────
    dv             = texto(soup, SEL["dv"])
    estado         = texto(soup, SEL["estado"])
    fecha_consulta = texto_clase(soup, SEL["fecha_consulta"])
    registro_activo= texto_clase(soup, SEL["registro_activo"])

    # ── Detectar tipo de persona ─────────────────────────────────────
    razon_social    = texto(soup, SEL["razon_social"])
    primer_apellido = texto(soup, SEL["primer_apellido"])
    primer_nombre   = texto(soup, SEL["primer_nombre"])

    es_juridica = bool(razon_social)
    es_natural  = bool(primer_apellido or primer_nombre)

    # ── Validar que obtuvimos resultado real ─────────────────────────
    if not estado and not razon_social and not primer_apellido:
        page_text = soup.get_text(" ", strip=True).lower()
        if "no se encontr" in page_text or "no existe" in page_text:
            raise ValueError(f"NIT {num_doc} no encontrado en el RUT de la DIAN.")
        if "mantenimiento" in page_text:
            raise RuntimeError("El portal DIAN está en mantenimiento.")
        if "www.dian.gov.co" in page_text or "portal dian" in page_text.lower():
            raise RuntimeError(
                "La respuesta corresponde al portal principal de la DIAN, no al formulario. "
                "Revise la URL (debe ser http://) y reintente."
            )
        raise ValueError(
            "No se encontraron campos en la respuesta. "
            "El portal puede haber cambiado su estructura HTML."
        )

    # ── Construir resultado según tipo de persona ────────────────────
    resultado = {
        "numDoc":         num_doc,
        "dv":             dv,
        "estado":         estado.upper() if estado else "DESCONOCIDO",
        "fechaConsulta":  fecha_consulta or datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        "registroActivo": registro_activo,
    }

    if es_juridica:
        resultado["tipoPersona"] = "JURÍDICA"
        resultado["razonSocial"] = razon_social
        resultado["nombre"]      = razon_social

    elif es_natural:
        segundo_apellido = texto(soup, SEL["segundo_apellido"])
        otros_nombres    = texto(soup, SEL["otros_nombres"])

        nombre_completo = " ".join(filter(None, [
            primer_apellido, segundo_apellido,
            primer_nombre,   otros_nombres
        ]))

        resultado["tipoPersona"]     = "NATURAL"
        resultado["primerApellido"]  = primer_apellido
        resultado["segundoApellido"] = segundo_apellido
        resultado["primerNombre"]    = primer_nombre
        resultado["otrosNombres"]    = otros_nombres
        resultado["nombre"]          = nombre_completo

    else:
        resultado["tipoPersona"] = "DESCONOCIDO"
        resultado["nombre"]      = "No disponible"

    log.info(f"[PARSE] Resultado: {resultado}")
    return resultado


# ─────────────────────────────────────────────────────────────────────
# ENDPOINTS FLASK
# ─────────────────────────────────────────────────────────────────────

@app.route("/consultar-rut", methods=["POST"])
def endpoint_consultar():
    body   = request.get_json(force=True)
    numDoc = (body.get("numDoc") or "").strip().replace(".", "").replace("-", "")

    if not numDoc:
        return jsonify({"error": "El campo numDoc es requerido."}), 400
    if not numDoc.isdigit():
        return jsonify({"error": "El NIT solo debe contener dígitos (sin puntos ni guiones)."}), 400
    if len(numDoc) < 5:
        return jsonify({"error": "NIT demasiado corto. Verifica el número ingresado."}), 400

    try:
        resultado = consultar_rut_dian(numDoc)
        return jsonify(resultado)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except (ConnectionError, RuntimeError) as e:
        log.error(f"Error DIAN: {e}")
        return jsonify({"error": str(e)}), 503
    except Exception as e:
        log.exception("Error inesperado")
        return jsonify({"error": f"Error interno: {str(e)}"}), 500


@app.route("/", methods=["GET"])
def index():
    """Sirve la interfaz HTML directamente desde Railway — evita problemas de CORS con iframes."""
    import os
    html_path = os.path.join(os.path.dirname(__file__), "consulta_rut_dian.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return f.read(), 200, {"Content-Type": "text/html; charset=utf-8"}
    return "<h2>Sube el archivo consulta_rut_dian.html al repositorio</h2>", 404


@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"status": "ok", "timestamp": datetime.now().isoformat()})


@app.route("/diagnostico", methods=["GET"])
def diagnostico():
    """
    Endpoint de diagnóstico: verifica la conectividad con el portal DIAN.
    Útil para detectar si el servidor cloud puede alcanzar muisca.dian.gov.co.
    Visitar: https://TU-APP.railway.app/diagnostico
    """
    import socket
    resultado = {
        "timestamp":   datetime.now().isoformat(),
        "url_dian":    URL_DIAN,
        "dns_muisca":  None,
        "get_status":  None,
        "get_url_final": None,
        "viewstate_encontrado": False,
        "campo_nit_encontrado": False,
        "error": None,
    }

    # Verificar resolución DNS
    try:
        ip = socket.gethostbyname("muisca.dian.gov.co")
        resultado["dns_muisca"] = ip
    except Exception as e:
        resultado["dns_muisca"] = f"ERROR DNS: {e}"
        resultado["error"] = str(e)
        return jsonify(resultado), 503

    # Intentar GET al portal
    try:
        session = crear_sesion()
        r = session.get(URL_DIAN, timeout=15)
        resultado["get_status"]    = r.status_code
        resultado["get_url_final"] = r.url

        soup = BeautifulSoup(r.content, "html.parser", from_encoding="iso-8859-1")

        # Verificar ViewState
        vs = soup.find("input", {"name": "javax.faces.ViewState"})
        resultado["viewstate_encontrado"] = bool(vs and vs.get("value"))

        # Verificar campo NIT
        campo = soup.find(id=SEL["campo_nit"])
        resultado["campo_nit_encontrado"] = bool(campo)

        if not resultado["viewstate_encontrado"]:
            resultado["error"] = (
                "ViewState no encontrado. El portal puede haber redirigido "
                "al portal principal o estar bloqueando IPs de datacenter."
            )
    except Exception as e:
        resultado["error"] = str(e)
        log.error(f"[DIAGNOSTICO] {e}")
        return jsonify(resultado), 503

    return jsonify(resultado)


# ─────────────────────────────────────────────────────────────────────
# EJECUCIÓN DIRECTA / PRUEBA
# ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) >= 3 and sys.argv[1] == "--test":
        nit = sys.argv[2]
        print(f"\n🔍 Consulta directa — NIT: {nit}\n{'─'*45}")
        try:
            res = consultar_rut_dian(nit)
            for k, v in res.items():
                print(f"  {k:20s}: {v}")
        except Exception as e:
            print(f"  ❌ Error: {e}")
        print()
    else:
        print("=" * 55)
        print("  Servidor DIAN RUT  →  http://localhost:5000")
        print()
        print("  Endpoints:")
        print("    POST /consultar-rut  { \"numDoc\": \"900123456\" }")
        print("    GET  /ping")
        print()
        print("  Prueba directa sin servidor:")
        print("    python backend_dian.py --test 900123456")
        print("=" * 55)
        app.run(host="0.0.0.0", port=5000, debug=False)
