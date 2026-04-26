"""
TurnoUni — Sistema de Turnos Digitales
Backend: Flask + Supabase REST API directo (sin SDK)
"""

from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from datetime import datetime, timezone
import qrcode
import requests
from io import BytesIO
import os

app = Flask(__name__)
CORS(app)

# ── Supabase config ────────────────────────────────────────────────────────────
SUPABASE_URL = "https://jgjlmkhcmtflowxdfaly.supabase.co/rest/v1"
SUPABASE_KEY = "sb_secret_5B2sNm2aQlhvDxv_LYZuAg__YcZGmND"

HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=representation",
}


def db_get(tabla, params=None):
    """GET — devuelve lista de filas."""
    r = requests.get(f"{SUPABASE_URL}/{tabla}", headers=HEADERS, params=params or {})
    r.raise_for_status()
    return r.json()


def db_post(tabla, payload):
    """POST — inserta una fila y devuelve la fila creada."""
    r = requests.post(f"{SUPABASE_URL}/{tabla}", headers=HEADERS, json=payload)
    r.raise_for_status()
    data = r.json()
    return data[0] if isinstance(data, list) else data


def db_patch(tabla, params, payload):
    """PATCH — actualiza filas que cumplan params."""
    r = requests.patch(f"{SUPABASE_URL}/{tabla}", headers=HEADERS, params=params, json=payload)
    r.raise_for_status()
    return r.json()


def hoy_iso():
    return datetime.now(timezone.utc).date().isoformat()


# ── Login ──────────────────────────────────────────────────────────────────────

@app.route("/auth/login", methods=["POST"])
def login():
    data     = request.json or {}
    codigo   = str(data.get("codigo", "")).strip()
    password = str(data.get("password", "")).strip()

    try:
        rows = db_get("usuarios", {
            "codigo":   f"eq.{codigo}",
            "password": f"eq.{password}",
            "select":   "*",
        })
    except Exception as e:
        return jsonify({"error": f"Error de base de datos: {str(e)}"}), 500

    if not rows:
        return jsonify({"error": "Código o contraseña incorrectos"}), 401

    usuario = rows[0]

    if str(usuario.get("activo", "SI")).upper() != "SI":
        return jsonify({"error": "Tu acceso está desactivado. Contacta a bienestar universitario."}), 403

    return jsonify({
        "usuario": {
            "codigo":   usuario["codigo"],
            "nombre":   usuario["nombre"],
            "programa": usuario["programa"],
        }
    })


# ── Franjas ────────────────────────────────────────────────────────────────────

@app.route("/franjas")
def franjas():
    tipo_filtro = request.args.get("tipo", None)
    hoy         = hoy_iso()

    params = {"select": "*", "order": "id.asc"}
    if tipo_filtro:
        params["tipo"] = f"eq.{tipo_filtro}"

    try:
        franjas_res = db_get("franjas", params)
        turnos_hoy  = db_get("turnos", {
            "select":        "franja_id",
            "cancelado":     "eq.false",
            "fecha_reserva": f"gte.{hoy}T00:00:00+00:00",
            "fecha_reserva": f"lte.{hoy}T23:59:59+00:00",
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    # Contar usados por franja
    usados_map = {}
    for t in turnos_hoy:
        fid = t["franja_id"]
        usados_map[fid] = usados_map.get(fid, 0) + 1

    resultado = []
    for f in franjas_res:
        usados      = usados_map.get(f["id"], 0)
        disponibles = max(0, f["cupos_max"] - usados)
        resultado.append({
            "id":                f["id"],
            "tipo":              f["tipo"],
            "hora_inicio":       f["hora_inicio"],
            "hora_fin":          f["hora_fin"],
            "cupos_max":         f["cupos_max"],
            "cupos_disponibles": disponibles,
        })

    return jsonify({"franjas": resultado})


# ── Crear turno ────────────────────────────────────────────────────────────────

@app.route("/turnos", methods=["POST"])
def crear_turno():
    data      = request.json or {}
    franja_id = data.get("franja_id")
    tipo      = data.get("tipo", "almuerzo")
    codigo    = str(data.get("codigo_usuario", "")).strip()
    hoy       = hoy_iso()

    try:
        # ¿Ya tiene turno hoy?
        turno_hoy = db_get("turnos", {
            "select":          "id",
            "codigo_usuario":  f"eq.{codigo}",
            "cancelado":       "eq.false",
            "fecha_reserva":   f"gte.{hoy}T00:00:00+00:00",
        })
        if turno_hoy:
            return jsonify({"error": "Ya tienes un turno activo para hoy"}), 400

        # Verificar franja
        franja_res = db_get("franjas", {"select": "*", "id": f"eq.{franja_id}"})
        if not franja_res:
            return jsonify({"error": "Franja no encontrada"}), 404
        franja = franja_res[0]

        # Verificar cupos
        usados_res = db_get("turnos", {
            "select":        "id",
            "franja_id":     f"eq.{franja_id}",
            "cancelado":     "eq.false",
            "fecha_reserva": f"gte.{hoy}T00:00:00+00:00",
        })
        if len(usados_res) >= franja["cupos_max"]:
            return jsonify({"error": "No hay cupos disponibles en esta franja"}), 400

        # Insertar turno
        nuevo = db_post("turnos", {
            "codigo_usuario": codigo,
            "franja_id":      franja_id,
            "tipo":           tipo,
            "cancelado":      False,
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({
        "turno": {
            "id":        nuevo["id"],
            "token":     str(nuevo["token"]),
            "tipo":      nuevo["tipo"],
            "cancelado": False,
            "franja": {
                "hora_inicio": franja["hora_inicio"],
                "hora_fin":    franja["hora_fin"],
            },
        }
    }), 201


# ── Mi turno ───────────────────────────────────────────────────────────────────

@app.route("/turnos/mi-turno")
def mi_turno():
    codigo = request.args.get("codigo", "")
    hoy    = hoy_iso()

    try:
        turnos = db_get("turnos", {
            "select":         "*",
            "codigo_usuario": f"eq.{codigo}",
            "cancelado":      "eq.false",
            "fecha_reserva":  f"gte.{hoy}T00:00:00+00:00",
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    if not turnos:
        return jsonify({"turno": None})

    turno = turnos[0]

    try:
        franja_res = db_get("franjas", {"select": "*", "id": f"eq.{turno['franja_id']}"})
        franja     = franja_res[0] if franja_res else {}
    except Exception:
        franja = {}

    return jsonify({
        "turno": {
            "id":        turno["id"],
            "token":     str(turno["token"]),
            "tipo":      turno["tipo"],
            "cancelado": turno["cancelado"],
            "franja": {
                "hora_inicio": franja.get("hora_inicio", ""),
                "hora_fin":    franja.get("hora_fin", ""),
            },
        }
    })


# ── QR ─────────────────────────────────────────────────────────────────────────

@app.route("/turnos/<token>/qr")
def qr(token):
    img    = qrcode.make(token)
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return send_file(buffer, mimetype="image/png")


# ── Cancelar ───────────────────────────────────────────────────────────────────

@app.route("/turnos/<int:turno_id>/cancelar", methods=["POST"])
def cancelar(turno_id):
    try:
        db_patch("turnos", {"id": f"eq.{turno_id}"}, {"cancelado": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"ok": True})


# ── Frontend ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_file("index.html")


# ── Healthcheck ────────────────────────────────────────────────────────────────

@app.route("/health")
def health():
    return jsonify({"status": "ok"})


# ── Run ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(debug=False, host="0.0.0.0", port=port)
