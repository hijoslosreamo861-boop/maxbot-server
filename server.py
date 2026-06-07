import os
import json
import asyncio
import logging
import requests as req
from aiohttp import web

# ── CONFIG ──────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
ADMIN_ID       = os.environ.get("ADMIN_ID", "")
ADMIN_KEY      = os.environ.get("ADMIN_KEY", "MAX2000SECRET123")
PORT           = int(os.environ.get("PORT", 8080))
TG_API         = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── BASE DE DATOS ────────────────────────────────────────
DB_FILE = "licencias.json"

def db_cargar():
    if not os.path.exists(DB_FILE):
        db_guardar({"licencias": {}})
    with open(DB_FILE) as f:
        return json.load(f)

def db_guardar(data):
    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=2)

# ── TELEGRAM ─────────────────────────────────────────────
def tg_send(chat_id, text):
    try:
        req.post(f"{TG_API}/sendMessage", json={
            "chat_id": chat_id, "text": text, "parse_mode": "Markdown"
        }, timeout=10)
    except Exception as e:
        log.error(f"Error enviando mensaje: {e}")

def tg_get_updates(offset=None):
    try:
        params = {"timeout": 30, "allowed_updates": ["message"]}
        if offset:
            params["offset"] = offset
        r = req.get(f"{TG_API}/getUpdates", params=params, timeout=35)
        return r.json().get("result", [])
    except:
        return []

# ── CLIENTES CONECTADOS ──────────────────────────────────
clientes = {}  # token → websocket

# ── COMANDOS ─────────────────────────────────────────────
import secrets
from datetime import datetime, timedelta

def generar_token():
    return secrets.token_hex(8).upper()

def procesar_comando(chat_id, texto):
    if str(chat_id) != str(ADMIN_ID):
        tg_send(chat_id, "⛔ No autorizado.")
        return

    partes = texto.strip().split()
    cmd = partes[0].lower()

    if cmd == "/start":
        tg_send(chat_id,
            "◆ *MAXBOT — INDICADORMAX2000* ◆\n\n"
            "/nuevo NOMBRE — Crear licencia\n"
            "/lista — Ver licencias\n"
            "/activar TOKEN — Activar\n"
            "/desactivar TOKEN — Desactivar\n"
            "/borrar TOKEN — Eliminar\n"
            "/estado — Estado del servidor"
        )

    elif cmd == "/nuevo":
        if len(partes) < 2:
            tg_send(chat_id, "Uso: /nuevo NOMBRE"); return
        nombre = " ".join(partes[1:])
        token  = generar_token()
        vence  = (datetime.now() + timedelta(days=30)).isoformat()
        db = db_cargar()
        db["licencias"][token] = {"nombre": nombre, "activa": True, "creada": datetime.now().isoformat(), "vence": vence}
        db_guardar(db)
        tg_send(chat_id, f"✅ Licencia creada para *{nombre}*\n\n🔑 Token:\n`{token}`\n\nVence: {vence[:10]}")

    elif cmd == "/lista":
        db = db_cargar()
        if not db["licencias"]:
            tg_send(chat_id, "Sin licencias. Usá /nuevo NOMBRE"); return
        msg = "📋 *LICENCIAS:*\n\n"
        for token, lic in db["licencias"].items():
            online  = "🟢 ONLINE" if token in clientes else "⚫ OFFLINE"
            estado  = "✅" if lic["activa"] else "🔴"
            msg += f"{estado} *{lic['nombre']}*\n`{token}`\n{online} | Vence: {lic['vence'][:10]}\n\n"
        tg_send(chat_id, msg)

    elif cmd == "/activar":
        if len(partes) < 2:
            tg_send(chat_id, "Uso: /activar TOKEN"); return
        token = partes[1].upper()
        db = db_cargar()
        if token not in db["licencias"]:
            tg_send(chat_id, "❌ Token no encontrado."); return
        db["licencias"][token]["activa"] = True
        db["licencias"][token]["vence"] = (datetime.now() + timedelta(days=30)).isoformat()
        db_guardar(db)
        tg_send(chat_id, f"✅ Licencia de *{db['licencias'][token]['nombre']}* activada por 30 días.")

    elif cmd == "/desactivar":
        if len(partes) < 2:
            tg_send(chat_id, "Uso: /desactivar TOKEN"); return
        token = partes[1].upper()
        db = db_cargar()
        if token not in db["licencias"]:
            tg_send(chat_id, "❌ Token no encontrado."); return
        db["licencias"][token]["activa"] = False
        db_guardar(db)
        nombre = db["licencias"][token]["nombre"]
        if token in clientes:
            asyncio.create_task(clientes[token].send_str(json.dumps({"tipo": "licencia_desactivada"})))
        tg_send(chat_id, f"🔴 Licencia de *{nombre}* desactivada.")

    elif cmd == "/borrar":
        if len(partes) < 2:
            tg_send(chat_id, "Uso: /borrar TOKEN"); return
        token = partes[1].upper()
        db = db_cargar()
        if token not in db["licencias"]:
            tg_send(chat_id, "❌ Token no encontrado."); return
        nombre = db["licencias"][token]["nombre"]
        del db["licencias"][token]
        db_guardar(db)
        tg_send(chat_id, f"🗑️ Licencia de *{nombre}* eliminada.")

    elif cmd == "/estado":
        db = db_cargar()
        total   = len(db["licencias"])
        activas = sum(1 for l in db["licencias"].values() if l["activa"])
        online  = len(clientes)
        tg_send(chat_id, f"📊 *ESTADO*\n\nLicencias: {total}\nActivas: {activas}\nOnline: {online}")

# ── POLLING DE TELEGRAM EN BACKGROUND ───────────────────
async def telegram_polling():
    offset = None
    log.info("Iniciando polling de Telegram...")
    while True:
        try:
            updates = await asyncio.get_event_loop().run_in_executor(None, lambda: tg_get_updates(offset))
            for update in updates:
                offset = update["update_id"] + 1
                msg = update.get("message", {})
                chat_id = msg.get("chat", {}).get("id")
                texto   = msg.get("text", "")
                if chat_id and texto:
                    procesar_comando(chat_id, texto)
        except Exception as e:
            log.error(f"Error polling: {e}")
            await asyncio.sleep(5)

# ── WEBHOOK TRADINGVIEW ──────────────────────────────────
async def handle_webhook(request):
    key = request.headers.get("x-admin-key", "")
    if key != ADMIN_KEY:
        return web.Response(status=401, text="No autorizado")
    try:
        señal = await request.json()
    except:
        return web.Response(status=400, text="JSON invalido")

    accion = señal.get("action", "")
    ticker = señal.get("ticker", "")
    log.info(f"Señal: {accion} {ticker}")

    tg_send(ADMIN_ID, f"📡 Señal: *{accion.upper()}* {ticker}")

    db = db_cargar()
    enviados = 0
    for token, ws in list(clientes.items()):
        lic = db["licencias"].get(token)
        if not lic or not lic["activa"]:
            continue
        if datetime.now() > datetime.fromisoformat(lic["vence"]):
            continue
        try:
            await ws.send_str(json.dumps({"tipo": "señal", "accion": accion, "ticker": ticker, "precio": señal.get("price", "")}))
            enviados += 1
        except:
            if token in clientes:
                del clientes[token]

    return web.json_response({"ok": True, "enviados": enviados})

# ── WEBSOCKET AGENTES ────────────────────────────────────
async def handle_ws(request):
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    token_cliente = None

    async for msg in ws:
        if msg.type == web.WSMsgType.TEXT:
            try:
                data = json.loads(msg.data)
            except:
                continue
            if data.get("tipo") == "auth":
                token = data.get("token", "").upper()
                db    = db_cargar()
                lic   = db["licencias"].get(token)
                if not lic:
                    await ws.send_str(json.dumps({"tipo": "error", "msg": "Token invalido"}))
                    await ws.close(); break
                if not lic["activa"]:
                    await ws.send_str(json.dumps({"tipo": "error", "msg": "Licencia desactivada"}))
                    await ws.close(); break
                if datetime.now() > datetime.fromisoformat(lic["vence"]):
                    await ws.send_str(json.dumps({"tipo": "error", "msg": "Licencia vencida"}))
                    await ws.close(); break
                token_cliente = token
                clientes[token] = ws
                await ws.send_str(json.dumps({"tipo": "autenticado", "nombre": lic["nombre"], "vence": lic["vence"][:10]}))
                log.info(f"Cliente conectado: {lic['nombre']}")
                tg_send(ADMIN_ID, f"🟢 *{lic['nombre']}* se conectó")

    if token_cliente and token_cliente in clientes:
        nombre = db_cargar()["licencias"].get(token_cliente, {}).get("nombre", token_cliente)
        del clientes[token_cliente]
        log.info(f"Cliente desconectado: {nombre}")

    return ws

# ── MAIN ─────────────────────────────────────────────────
async def main():
    app = web.Application()
    app.router.add_post("/webhook", handle_webhook)
    app.router.add_get("/ws", handle_ws)
    app.router.add_get("/", lambda r: web.Response(text="MaxBot OK"))

    asyncio.create_task(telegram_polling())

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    log.info(f"Servidor corriendo en puerto {PORT}")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
