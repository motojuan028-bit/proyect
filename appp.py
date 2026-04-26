"""
Sistema de Turnos Digitales — TurnoUni
Backend: Flask + Supabase (PostgreSQL)

Variables de entorno necesarias (configura en Railway):
  SUPABASE_URL      → URL de tu proyecto, ej: https://xxxx.supabase.co
  SUPABASE_KEY      → service_role key (Settings → API → service_role)
  PORT              → Railway lo asigna automáticamente
"""

from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from supabase import create_client, Client
from datetime import datetime, timezone
import qrcode
from io import BytesIO
import os

app = Flask(__name__)
CORS(app)

# ── Supabase client ────────────────────────────────────────────────────────────
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError(
        "Faltan variables de entorno SUPABASE_URL y SUPABASE_KEY.\n"
        "Configúralas en Railway → Variables."
    )

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


def hoy_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


# ── Login ──────────────────────────────────────────────────────────────────────

@app.route("/auth/login", methods=["POST"])
def login():
    data     = request.json or {}
    codigo   = str(data.get("codigo", "")).strip()
    password = str(data.get("password", "")).strip()

    res = (
        supabase.table("usuarios")
        .select("*")
        .eq("codigo", codigo)
        .eq("password", password)
        .execute()
    )
    usuario = res.data[0] if res.data else None

    if not usuario:
        return jsonify({"error": "Código o contraseña incorrectos"}), 401

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

    query = supabase.table("franjas").select("*").order("id")
    if tipo_filtro:
        query = query.eq("tipo", tipo_filtro)
    franjas_res = query.execute().data

    turnos_hoy = (
        supabase.table("turnos")
        .select("franja_id")
        .eq("cancelado", False)
        .gte("fecha_reserva", f"{hoy}T00:00:00+00:00")
        .lte("fecha_reserva", f"{hoy}T23:59:59+00:00")
        .execute()
        .data
    )
    usados_por_franja = {}
    for t in turnos_hoy:
        fid = t["franja_id"]
        usados_por_franja[fid] = usados_por_franja.get(fid, 0) + 1

    resultado = []
    for f in franjas_res:
        usados      = usados_por_franja.get(f["id"], 0)
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

    # ¿Ya tiene turno hoy?
    turno_hoy = (
        supabase.table("turnos")
        .select("id")
        .eq("codigo_usuario", codigo)
        .eq("cancelado", False)
        .gte("fecha_reserva", f"{hoy}T00:00:00+00:00")
        .lte("fecha_reserva", f"{hoy}T23:59:59+00:00")
        .execute()
        .data
    )
    if turno_hoy:
        return jsonify({"error": "Ya tienes un turno activo para hoy"}), 400

    # Verificar franja y cupos
    franja_res = (
        supabase.table("franjas")
        .select("*")
        .eq("id", franja_id)
        .execute()
        .data
    )
    franja = franja_res[0] if franja_res else None
    if not franja:
        return jsonify({"error": "Franja no encontrada"}), 404

    usados = len(
        supabase.table("turnos")
        .select("id")
        .eq("franja_id", franja_id)
        .eq("cancelado", False)
        .gte("fecha_reserva", f"{hoy}T00:00:00+00:00")
        .lte("fecha_reserva", f"{hoy}T23:59:59+00:00")
        .execute()
        .data
    )
    if usados >= franja["cupos_max"]:
        return jsonify({"error": "No hay cupos disponibles en esta franja"}), 400

    nuevo = (
        supabase.table("turnos")
        .insert({
            "codigo_usuario": codigo,
            "franja_id":      franja_id,
            "tipo":           tipo,
            "cancelado":      False,
        })
        .execute()
        .data[0]
    )

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

    turno_res = (
        supabase.table("turnos")
        .select("*, franjas(hora_inicio, hora_fin)")
        .eq("codigo_usuario", codigo)
        .eq("cancelado", False)
        .gte("fecha_reserva", f"{hoy}T00:00:00+00:00")
        .lte("fecha_reserva", f"{hoy}T23:59:59+00:00")
        .execute()
        .data
    )
    turno = turno_res[0] if turno_res else None

    if not turno:
        return jsonify({"turno": None})

    franja = turno.get("franjas") or {}
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
    supabase.table("turnos").update({"cancelado": True}).eq("id", turno_id).execute()
    return jsonify({"ok": True})


# ── Frontend ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_file("index.html")


# ── Run ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"TurnoUni corriendo en http://0.0.0.0:{port}")
    app.run(debug=False, host="0.0.0.0", port=port)
