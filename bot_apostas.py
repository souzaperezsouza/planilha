import os
import logging
import threading
import psycopg2
import psycopg2.extras
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InputFile
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ConversationHandler, ContextTypes, filters
)

TOKEN         = "8790751046:AAG-AsvU3V-K5j4U8IOUQrpT6NXX8K3FcjU"
DATABASE_URL  = os.environ.get("DATABASE_URL", "postgresql://apostas_db_br3e_user:9Q8kF2084mtEmOESc09jc22ZR7nS5FLz@dpg-d6ub6lfafjfc7380et2g-a/apostas_db_br3e")
BANCA_INICIAL = 5000

CASAS = [
    "Bet365", "Betano", "SportingBet", "Novibet", "Vaidebet",
    "Betfast", "BETesporte", "Betao", "Betnacional", "BetFair",
    "Stake", "Pagol", "Vupi", "Outra"
]

ESPORTES = [
    "⚽ Futebol", "🏀 Basquete", "🎾 Tênis", "🏒 Hóquei",
    "🏈 Futebol Americano", "⚾ Beisebol", "🥊 MMA/Boxe", "🏐 Vôlei", "Outro"
]

logging.basicConfig(level=logging.WARNING)

# Estados
DATA, HORARIO, DESCRICAO, ODD, STAKE, ESPORTE, CASA  = range(7)
ATUALIZAR_ID, ATUALIZAR_RES                          = range(7, 9)
EDITAR_ID, EDITAR_CAMPO, EDITAR_VALOR, EDITAR_CASA   = range(9, 13)
RESULTADOS_DATA = 13

CANCELAR_BTN = "❌ Cancelar"

def teclado_cancelar():
    return ReplyKeyboardMarkup([[CANCELAR_BTN]], resize_keyboard=True)

def teclado_menu():
    return ReplyKeyboardMarkup([
        ["📝 Nova aposta",     "⏳ Ver pendentes"],
        ["📈 Resultados",      "📋 Últimas apostas"],
        ["🏦 Por casa",        "✏️ Editar aposta"],
        ["📤 Exportar CSV"],
    ], resize_keyboard=True)

# ── BANCO DE DADOS ────────────────────────────────────────────────────────────
def conectar():
    return psycopg2.connect(DATABASE_URL)

def inicializar_db():
    with conectar() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS apostas (
                    id        SERIAL PRIMARY KEY,
                    data      DATE NOT NULL,
                    horario   VARCHAR(5),
                    descricao TEXT NOT NULL,
                    odd       NUMERIC(8,3) NOT NULL,
                    stake     NUMERIC(10,2) NOT NULL,
                    resultado VARCHAR(10) DEFAULT 'pendente',
                    casa      VARCHAR(50),
                    esporte   VARCHAR(50)
                )
            """)
        conn.commit()

def carregar():
    with conectar() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM apostas ORDER BY data ASC, horario ASC, id ASC")
            rows = cur.fetchall()
    return [dict(r) for r in rows]

def inserir(aposta):
    with conectar() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO apostas (data, horario, descricao, odd, stake, resultado, casa, esporte)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                aposta["data"], aposta["horario"], aposta["descricao"],
                aposta["odd"], aposta["stake"], aposta["resultado"],
                aposta["casa"], aposta["esporte"]
            ))
            new_id = cur.fetchone()[0]
        conn.commit()
    return new_id

def atualizar_campo(id_aposta, campo, valor):
    campos_permitidos = {"data","horario","descricao","odd","stake","resultado","casa","esporte"}
    if campo not in campos_permitidos:
        return
    with conectar() as conn:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE apostas SET {campo} = %s WHERE id = %s", (valor, id_aposta))
        conn.commit()

def buscar_por_id(id_aposta):
    with conectar() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM apostas WHERE id = %s", (id_aposta,))
            row = cur.fetchone()
    return dict(row) if row else None

def calcular_metricas():
    apostas = carregar()
    res = [a for a in apostas if a["resultado"] in ("ganhou", "perdeu")]
    if not res:
        return None
    lucro       = sum(float(a["stake"]) * (float(a["odd"]) - 1) if a["resultado"] == "ganhou" else -float(a["stake"]) for a in res)
    stake_total = sum(float(a["stake"]) for a in res)
    vitorias    = sum(1 for a in res if a["resultado"] == "ganhou")
    return {
        "total":      len(apostas),
        "resolvidas": len(res),
        "pendentes":  len([a for a in apostas if a["resultado"] == "pendente"]),
        "vitorias":   vitorias,
        "win_rate":   vitorias / len(res),
        "lucro":      lucro,
        "roi":        lucro / stake_total,
        "progressao": lucro / BANCA_INICIAL,
    }

def exportar_csv_string():
    apostas = carregar()
    linhas  = ["id,data,horario,descricao,odd,stake,resultado,casa,esporte"]
    for a in apostas:
        data = a["data"].strftime("%Y-%m-%d") if hasattr(a["data"], "strftime") else str(a["data"])
        linhas.append(f"{a['id']},{data},{a.get('horario','')},{a['descricao']},{a['odd']},{a['stake']},{a['resultado']},{a.get('casa','')},{a.get('esporte','')}")
    return "\n".join(linhas).encode("utf-8")

# ── /start e menu ─────────────────────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔄 Atualizando menu...", reply_markup=ReplyKeyboardRemove())
    await update.message.reply_text(
        "👋 *Gestor de Apostas*\n\nEscolha uma opção:",
        reply_markup=teclado_menu(),
        parse_mode="Markdown"
    )

async def menu_botao(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text
    if txt == "📝 Nova aposta":        return await nova_aposta_inicio(update, ctx)
    if txt == "⏳ Ver pendentes":      return await ver_pendentes(update, ctx)
    if txt == "📈 Resultados":         return await resultados(update, ctx)
    if txt == "📋 Últimas apostas":    return await ultimas(update, ctx)
    if txt == "🏦 Por casa":           return await por_casa(update, ctx)
    if txt == "✏️ Editar aposta":      return await editar_inicio(update, ctx)
    if txt == "📤 Exportar CSV":       return await exportar_csv(update, ctx)

# ── NOVA APOSTA ───────────────────────────────────────────────────────────────
async def nova_aposta_inicio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    hoje = datetime.now().strftime("%d/%m/%Y")
    await update.message.reply_text(
        f"📅 *Data do jogo*\nHoje é *{hoje}* — mande *0* para confirmar ou digite outra data (DD/MM/AAAA):",
        reply_markup=teclado_cancelar(), parse_mode="Markdown"
    )
    return DATA

async def receber_data(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    if raw == CANCELAR_BTN: return await cancelar(update, ctx)
    if raw == "0":
        ctx.user_data["data"] = datetime.now().strftime("%Y-%m-%d")
    else:
        ok = False
        for fmt in ("%d/%m/%Y", "%d/%m/%y"):
            try:
                ctx.user_data["data"] = datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
                ok = True; break
            except ValueError: pass
        if not ok:
            await update.message.reply_text("❌ Data inválida. Use DD/MM/AAAA ou 0 para hoje:")
            return DATA
    agora = datetime.now().strftime("%H:%M")
    await update.message.reply_text(
        f"⏰ *Horário do jogo*\nAgora são *{agora}* — mande *0* para usar esse horário ou digite outro (HH:MM):",
        reply_markup=teclado_cancelar(), parse_mode="Markdown"
    )
    return HORARIO

async def receber_horario(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    if raw == CANCELAR_BTN: return await cancelar(update, ctx)
    if raw == "0":
        ctx.user_data["horario"] = datetime.now().strftime("%H:%M")
    else:
        try:
            ctx.user_data["horario"] = datetime.strptime(raw, "%H:%M").strftime("%H:%M")
        except ValueError:
            await update.message.reply_text("❌ Use HH:MM ou 0 para agora:")
            return HORARIO
    await update.message.reply_text("🏷 *Descrição da aposta:*", reply_markup=teclado_cancelar(), parse_mode="Markdown")
    return DESCRICAO

async def receber_descricao(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message.text.strip() == CANCELAR_BTN: return await cancelar(update, ctx)
    ctx.user_data["descricao"] = update.message.text.strip()
    await update.message.reply_text("🔢 *Odd:*", reply_markup=teclado_cancelar(), parse_mode="Markdown")
    return ODD

async def receber_odd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    if raw == CANCELAR_BTN: return await cancelar(update, ctx)
    try:
        ctx.user_data["odd"] = float(raw.replace(",", "."))
        await update.message.reply_text("💰 *Stake (R$):*", reply_markup=teclado_cancelar(), parse_mode="Markdown")
        return STAKE
    except ValueError:
        await update.message.reply_text("❌ Odd inválida:")
        return ODD

async def receber_stake(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    if raw == CANCELAR_BTN: return await cancelar(update, ctx)
    try:
        ctx.user_data["stake"] = float(raw.replace(",", "."))
        teclado_esp = [[e] for e in ESPORTES] + [[CANCELAR_BTN]]
        await update.message.reply_text(
            "🏅 *Esporte:*",
            reply_markup=ReplyKeyboardMarkup(teclado_esp, resize_keyboard=True, one_time_keyboard=True),
            parse_mode="Markdown"
        )
        return ESPORTE
    except ValueError:
        await update.message.reply_text("❌ Stake inválido:")
        return STAKE

async def receber_esporte(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    if raw == CANCELAR_BTN: return await cancelar(update, ctx)
    if ctx.user_data.pop("aguardando_esporte_custom", False):
        ctx.user_data["esporte"] = raw
    elif raw == "Outro":
        await update.message.reply_text("Digite o nome do esporte:", reply_markup=teclado_cancelar())
        ctx.user_data["aguardando_esporte_custom"] = True
        return ESPORTE
    else:
        ctx.user_data["esporte"] = raw
    teclado_casa = [[c] for c in CASAS] + [[CANCELAR_BTN]]
    await update.message.reply_text(
        "🏦 *Casa de aposta:*",
        reply_markup=ReplyKeyboardMarkup(teclado_casa, resize_keyboard=True, one_time_keyboard=True),
        parse_mode="Markdown"
    )
    return CASA

async def receber_casa(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    casa = update.message.text.strip()
    if casa == CANCELAR_BTN: return await cancelar(update, ctx)
    if casa == "Outra":
        await update.message.reply_text("Digite o nome da casa:", reply_markup=teclado_cancelar())
        ctx.user_data["aguardando_casa_custom"] = True
        return CASA
    ctx.user_data.pop("aguardando_casa_custom", False)
    ctx.user_data["casa"] = casa

    new_id = inserir({
        "data":      ctx.user_data["data"],
        "horario":   ctx.user_data.get("horario", ""),
        "descricao": ctx.user_data["descricao"],
        "odd":       ctx.user_data["odd"],
        "stake":     ctx.user_data["stake"],
        "resultado": "pendente",
        "casa":      ctx.user_data["casa"],
        "esporte":   ctx.user_data.get("esporte", ""),
    })

    data_fmt = datetime.strptime(ctx.user_data["data"], "%Y-%m-%d").strftime("%d/%m/%Y")
    hora_txt = f" às {ctx.user_data['horario']}" if ctx.user_data.get("horario") else ""
    await update.message.reply_text(
        f"✅ *Aposta #{new_id} salva!*\n\n"
        f"📅 {data_fmt}{hora_txt}\n"
        f"🏅 {ctx.user_data.get('esporte','')}\n"
        f"🏷 {ctx.user_data['descricao']}\n"
        f"🔢 Odd: {ctx.user_data['odd']}\n"
        f"💰 Stake: R$ {float(ctx.user_data['stake']):.2f}\n"
        f"🏦 Casa: {ctx.user_data['casa']}",
        parse_mode="Markdown"
    )
    return await voltar_menu(update, ctx)

# ── ATUALIZAR RESULTADO ───────────────────────────────────────────────────────
async def atualizar_inicio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    apostas   = carregar()
    pendentes = [a for a in apostas if a["resultado"] == "pendente"]
    if not pendentes:
        await update.message.reply_text("✅ Nenhuma aposta pendente!")
        return ConversationHandler.END
    pendentes.sort(key=lambda a: (str(a["data"]), a.get("horario") or ""))
    linhas = ["⏳ *Apostas pendentes:*\n"]
    for a in pendentes:
        data_fmt = a["data"].strftime("%d/%m") if hasattr(a["data"], "strftime") else str(a["data"])[:10]
        hora = f" {a.get('horario','')}" if a.get("horario") else ""
        linhas.append(f"*#{a['id']}* — {data_fmt}{hora} — {str(a['descricao'])[:35]} (odd {a['odd']})")
    linhas.append("\nDigite o *ID* da aposta a atualizar:")
    await update.message.reply_text("\n".join(linhas), reply_markup=teclado_cancelar(), parse_mode="Markdown")
    return ATUALIZAR_ID

async def receber_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    if raw == CANCELAR_BTN: return await cancelar(update, ctx)
    try:
        id_alvo = int(raw)
    except ValueError:
        await update.message.reply_text("❌ Digite só o número do ID:")
        return ATUALIZAR_ID
    aposta = buscar_por_id(id_alvo)
    if not aposta or aposta["resultado"] != "pendente":
        await update.message.reply_text("❌ ID não encontrado ou aposta já resolvida.")
        return ATUALIZAR_ID
    ctx.user_data["id_alvo"] = id_alvo
    teclado = [["✅ Ganhou", "❌ Perdeu", "↩️ Void"], [CANCELAR_BTN]]
    await update.message.reply_text(
        f"Aposta *#{id_alvo}*: _{aposta['descricao']}_\n\nQual foi o resultado?",
        reply_markup=ReplyKeyboardMarkup(teclado, resize_keyboard=True, one_time_keyboard=True),
        parse_mode="Markdown"
    )
    return ATUALIZAR_RES

async def receber_resultado(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()
    if txt == CANCELAR_BTN: return await cancelar(update, ctx)
    mapa = {"✅ Ganhou": "ganhou", "❌ Perdeu": "perdeu", "↩️ Void": "void"}
    res  = mapa.get(txt)
    if not res:
        await update.message.reply_text("Escolha uma das opções:")
        return ATUALIZAR_RES
    id_alvo = ctx.user_data["id_alvo"]
    atualizar_campo(id_alvo, "resultado", res)
    aposta = buscar_por_id(id_alvo)
    lucro_txt = ""
    if res in ("ganhou", "perdeu"):
        l = float(aposta["stake"]) * (float(aposta["odd"]) - 1) if res == "ganhou" else -float(aposta["stake"])
        lucro_txt = f"\n💰 Lucro: *{'+'if l>=0 else ''}R$ {l:.2f}*"
    await update.message.reply_text(
        f"✅ Aposta *#{id_alvo}* atualizada para *{res}*!{lucro_txt}",
        parse_mode="Markdown"
    )
    return await voltar_menu(update, ctx)

# ── VER PENDENTES ─────────────────────────────────────────────────────────────
async def ver_pendentes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    apostas   = carregar()
    pendentes = sorted([a for a in apostas if a["resultado"] == "pendente"],
                       key=lambda a: (str(a["data"]), a.get("horario") or ""))
    if not pendentes:
        await update.message.reply_text("✅ Nenhuma aposta pendente!")
        return
    linhas = [f"⏳ *{len(pendentes)} apostas pendentes:*\n"]
    for a in pendentes:
        data_fmt = a["data"].strftime("%d/%m/%Y") if hasattr(a["data"], "strftime") else str(a["data"])[:10]
        hora = f" {a.get('horario','')}" if a.get("horario") else ""
        linhas.append(f"*#{a['id']}* {data_fmt}{hora} | odd {a['odd']} | R${float(a['stake']):.0f} | {a.get('casa','')}\n_{a['descricao']}_\n")
    await update.message.reply_text("\n".join(linhas), parse_mode="Markdown")

# ── RESUMO ────────────────────────────────────────────────────────────────────
async def resumo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    m = calcular_metricas()
    if not m:
        await update.message.reply_text("Nenhuma aposta resolvida ainda.")
        return
    emoji_lucro = "📈" if m["lucro"] >= 0 else "📉"
    await update.message.reply_text(
        f"📊 *Resumo Geral*\n\n"
        f"🎯 Total: {m['total']} apostas\n"
        f"✅ Resolvidas: {m['resolvidas']}  |  ⏳ Pendentes: {m['pendentes']}\n"
        f"🏆 Win Rate: {m['win_rate']:.1%} ({m['vitorias']}V/{m['resolvidas']-m['vitorias']}D)\n"
        f"{emoji_lucro} Lucro: *{'+'if m['lucro']>=0 else ''}R$ {m['lucro']:.2f}*\n"
        f"📈 ROI: *{m['roi']:+.1%}*\n"
        f"💹 Progressão: *{m['progressao']:+.2%}*",
        parse_mode="Markdown"
    )

# ── ÚLTIMAS APOSTAS ───────────────────────────────────────────────────────────
async def ultimas(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    apostas = carregar()
    if not apostas:
        await update.message.reply_text("Nenhuma aposta cadastrada.")
        return
    ultimas_10 = sorted(apostas, key=lambda a: (str(a["data"]), int(a["id"])), reverse=True)[:10]
    emojis = {"ganhou": "✅", "perdeu": "❌", "pendente": "⏳", "void": "↩️"}
    linhas = ["📋 *Últimas 10 apostas:*\n"]
    for a in ultimas_10:
        data_fmt = a["data"].strftime("%d/%m") if hasattr(a["data"], "strftime") else str(a["data"])[:10]
        hora = f" {a.get('horario','')}" if a.get("horario") else ""
        e = emojis.get(a["resultado"], "")
        linhas.append(f"{e} *#{a['id']}* {data_fmt}{hora} | odd {a['odd']} | R${float(a['stake']):.0f}\n_{a['descricao']}_\n")
    await update.message.reply_text("\n".join(linhas), parse_mode="Markdown")

# ── POR CASA ──────────────────────────────────────────────────────────────────
async def por_casa(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    apostas = carregar()
    res     = [a for a in apostas if a["resultado"] in ("ganhou", "perdeu")]
    if not res:
        await update.message.reply_text("Nenhuma aposta resolvida ainda.")
        return
    casas = {}
    for a in res:
        casa = a.get("casa") or "Sem casa"
        if casa not in casas:
            casas[casa] = {"ap": 0, "g": 0, "stake": 0.0, "lucro": 0.0}
        c = casas[casa]
        c["ap"] += 1; c["stake"] += float(a["stake"])
        if a["resultado"] == "ganhou":
            c["g"] += 1; c["lucro"] += float(a["stake"]) * (float(a["odd"]) - 1)
        else:
            c["lucro"] -= float(a["stake"])
    ordenadas = sorted(casas.items(), key=lambda x: x[1]["lucro"], reverse=True)
    linhas = ["🏦 *Desempenho por Casa:*\n"]
    for nome, c in ordenadas:
        roi   = c["lucro"] / c["stake"] if c["stake"] else 0
        emoji = "🟢" if c["lucro"] >= 0 else "🔴"
        linhas.append(f"{emoji} *{nome}*\n  {c['ap']} apostas | {c['g']}V/{c['ap']-c['g']}D\n  Lucro: {'+'if c['lucro']>=0 else ''}R$ {c['lucro']:.2f} | ROI: {roi:+.1%}\n")
    await update.message.reply_text("\n".join(linhas), parse_mode="Markdown")

# ── EDITAR APOSTA ─────────────────────────────────────────────────────────────
CAMPOS_EDITAVEIS = ["data", "horario", "descricao", "odd", "stake", "esporte", "casa", "resultado"]
CAMPOS_LABEL     = {"data":"📅 Data","horario":"⏰ Horário","descricao":"🏷 Descrição",
                    "odd":"🔢 Odd","stake":"💰 Stake","esporte":"🏅 Esporte","casa":"🏦 Casa"}

async def editar_inicio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    apostas   = carregar()
    pendentes = sorted([a for a in apostas if a["resultado"] == "pendente"],
                       key=lambda a: (str(a["data"]), a.get("horario") or ""))
    emojis = {"ganhou":"✅","perdeu":"❌","pendente":"⏳","void":"↩️"}
    linhas = ["✏️ *Editar aposta*\n"]
    if pendentes:
        linhas.append("*Apostas pendentes:*\n")
        for a in pendentes:
            data_fmt = a["data"].strftime("%d/%m") if hasattr(a["data"],"strftime") else str(a["data"])[:10]
            hora = f" {a.get('horario','')}" if a.get("horario") else ""
            linhas.append(f"⏳ *#{a['id']}* {data_fmt}{hora} | odd {a['odd']} | _{str(a['descricao'])[:30]}_")
        linhas.append("")
    else:
        linhas.append("Nenhuma aposta pendente.\n")
    linhas.append("Digite o *ID* da aposta que quer editar\n_(pode ser qualquer aposta, não só as pendentes)_:")
    await update.message.reply_text("\n".join(linhas), reply_markup=teclado_cancelar(), parse_mode="Markdown")
    return EDITAR_ID

async def editar_receber_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    if raw == CANCELAR_BTN: return await cancelar(update, ctx)
    try:
        id_alvo = int(raw)
    except ValueError:
        await update.message.reply_text("❌ Digite só o número do ID:")
        return EDITAR_ID
    aposta = buscar_por_id(id_alvo)
    if not aposta:
        await update.message.reply_text("❌ ID não encontrado.")
        return EDITAR_ID
    ctx.user_data["editar_id"] = id_alvo
    data_fmt = aposta["data"].strftime("%d/%m/%Y") if hasattr(aposta["data"],"strftime") else str(aposta["data"])[:10]
    hora = f" {aposta.get('horario','')}" if aposta.get("horario") else ""
    info = (f"✏️ *Aposta #{id_alvo}*\n\n📅 {data_fmt}{hora}\n🏷 {aposta['descricao']}\n"
            f"🔢 Odd: {aposta['odd']}\n💰 Stake: R$ {float(aposta['stake']):.2f}\n"
            f"🏦 Casa: {aposta.get('casa','')}\n📊 Resultado: {aposta['resultado']}\n\n*Qual campo quer editar?*")
    teclado = [[CAMPOS_LABEL[c]] for c in CAMPOS_EDITAVEIS] + [[CANCELAR_BTN]]
    await update.message.reply_text(info,
        reply_markup=ReplyKeyboardMarkup(teclado, resize_keyboard=True, one_time_keyboard=True),
        parse_mode="Markdown")
    return EDITAR_CAMPO

async def editar_receber_campo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    if raw == CANCELAR_BTN: return await cancelar(update, ctx)
    campo = next((k for k, v in CAMPOS_LABEL.items() if v == raw), None)
    if not campo:
        await update.message.reply_text("❌ Escolha um dos campos:")
        return EDITAR_CAMPO
    ctx.user_data["editar_campo"] = campo
    if campo == "casa":
        teclado = [[c] for c in CASAS] + [[CANCELAR_BTN]]
        await update.message.reply_text("🏦 *Escolha a nova casa:*",
            reply_markup=ReplyKeyboardMarkup(teclado, resize_keyboard=True, one_time_keyboard=True),
            parse_mode="Markdown")
        return EDITAR_CASA
    if campo == "esporte":
        teclado = [[e] for e in ESPORTES] + [[CANCELAR_BTN]]
        await update.message.reply_text("🏅 *Escolha o novo esporte:*",
            reply_markup=ReplyKeyboardMarkup(teclado, resize_keyboard=True, one_time_keyboard=True),
            parse_mode="Markdown")
        return EDITAR_CASA
    if campo == "resultado":
        teclado = [["✅ Ganhou", "❌ Perdeu"], ["↩️ Void", "⏳ Pendente"], [CANCELAR_BTN]]
        await update.message.reply_text("📊 *Qual o resultado?*",
            reply_markup=ReplyKeyboardMarkup(teclado, resize_keyboard=True, one_time_keyboard=True),
            parse_mode="Markdown")
        return EDITAR_CASA
    dicas = {"data":"Nova data (DD/MM/AAAA) ou 0 para hoje:","horario":"Novo horário (HH:MM) ou 0 para agora:",
             "descricao":"Nova descrição:","odd":"Nova odd:","stake":"Novo stake (R$):"}
    await update.message.reply_text(dicas[campo], reply_markup=teclado_cancelar())
    return EDITAR_VALOR

async def editar_receber_valor(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw   = update.message.text.strip()
    if raw == CANCELAR_BTN: return await cancelar(update, ctx)
    campo = ctx.user_data["editar_campo"]
    id_alvo = ctx.user_data["editar_id"]
    novo_valor = None
    if campo == "data":
        if raw == "0": novo_valor = datetime.now().strftime("%Y-%m-%d")
        else:
            for fmt in ("%d/%m/%Y", "%d/%m/%y"):
                try: novo_valor = datetime.strptime(raw, fmt).strftime("%Y-%m-%d"); break
                except ValueError: pass
            if not novo_valor:
                await update.message.reply_text("❌ Data inválida:")
                return EDITAR_VALOR
    elif campo == "horario":
        novo_valor = datetime.now().strftime("%H:%M") if raw == "0" else raw
    elif campo in ("odd","stake"):
        try: novo_valor = float(raw.replace(",","."))
        except ValueError:
            await update.message.reply_text("❌ Digite um número:")
            return EDITAR_VALOR
    else:
        novo_valor = raw
    return await aplicar_edicao(update, ctx, id_alvo, campo, novo_valor)

async def editar_receber_casa(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    if raw == CANCELAR_BTN: return await cancelar(update, ctx)
    if raw == "Outra" or raw == "Outro":
        await update.message.reply_text("Digite o nome:", reply_markup=teclado_cancelar())
        ctx.user_data["editar_campo"] = "casa_custom"
        return EDITAR_VALOR
    # Mapear botões de resultado
    mapa_res = {"✅ Ganhou": "ganhou", "❌ Perdeu": "perdeu", "↩️ Void": "void", "⏳ Pendente": "pendente"}
    if ctx.user_data.get("editar_campo") == "resultado" and raw in mapa_res:
        return await aplicar_edicao(update, ctx, ctx.user_data["editar_id"], "resultado", mapa_res[raw])
    return await aplicar_edicao(update, ctx, ctx.user_data["editar_id"], ctx.user_data["editar_campo"], raw)

async def aplicar_edicao(update, ctx, id_alvo, campo, novo_valor):
    campo_real = "casa" if campo == "casa_custom" else campo
    atualizar_campo(id_alvo, campo_real, novo_valor)
    label  = CAMPOS_LABEL.get(campo_real, campo_real)
    exibe  = novo_valor
    if campo_real == "data" and isinstance(novo_valor, str) and len(novo_valor) == 10:
        exibe = datetime.strptime(novo_valor, "%Y-%m-%d").strftime("%d/%m/%Y")
    elif campo_real in ("odd","stake"):
        exibe = f"{float(novo_valor):.2f}"
    await update.message.reply_text(f"✅ *Aposta #{id_alvo} atualizada!*\n{label} → *{exibe}*", parse_mode="Markdown")
    return await voltar_menu(update, ctx)


# ── RESULTADOS ────────────────────────────────────────────────────────────────
def calcular_lucro_lista(lista):
    return sum(
        float(a["stake"]) * (float(a["odd"]) - 1) if a["resultado"] == "ganhou"
        else -float(a["stake"])
        for a in lista
    )

async def resultados(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    apostas = carregar()
    res     = [a for a in apostas if a["resultado"] in ("ganhou", "perdeu")]
    if not res:
        await update.message.reply_text("Nenhuma aposta resolvida ainda.")
        return

    lucro_total = calcular_lucro_lista(res)
    stake_total = sum(float(a["stake"]) for a in res)
    vitorias    = sum(1 for a in res if a["resultado"] == "ganhou")
    roi         = lucro_total / stake_total if stake_total else 0
    progressao  = lucro_total / BANCA_INICIAL
    sinal_l     = "+" if lucro_total >= 0 else ""
    emoji_lucro = "📈" if lucro_total >= 0 else "📉"

    teclado_res = ReplyKeyboardMarkup([["🏦 Por Casa"], [CANCELAR_BTN]], resize_keyboard=True)
    await update.message.reply_text(
        f"{emoji_lucro} *Resultados Gerais*\n\n"
        f"💰 Lucro Total: *{sinal_l}R$ {lucro_total:.2f}*\n"
        f"📊 ROI: *{roi:+.1%}*\n"
        f"💹 Progressão: *{progressao:+.2%}*\n"
        f"🏆 {vitorias}V / {len(res)-vitorias}D\n\n"
        f"📅 Digite uma data (DD/MM) para ver aquele dia\n"
        f"🏦 Ou clique em *Por Casa* para ver por casa\n"
        f"*0* para voltar.",
        reply_markup=teclado_res,
        parse_mode="Markdown"
    )
    return RESULTADOS_DATA

async def resultados_por_casa(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    apostas = carregar()
    res     = [a for a in apostas if a["resultado"] in ("ganhou", "perdeu")]
    if not res:
        await update.message.reply_text("Nenhuma aposta resolvida ainda.")
        return RESULTADOS_DATA
    casas = {}
    for a in res:
        casa = a.get("casa") or "Sem casa"
        if casa not in casas:
            casas[casa] = {"ap":0,"g":0,"stake":0.0,"lucro":0.0}
        c = casas[casa]
        c["ap"] += 1; c["stake"] += float(a["stake"])
        if a["resultado"] == "ganhou":
            c["g"] += 1; c["lucro"] += float(a["stake"]) * (float(a["odd"])-1)
        else:
            c["lucro"] -= float(a["stake"])
    ordenadas = sorted(casas.items(), key=lambda x: x[1]["lucro"], reverse=True)
    linhas = ["🏦 *Por Casa:*\n"]
    for nome, c in ordenadas:
        roi   = c["lucro"]/c["stake"] if c["stake"] else 0
        emoji = "🟢" if c["lucro"] >= 0 else "🔴"
        sinal = "+" if c["lucro"] >= 0 else ""
        linhas.append(f"{emoji} *{nome}*\n  {c['ap']} ap | {c['g']}V/{c['ap']-c['g']}D | {sinal}R$ {c['lucro']:.2f} | ROI: {roi:+.1%}\n")
    linhas.append("\nDigite uma data (DD/MM) ou *0* para voltar:")
    teclado_res = ReplyKeyboardMarkup([["🏦 Por Casa"],[CANCELAR_BTN]], resize_keyboard=True)
    await update.message.reply_text("\n".join(linhas), reply_markup=teclado_res, parse_mode="Markdown")
    return RESULTADOS_DATA

async def resultados_receber_data(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    # Qualquer botão do menu principal sai do fluxo
    botoes_menu = {"📝 Nova aposta","⏳ Ver pendentes","📈 Resultados","✏️ Editar aposta","📤 Exportar CSV",
                   "📊 Resumo","📋 Últimas apostas","🏦 Por casa","✅ Atualizar resultado"}
    if raw == CANCELAR_BTN or raw == "0" or raw in botoes_menu:
        if raw in botoes_menu and raw != "📈 Resultados":
            return await menu_botao(update, ctx)
        return await voltar_menu(update, ctx)
    if raw == "🏦 Por Casa":
        return await resultados_por_casa(update, ctx)

    # Tenta parsear a data
    data_obj = None
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%d/%m"):
        try:
            if fmt == "%d/%m":
                ano = datetime.now().year
                data_obj = datetime.strptime(f"{raw}/{ano}", "%d/%m/%Y")
            else:
                data_obj = datetime.strptime(raw, fmt)
            break
        except ValueError:
            pass

    if not data_obj:
        await update.message.reply_text("❌ Data inválida. Use DD/MM ou DD/MM/AAAA:")
        return RESULTADOS_DATA

    data_str = data_obj.strftime("%Y-%m-%d")
    apostas  = carregar()
    do_dia   = [a for a in apostas if str(a["data"])[:10] == data_str and a["resultado"] in ("ganhou","perdeu")]

    if not do_dia:
        await update.message.reply_text(
            f"Nenhuma aposta resolvida em {data_obj.strftime('%d/%m/%Y')}.",
        )
        return RESULTADOS_DATA

    lucro_dia = calcular_lucro_lista(do_dia)
    g = sum(1 for a in do_dia if a["resultado"] == "ganhou")
    p = sum(1 for a in do_dia if a["resultado"] == "perdeu")
    emoji = "🟢" if lucro_dia >= 0 else "🔴"
    sinal = "+" if lucro_dia >= 0 else ""

    emojis = {"ganhou": "✅", "perdeu": "❌"}
    linhas = [f"{emoji} *{data_obj.strftime('%d/%m/%Y')}* — {g}V {p}D — {sinal}R$ {lucro_dia:.2f}\n"]
    for a in do_dia:
        e = emojis.get(a["resultado"], "")
        linhas.append(f"{e} odd {a['odd']} | R${float(a['stake']):.0f} → {sinal if a['resultado']=='ganhou' else '-'}R${abs(float(a['stake'])*(float(a['odd'])-1) if a['resultado']=='ganhou' else float(a['stake'])):.2f}\n_{a['descricao']}_\n")

    linhas.append("\nDigite outra data, *Por Casa* ou *0* para voltar:")
    teclado_res = ReplyKeyboardMarkup([["🏦 Por Casa"],[CANCELAR_BTN]], resize_keyboard=True)
    await update.message.reply_text("\n".join(linhas), reply_markup=teclado_res, parse_mode="Markdown")
    return RESULTADOS_DATA


# ── EXPORTAR CSV ──────────────────────────────────────────────────────────────
async def exportar_csv(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    apostas   = carregar()
    total     = len(apostas)
    pendentes = sum(1 for a in apostas if a["resultado"] == "pendente")
    csv_bytes = exportar_csv_string()
    caption   = f"\U0001f4ca apostas.csv\n{total} apostas | {pendentes} pendentes"
    await update.message.reply_document(
        document=InputFile(csv_bytes, filename="apostas.csv"),
        caption=caption
    )

# ── HELPERS ───────────────────────────────────────────────────────────────────
async def voltar_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("O que mais?", reply_markup=teclado_menu())
    return ConversationHandler.END

async def cancelar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await update.message.reply_text("❌ Cancelado.", reply_markup=teclado_menu())
    return ConversationHandler.END

# ── SERVIDOR KEEP-ALIVE ───────────────────────────────────────────────────────
class PingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
    def log_message(self, *args): pass

def iniciar_servidor():
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(("0.0.0.0", port), PingHandler).serve_forever()

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    inicializar_db()

    app = Application.builder().token(TOKEN).build()

    conv_nova = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📝 Nova aposta$"), nova_aposta_inicio)],
        states={
            DATA:      [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_data)],
            HORARIO:   [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_horario)],
            DESCRICAO: [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_descricao)],
            ODD:       [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_odd)],
            STAKE:     [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_stake)],
            ESPORTE:   [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_esporte)],
            CASA:      [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_casa)],
        },
        fallbacks=[CommandHandler("cancelar", cancelar),
                   MessageHandler(filters.Regex(f"^{CANCELAR_BTN}$"), cancelar)],
    )
    conv_atualizar = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^✅ Atualizar resultado$"), atualizar_inicio)],
        states={
            ATUALIZAR_ID:  [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_id)],
            ATUALIZAR_RES: [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_resultado)],
        },
        fallbacks=[CommandHandler("cancelar", cancelar),
                   MessageHandler(filters.Regex(f"^{CANCELAR_BTN}$"), cancelar)],
    )
    conv_resultados = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📈 Resultados$"), resultados)],
        states={
            RESULTADOS_DATA: [MessageHandler(filters.TEXT & ~filters.COMMAND, resultados_receber_data)],
        },
        fallbacks=[
            CommandHandler("cancelar", cancelar),
            MessageHandler(filters.Regex(f"^{CANCELAR_BTN}$"), cancelar),
            MessageHandler(filters.Regex("^(📝 Nova aposta|⏳ Ver pendentes|✏️ Editar aposta|📤 Exportar CSV)$"), cancelar),
        ],
        allow_reentry=True,
    )

    conv_editar = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^✏️ Editar aposta$"), editar_inicio)],
        states={
            EDITAR_ID:    [MessageHandler(filters.TEXT & ~filters.COMMAND, editar_receber_id)],
            EDITAR_CAMPO: [MessageHandler(filters.TEXT & ~filters.COMMAND, editar_receber_campo)],
            EDITAR_VALOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, editar_receber_valor)],
            EDITAR_CASA:  [MessageHandler(filters.TEXT & ~filters.COMMAND, editar_receber_casa)],
        },
        fallbacks=[CommandHandler("cancelar", cancelar),
                   MessageHandler(filters.Regex(f"^{CANCELAR_BTN}$"), cancelar)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("exportar", exportar_csv))
    app.add_handler(conv_nova)
    app.add_handler(conv_atualizar)
    app.add_handler(conv_editar)
    app.add_handler(conv_resultados)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_botao))

    threading.Thread(target=iniciar_servidor, daemon=True).start()
    print("🤖 Bot rodando! Abra o Telegram e mande /start")
    app.run_polling()

if __name__ == "__main__":
    main()
