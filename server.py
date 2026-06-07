"""
╔══════════════════════════════════════════════════════╗
║     MAXBOT SERVER — INDICADORMAX2000                 ║
║     Servidor + Bot de Telegram integrado             ║
╚══════════════════════════════════════════════════════╝

Instalación en Railway:
1. Subir esta carpeta a GitHub
2. Deployar en Railway.app (gratis)
3. Configurar variables de entorno:
   - TELEGRAM_TOKEN = el token de @BotFather
   - ADMIN_ID       = tu ID de Telegram (buscá @userinfobot)
   - ADMIN_KEY      = clave secreta para el webhook
"""

import os
import json
import hmac
import hashlib
import asyncio
import logging
from datetime import datetime, timedelta
from aiohttp import web
from telegram import Update, Bot
from telegram.ext import (
    Application, CommandHandler, ContextTypes, MessageHandler, filters
)

# ────────────────────────────────────────
# CONFIGURACIÓN
# ────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8418198642:AAGfzJC0EtuwD8jD-CCILmA_xn-nvBebYTo")
ADMIN_ID       = int(os.environ.get("ADMIN_ID", "2040847121"))  # Tu ID de Telegram
ADMIN_KEY      = os.environ.get("ADMIN_KEY", "MAX2000SECRET")
PORT           = int(os.environ.get("PORT", 8080))

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO
)
log = logging.getLogger(__name__)

# ────────────────────────────────────────
# BASE DE DATOS (archivo JSON simple)
# ────────────────────────────────────────
DB_FILE = "licencias.json"

def db_cargar():
    if not os.path.exists(DB_FILE):
        data = {"licencias": {}}
        db_guardar(data)
        return data
    with open(DB_FILE, "r") as f:
        return json.load(f)

def db_guardar(data):
    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=2)

def generar_token():
    import secrets
    return secrets.token_hex(8).upper()

# ────────────────────────────────────────
# CLIENTES CONECTADOS
# token → websocket del agente
# ────────────────────────────────────────
clientes = {}  # token → {"ws": ws, "nombre": str}

# ────────────────────────────────────────
# BOT DE TELEGRAM — COMANDOS
# ────────────────────────────────────────
def solo_admin(func):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID:
            await update.message.reply_text("⛔ No autorizado.")
            return
        await func(update, ctx)
    return wrapper

@solo_admin
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "◆ MAXBOT — INDICADORMAX2000 ◆\n\n"
        "Comandos disponibles:\n\n"
        "/nuevo NOMBRE — Crear licencia\n"
        "/lista — Ver todas las licencias\n"
        "/activar TOKEN — Activar licencia\n"
        "/desactivar TOKEN — Desactivar licencia\n"
        "/borrar TOKEN — Eliminar licencia\n"
        "/estado — Ver estado del servidor\n"
    )

@solo_admin
async def cmd_nuevo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Uso: /nuevo NOMBRE\nEjemplo: /nuevo Juan Pérez")
        return

    nombre = " ".join(ctx.args)
    token  = generar_token()
    vence  = (datetime.now() + timedelta(days=30)).isoformat()

    db = db_cargar()
    db["licencias"][token] = {
        "nombre": nombre,
        "activa": True,
        "creada": datetime.now().isoformat(),
        "vence" : vence
    }
    db_guardar(db)

    log.info(f"Licencia creada: {nombre} → {token}")
    await update.message.reply_text(
        f"✅ Licencia creada para *{nombre}*\n\n"
        f"🔑 Token:\n`{token}`\n\n"
        f"📅 Vence: {vence[:10]}\n\n"
        f"Mandále este token al cliente.",
        parse_mode="Markdown"
    )

@solo_admin
async def cmd_lista(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    db = db_cargar()
    licencias = db["licencias"]

    if not licencias:
        await update.message.reply_text("Sin licencias aún. Usá /nuevo NOMBRE")
        return

    msg = "📋 *LICENCIAS ACTIVAS:*\n\n"
    for token, lic in licencias.items():
        online  = "🟢 ONLINE" if token in clientes else "⚫ OFFLINE"
        estado  = "✅ ACTIVA" if lic["activa"] else "🔴 INACTIVA"
        vence   = lic["vence"][:10]
        vencido = datetime.now() > datetime.fromisoformat(lic["vence"])
        expirado = " ⚠️ VENCIDA" if vencido else ""

        msg += (
            f"👤 *{lic['nombre']}*\n"
            f"   Token: `{token}`\n"
            f"   {estado} | {online}{expirado}\n"
            f"   Vence: {vence}\n\n"
        )

    await update.message.reply_text(msg, parse_mode="Markdown")

@solo_admin
async def cmd_activar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Uso: /activar TOKEN")
        return

    token = ctx.args[0].upper()
    db    = db_cargar()

    if token not in db["licencias"]:
        await update.message.reply_text("❌ Token no encontrado.")
        return

    db["licencias"][token]["activa"] = True
    # Extender 30 días desde hoy
    db["licencias"][token]["vence"] = (datetime.now() + timedelta(days=30)).isoformat()
    db_guardar(db)

    nombre = db["licencias"][token]["nombre"]
    log.info(f"Licencia activada: {nombre}")
    await update.message.reply_text(f"✅ Licencia de *{nombre}* activada por 30 días.", parse_mode="Markdown")

@solo_admin
async def cmd_desactivar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Uso: /desactivar TOKEN")
        return

    token = ctx.args[0].upper()
    db    = db_cargar()

    if token not in db["licencias"]:
        await update.message.reply_text("❌ Token no encontrado.")
        return

    db["licencias"][token]["activa"] = False
    db_guardar(db)

    nombre = db["licencias"][token]["nombre"]

    # Desconectar al cliente si está online
    if token in clientes:
        try:
            await clientes[token]["ws"].send_str(
                json.dumps({"tipo": "licencia_desactivada"})
            )
        except:
            pass
        del clientes[token]

    log.info(f"Licencia desactivada: {nombre}")
    await update.message.reply_text(
        f"🔴 Licencia de *{nombre}* desactivada.\nEl bot del cliente se detuvo al instante.",
        parse_mode="Markdown"
    )

@solo_admin
async def cmd_borrar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Uso: /borrar TOKEN")
        return

    token = ctx.args[0].upper()
    db    = db_cargar()

    if token not in db["licencias"]:
        await update.message.reply_text("❌ Token no encontrado.")
        return

    nombre = db["licencias"][token]["nombre"]
    del db["licencias"][token]
    db_guardar(db)

    if token in clientes:
        del clientes[token]

    await update.message.reply_text(f"🗑️ Licencia de *{nombre}* eliminada.", parse_mode="Markdown")

@solo_admin
async def cmd_estado(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    db       = db_cargar()
    total    = len(db["licencias"])
    activas  = sum(1 for l in db["licencias"].values() if l["activa"])
    online   = len(clientes)

    await update.message.reply_text(
        f"📊 *ESTADO DEL SERVIDOR*\n\n"
        f"📋 Licencias totales: {total}\n"
        f"✅ Licencias activas: {activas}\n"
        f"🟢 Bots online ahora: {online}\n",
        parse_mode="Markdown"
    )

# ────────────────────────────────────────
# WEBHOOK DE TRADINGVIEW (HTTP)
# TradingView manda las señales acá
# ────────────────────────────────────────
async def handle_webhook(request):
    # Verificar clave de seguridad
    key = request.headers.get("x-admin-key", "")
    if key != ADMIN_KEY:
        return web.Response(status=401, text="No autorizado")

    try:
        señal = await request.json()
    except:
        return web.Response(status=400, text="JSON inválido")

    accion = señal.get("action", "")
    ticker = señal.get("ticker", "")
    log.info(f"📡 Señal recibida: {accion} {ticker}")

    # Notificar al admin por Telegram
    bot = request.app["bot"]
    try:
        await bot.send_message(
            chat_id=ADMIN_ID,
            text=f"📡 Señal: *{accion.upper()}* {ticker}",
            parse_mode="Markdown"
        )
    except:
        pass

    # Distribuir a todos los clientes con licencia activa
    db      = db_cargar()
    enviados = 0
    desconectados = []

    for token, cliente in list(clientes.items()):
        lic = db["licencias"].get(token)
        if not lic or not lic["activa"]:
            continue

        # Verificar vencimiento
        if datetime.now() > datetime.fromisoformat(lic["vence"]):
            continue

        try:
            await cliente["ws"].send_str(json.dumps({
                "tipo"  : "señal",
                "accion": accion,
                "ticker": ticker,
                "precio": señal.get("price", "")
            }))
            enviados += 1
        except:
            desconectados.append(token)

    # Limpiar desconectados
    for token in desconectados:
        if token in clientes:
            del clientes[token]

    log.info(f"Señal enviada a {enviados} clientes")
    return web.json_response({"ok": True, "enviados": enviados})

# ────────────────────────────────────────
# WEBSOCKET — CONEXIÓN DE AGENTES
# El agente del cliente se conecta acá
# ────────────────────────────────────────
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

            tipo = data.get("tipo")

            # Autenticación
            if tipo == "auth":
                token = data.get("token", "").upper()
                db    = db_cargar()
                lic   = db["licencias"].get(token)

                if not lic:
                    await ws.send_str(json.dumps({"tipo": "error", "msg": "Token inválido"}))
                    await ws.close()
                    break

                if not lic["activa"]:
                    await ws.send_str(json.dumps({"tipo": "error", "msg": "Licencia desactivada"}))
                    await ws.close()
                    break

                if datetime.now() > datetime.fromisoformat(lic["vence"]):
                    await ws.send_str(json.dumps({"tipo": "error", "msg": "Licencia vencida"}))
                    await ws.close()
                    break

                # Registrar cliente
                token_cliente = token
                clientes[token] = {"ws": ws, "nombre": lic["nombre"]}

                await ws.send_str(json.dumps({
                    "tipo"  : "autenticado",
                    "nombre": lic["nombre"],
                    "vence" : lic["vence"][:10]
                }))

                log.info(f"✅ Cliente conectado: {lic['nombre']}")

                # Notificar al admin
                bot = request.app["bot"]
                try:
                    await bot.send_message(
                        chat_id=ADMIN_ID,
                        text=f"🟢 *{lic['nombre']}* se conectó",
                        parse_mode="Markdown"
                    )
                except:
                    pass

        elif msg.type == web.WSMsgType.ERROR:
            break

    # Cliente desconectado
    if token_cliente and token_cliente in clientes:
        nombre = clientes[token_cliente]["nombre"]
        del clientes[token_cliente]
        log.info(f"❌ Cliente desconectado: {nombre}")

    return ws

# ────────────────────────────────────────
# MAIN
# ────────────────────────────────────────
async def main():
    # Crear aplicación de Telegram
    app_tg = Application.builder().token(TELEGRAM_TOKEN).build()
    app_tg.add_handler(CommandHandler("start",      cmd_start))
    app_tg.add_handler(CommandHandler("nuevo",      cmd_nuevo))
    app_tg.add_handler(CommandHandler("lista",      cmd_lista))
    app_tg.add_handler(CommandHandler("activar",    cmd_activar))
    app_tg.add_handler(CommandHandler("desactivar", cmd_desactivar))
    app_tg.add_handler(CommandHandler("borrar",     cmd_borrar))
    app_tg.add_handler(CommandHandler("estado",     cmd_estado))

    await app_tg.initialize()
    await app_tg.start()
    await app_tg.updater.start_polling()

    # Crear servidor HTTP
    app_web = web.Application()
    app_web["bot"] = app_tg.bot
    app_web.router.add_post("/webhook", handle_webhook)
    app_web.router.add_get("/ws",       handle_ws)

    runner = web.AppRunner(app_web)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    log.info(f"🚀 MaxBot corriendo en puerto {PORT}")
    log.info(f"📡 Webhook: /webhook")
    log.info(f"🔌 WebSocket: /ws")

    # Mantener corriendo
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
