import os
import io
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

CASAS = ["Bet365","Betano","SportingBet","Novibet","Vaidebet","Betfast","BETesporte",
         "Betnacional","BetFair","Stake","Pagol","Esportes Da Sorte","Gol De Bet","Outra"]

ESPORTES = ["⚽ Futebol","🏀 Basquete","🎾 Tênis","🏒 Hóquei",
            "🏈 Futebol Americano","⚾ Beisebol","🥊 MMA/Boxe","🏐 Vôlei","Outro"]

logging.basicConfig(level=logging.WARNING)

# Estados dos ConversationHandlers
DATA, HORARIO, DESCRICAO, ODD, STAKE, ESPORTE, CASA = range(7)
ATUALIZAR_ID, ATUALIZAR_RES                         = range(7, 9)
EDITAR_ID, EDITAR_CAMPO, EDITAR_VALOR, EDITAR_CASA  = range(9, 13)

CANCELAR_BTN = "❌ Cancelar"

# ── TECLADOS ──────────────────────────────────────────────────────────────────
def teclado_menu():
    return ReplyKeyboardMarkup([
        ["📝 Nova aposta",   "⏳ Ver pendentes"],
        ["📈 Resultados",    "✏️ Editar aposta"],
        ["📊 Gerar Dashboard"],
    ], resize_keyboard=True)

def teclado_cancelar():
    return ReplyKeyboardMarkup([[CANCELAR_BTN]], resize_keyboard=True)

def teclado_resultados():
    return ReplyKeyboardMarkup([["🏦 Por Casa", "🔙 Voltar"]], resize_keyboard=True)

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
                    esporte   VARCHAR(50),
                    freebet   VARCHAR(3) DEFAULT 'nao'
                )
            """)
            # Adicionar coluna se não existir (migração)
            cur.execute("""
                ALTER TABLE apostas ADD COLUMN IF NOT EXISTS freebet VARCHAR(3) DEFAULT 'nao'
            """)
        conn.commit()

def carregar():
    with conectar() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM apostas ORDER BY data ASC, horario ASC, id ASC")
            return [dict(r) for r in cur.fetchall()]

def inserir(a):
    with conectar() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO apostas (data,horario,descricao,odd,stake,resultado,casa,esporte,freebet)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
            """, (a["data"],a["horario"],a["descricao"],a["odd"],a["stake"],
                  a["resultado"],a["casa"],a["esporte"],a.get("freebet","nao")))
            new_id = cur.fetchone()[0]
        conn.commit()
    return new_id

def atualizar_campo(id_aposta, campo, valor):
    if campo not in {"data","horario","descricao","odd","stake","resultado","casa","esporte"}:
        return
    with conectar() as conn:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE apostas SET {campo}=%s WHERE id=%s", (valor, id_aposta))
        conn.commit()

def buscar_por_id(id_aposta):
    with conectar() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM apostas WHERE id=%s", (id_aposta,))
            row = cur.fetchone()
    return dict(row) if row else None

# ── HELPERS ───────────────────────────────────────────────────────────────────
def normalizar_casa(s):
    return (s or "").strip().title() or "Sem casa"

def lucro_aposta(a):
    freebet = str(a.get("freebet","nao")).strip().lower() == "sim"
    if a["resultado"] == "ganhou":
        return float(a["stake"]) * (float(a["odd"]) - 1)
    if a["resultado"] == "perdeu":
        return 0.0 if freebet else -float(a["stake"])
    return 0.0

async def voltar_menu(update, ctx):
    ctx.user_data.clear()
    await update.message.reply_text("O que mais?", reply_markup=teclado_menu())
    return ConversationHandler.END

async def cancelar(update, ctx):
    ctx.user_data.clear()
    await update.message.reply_text("❌ Cancelado.", reply_markup=teclado_menu())
    return ConversationHandler.END

# ── /start ────────────────────────────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await update.message.reply_text(
        "👋 *Gestor de Apostas*\n\nEscolha uma opção:",
        reply_markup=teclado_menu(), parse_mode="Markdown"
    )

# ── MENU PRINCIPAL ────────────────────────────────────────────────────────────
async def menu_botao(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text

    # Modo resultados — trata inputs dentro do contexto
    if ctx.user_data.get("modo") == "resultados":
        if txt == "🔙 Voltar":
            return await voltar_menu(update, ctx)
        if txt == "🏦 Por Casa":
            return await resultados_por_casa(update, ctx)
        # Tenta como data
        if await resultados_dia(update, ctx, txt):
            return
        # Se não reconheceu, sai do modo
        ctx.user_data.clear()

    if txt == "📝 Nova aposta":     return await nova_aposta_inicio(update, ctx)
    if txt == "⏳ Ver pendentes":   return await ver_pendentes(update, ctx)
    if txt == "📈 Resultados":      return await resultados(update, ctx)
    if txt == "✏️ Editar aposta":   return await editar_inicio(update, ctx)
    if txt == "📊 Gerar Dashboard":  return await gerar_dashboard(update, ctx)

# ── NOVA APOSTA ───────────────────────────────────────────────────────────────
async def nova_aposta_inicio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    hoje = datetime.now().strftime("%d/%m/%Y")
    await update.message.reply_text(
        f"📅 *Data do jogo*\nHoje é *{hoje}* — mande *0* para confirmar ou digite outra (DD/MM/AAAA):",
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
        f"⏰ *Horário do jogo*\nAgora são *{agora}* — mande *0* para usar ou digite outro (HH:MM):",
        reply_markup=teclado_cancelar(), parse_mode="Markdown"
    )
    return HORARIO

async def receber_horario(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    if raw == CANCELAR_BTN: return await cancelar(update, ctx)
    ctx.user_data["horario"] = datetime.now().strftime("%H:%M") if raw == "0" else raw
    if raw != "0":
        try: datetime.strptime(raw, "%H:%M")
        except ValueError:
            await update.message.reply_text("❌ Use HH:MM ou 0 para agora:")
            return HORARIO
    await update.message.reply_text("🏷 *Descrição:*", reply_markup=teclado_cancelar(), parse_mode="Markdown")
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
        await update.message.reply_text("🏅 *Esporte:*",
            reply_markup=ReplyKeyboardMarkup(teclado_esp, resize_keyboard=True, one_time_keyboard=True),
            parse_mode="Markdown")
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
    await update.message.reply_text("🏦 *Casa de aposta:*",
        reply_markup=ReplyKeyboardMarkup(teclado_casa, resize_keyboard=True, one_time_keyboard=True),
        parse_mode="Markdown")
    return CASA

async def receber_casa(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    casa = update.message.text.strip()
    if casa == CANCELAR_BTN: return await cancelar(update, ctx)
    if casa == "Outra":
        await update.message.reply_text("Digite o nome da casa:", reply_markup=teclado_cancelar())
        ctx.user_data["aguardando_casa_custom"] = True
        return CASA
    ctx.user_data.pop("aguardando_casa_custom", False)
    ctx.user_data["casa"] = casa.strip().title() if casa not in CASAS else casa

    new_id = inserir({
        "data": ctx.user_data["data"], "horario": ctx.user_data.get("horario",""),
        "descricao": ctx.user_data["descricao"], "odd": ctx.user_data["odd"],
        "stake": ctx.user_data["stake"], "resultado": "pendente",
        "casa": ctx.user_data["casa"], "esporte": ctx.user_data.get("esporte",""),
    })
    data_fmt = datetime.strptime(ctx.user_data["data"], "%Y-%m-%d").strftime("%d/%m/%Y")
    hora_txt = f" às {ctx.user_data['horario']}" if ctx.user_data.get("horario") else ""
    await update.message.reply_text(
        f"✅ *Aposta #{new_id} salva!*\n\n"
        f"📅 {data_fmt}{hora_txt}\n🏅 {ctx.user_data.get('esporte','')}\n"
        f"🏷 {ctx.user_data['descricao']}\n🔢 Odd: {ctx.user_data['odd']}\n"
        f"💰 Stake: R$ {float(ctx.user_data['stake']):.2f}\n🏦 Casa: {ctx.user_data['casa']}",
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
        data_fmt = a["data"].strftime("%d/%m/%Y") if hasattr(a["data"],"strftime") else str(a["data"])[:10]
        hora = f" {a.get('horario','')}" if a.get("horario") else ""
        linhas.append(f"*#{a['id']}* {data_fmt}{hora} | odd {a['odd']} | R${float(a['stake']):.0f} | {a.get('casa','')}\n_{a['descricao']}_\n")
    await update.message.reply_text("\n".join(linhas), parse_mode="Markdown")

# ── RESULTADOS ────────────────────────────────────────────────────────────────
async def resultados(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["modo"] = "resultados"
    apostas = carregar()
    res     = [a for a in apostas if a["resultado"] in ("ganhou","perdeu")]
    if not res:
        await update.message.reply_text("Nenhuma aposta resolvida ainda.")
        return
    lucro_total = sum(lucro_aposta(a) for a in res)
    stake_total = sum(float(a["stake"]) for a in res)
    vitorias    = sum(1 for a in res if a["resultado"] == "ganhou")
    roi         = lucro_total / stake_total if stake_total else 0
    progressao  = lucro_total / BANCA_INICIAL
    sinal       = "+" if lucro_total >= 0 else ""
    emoji       = "📈" if lucro_total >= 0 else "📉"
    pendentes_ap = [a for a in apostas if a["resultado"] == "pendente"]
    stake_curso  = sum(float(a["stake"]) for a in pendentes_ap)
    await update.message.reply_text(
        f"{emoji} *Resultados Gerais*\n\n"
        f"💰 Lucro Total: *{sinal}R$ {lucro_total:.2f}*\n"
        f"📊 ROI: *{roi:+.1%}*\n"
        f"💹 Progressão: *{progressao:+.2%}*\n"
        f"🏆 {vitorias}V / {len(res)-vitorias}D\n"
        f"⏳ Stake em curso: *R$ {stake_curso:.2f}* ({len(pendentes_ap)} apostas)\n"
        f"📏 Unidades: *{lucro_total/50:+.2f}u*\n\n"
        f"📅 Digite uma data (DD/MM) para ver aquele dia\n"
        f"🏦 *Por Casa* para ver por casa\n"
        f"🔙 *Voltar* para sair",
        reply_markup=teclado_resultados(), parse_mode="Markdown"
    )

async def resultados_por_casa(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    apostas = carregar()
    res     = [a for a in apostas if a["resultado"] in ("ganhou","perdeu")]
    casas   = {}
    for a in res:
        casa = normalizar_casa(a.get("casa"))
        if casa not in casas:
            casas[casa] = {"ap":0,"g":0,"stake":0.0,"lucro":0.0}
        c = casas[casa]; c["ap"] += 1; c["stake"] += float(a["stake"])
        if a["resultado"] == "ganhou":
            c["g"] += 1; c["lucro"] += float(a["stake"]) * (float(a["odd"])-1)
        else:
            c["lucro"] -= float(a["stake"])
    ordenadas = sorted(casas.items(), key=lambda x: x[1]["lucro"], reverse=True)
    linhas    = ["🏦 *Por Casa:*\n"]
    for nome, c in ordenadas:
        roi   = c["lucro"]/c["stake"] if c["stake"] else 0
        emoji = "🟢" if c["lucro"] >= 0 else "🔴"
        sinal = "+" if c["lucro"] >= 0 else ""
        linhas.append(f"{emoji} *{nome}*\n  {c['ap']} ap | {c['g']}V/{c['ap']-c['g']}D | {sinal}R$ {c['lucro']:.2f} | ROI: {roi:+.1%}\n")
    linhas.append("\nDigite uma data (DD/MM) ou 🔙 Voltar:")
    await update.message.reply_text("\n".join(linhas), reply_markup=teclado_resultados(), parse_mode="Markdown")

async def resultados_dia(update: Update, ctx: ContextTypes.DEFAULT_TYPE, raw: str) -> bool:
    data_obj = None
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%d/%m"):
        try:
            if fmt == "%d/%m":
                data_obj = datetime.strptime(f"{raw}/{datetime.now().year}", "%d/%m/%Y")
            else:
                data_obj = datetime.strptime(raw, fmt)
            break
        except ValueError: pass
    if not data_obj:
        return False
    data_str = data_obj.strftime("%Y-%m-%d")
    apostas  = carregar()
    do_dia   = [a for a in apostas if str(a["data"])[:10] == data_str and a["resultado"] in ("ganhou","perdeu")]
    if not do_dia:
        await update.message.reply_text(
            f"Nenhuma aposta resolvida em {data_obj.strftime('%d/%m/%Y')}.",
            reply_markup=teclado_resultados())
        return True
    lucro_dia = sum(lucro_aposta(a) for a in do_dia)
    g = sum(1 for a in do_dia if a["resultado"] == "ganhou")
    p = sum(1 for a in do_dia if a["resultado"] == "perdeu")
    emoji = "🟢" if lucro_dia >= 0 else "🔴"
    sinal = "+" if lucro_dia >= 0 else ""
    emojis = {"ganhou":"✅","perdeu":"❌"}
    linhas = [f"{emoji} *{data_obj.strftime('%d/%m/%Y')}* — {g}V {p}D — {sinal}R$ {lucro_dia:.2f}\n"]
    for a in do_dia:
        l = lucro_aposta(a)
        s = "+" if l >= 0 else ""
        linhas.append(f"{emojis.get(a['resultado'],'')} odd {a['odd']} | R${float(a['stake']):.0f} → {s}R$ {l:.2f}\n_{a['descricao']}_\n")
    linhas.append("Digite outra data, 🏦 *Por Casa* ou 🔙 *Voltar*:")
    await update.message.reply_text("\n".join(linhas), reply_markup=teclado_resultados(), parse_mode="Markdown")
    return True

# ── EDITAR APOSTA ─────────────────────────────────────────────────────────────
CAMPOS_EDITAVEIS = ["data","horario","descricao","odd","stake","esporte","casa","resultado","cashout","freebet"]
CAMPOS_LABEL     = {
    "data":"📅 Data","horario":"⏰ Horário","descricao":"🏷 Descrição",
    "odd":"🔢 Odd","stake":"💰 Stake","esporte":"🏅 Esporte",
    "casa":"🏦 Casa","resultado":"📊 Resultado","cashout":"💸 Cashout","freebet":"🎁 Freebet"
}

async def editar_inicio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    apostas   = carregar()
    pendentes = sorted([a for a in apostas if a["resultado"] == "pendente"],
                       key=lambda a: (str(a["data"]), a.get("horario") or ""))
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
    linhas.append("Digite o *ID* da aposta que quer editar:")
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
            f"🏦 Casa: {aposta.get('casa','')}\n📊 Resultado: {aposta['resultado']}\n\n*Qual campo editar?*")
    teclado = [[CAMPOS_LABEL[c]] for c in CAMPOS_EDITAVEIS] + [[CANCELAR_BTN]]
    await update.message.reply_text(info,
        reply_markup=ReplyKeyboardMarkup(teclado, resize_keyboard=True, one_time_keyboard=True),
        parse_mode="Markdown")
    return EDITAR_CAMPO

async def editar_receber_campo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    if raw == CANCELAR_BTN: return await cancelar(update, ctx)
    campo = next((k for k,v in CAMPOS_LABEL.items() if v == raw), None)
    if not campo:
        await update.message.reply_text("❌ Escolha um dos campos:")
        return EDITAR_CAMPO
    ctx.user_data["editar_campo"] = campo
    if campo == "casa":
        teclado = [[c] for c in CASAS] + [[CANCELAR_BTN]]
        await update.message.reply_text("🏦 *Nova casa:*",
            reply_markup=ReplyKeyboardMarkup(teclado, resize_keyboard=True, one_time_keyboard=True),
            parse_mode="Markdown")
        return EDITAR_CASA
    if campo == "esporte":
        teclado = [[e] for e in ESPORTES] + [[CANCELAR_BTN]]
        await update.message.reply_text("🏅 *Novo esporte:*",
            reply_markup=ReplyKeyboardMarkup(teclado, resize_keyboard=True, one_time_keyboard=True),
            parse_mode="Markdown")
        return EDITAR_CASA
    if campo == "resultado":
        teclado = [["✅ Ganhou","❌ Perdeu"],["↩️ Void","⏳ Pendente"],[CANCELAR_BTN]]
        await update.message.reply_text("📊 *Qual o resultado?*",
            reply_markup=ReplyKeyboardMarkup(teclado, resize_keyboard=True, one_time_keyboard=True),
            parse_mode="Markdown")
        return EDITAR_CASA
    if campo == "freebet":
        teclado_fb = [["✅ Sim, foi freebet","❌ Não foi freebet"],[CANCELAR_BTN]]
        await update.message.reply_text(
            "🎁 *Freebet?*\nEssa aposta foi uma freebet?",
            reply_markup=ReplyKeyboardMarkup(teclado_fb, resize_keyboard=True, one_time_keyboard=True),
            parse_mode="Markdown")
        return EDITAR_CASA
    if campo == "cashout":
        await update.message.reply_text(
            "💸 *Cashout*\nDigite o valor que voce *recebeu* de volta (R$):\n"
            "Ex: apostou R$50, fez cashout por R$30 -> digite *30*\n"
            "Se perdeu tudo -> digite *0*",
            reply_markup=teclado_cancelar(), parse_mode="Markdown")
        return EDITAR_VALOR
    dicas = {"data":"Nova data (DD/MM/AAAA) ou 0 para hoje:","horario":"Novo horário (HH:MM) ou 0 para agora:",
             "descricao":"Nova descrição:","odd":"Nova odd:","stake":"Novo stake (R$):"}
    await update.message.reply_text(dicas[campo], reply_markup=teclado_cancelar())
    return EDITAR_VALOR

async def editar_receber_valor(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw   = update.message.text.strip()
    if raw == CANCELAR_BTN: return await cancelar(update, ctx)
    campo   = ctx.user_data["editar_campo"]
    id_alvo = ctx.user_data["editar_id"]
    novo_valor = None
    if campo == "data":
        if raw == "0": novo_valor = datetime.now().strftime("%Y-%m-%d")
        else:
            for fmt in ("%d/%m/%Y","%d/%m/%y"):
                try: novo_valor = datetime.strptime(raw, fmt).strftime("%Y-%m-%d"); break
                except ValueError: pass
            if not novo_valor:
                await update.message.reply_text("❌ Data inválida:")
                return EDITAR_VALOR
    elif campo == "horario":
        novo_valor = datetime.now().strftime("%H:%M") if raw == "0" else raw
    elif campo in ("odd","stake","cashout"):
        try: novo_valor = float(raw.replace(",","."))
        except ValueError:
            await update.message.reply_text("❌ Digite um número:")
            return EDITAR_VALOR
        if campo == "cashout":
            aposta   = buscar_por_id(id_alvo)
            stake    = float(aposta["stake"])
            recebido = novo_valor
            lucro_co = recebido - stake
            atualizar_campo(id_alvo, "resultado", "void")
            sinal    = "+" if lucro_co >= 0 else ""
            emoji_co = "📈" if lucro_co >= 0 else "📉"
            await update.message.reply_text(
                f"💸 *Cashout registrado!*\n\n"
                f"💰 Apostado: R$ {stake:.2f}\n"
                f"💸 Recebido: R$ {recebido:.2f}\n"
                f"{emoji_co} Resultado: *{sinal}R$ {lucro_co:.2f}*",
                parse_mode="Markdown")
            return await voltar_menu(update, ctx)
    else:
        novo_valor = raw
    return await aplicar_edicao(update, ctx, id_alvo, campo, novo_valor)

async def editar_receber_casa(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw   = update.message.text.strip()
    if raw == CANCELAR_BTN: return await cancelar(update, ctx)
    campo   = ctx.user_data["editar_campo"]
    id_alvo = ctx.user_data["editar_id"]
    if campo == "resultado":
        mapa = {"✅ Ganhou":"ganhou","❌ Perdeu":"perdeu","↩️ Void":"void","⏳ Pendente":"pendente"}
        valor = mapa.get(raw, raw)
        return await aplicar_edicao(update, ctx, id_alvo, "resultado", valor)
    if campo == "freebet":
        valor = "sim" if raw == "✅ Sim, foi freebet" else "nao"
        return await aplicar_edicao(update, ctx, id_alvo, "freebet", valor)
    if campo == "freebet":
        valor = True if raw == "✅ Sim, foi freebet" else False
        return await aplicar_edicao(update, ctx, id_alvo, "freebet", valor)
    if raw == "Outra" or raw == "Outro":
        await update.message.reply_text("Digite o nome:", reply_markup=teclado_cancelar())
        ctx.user_data["editar_campo"] = campo + "_custom"
        return EDITAR_VALOR
    return await aplicar_edicao(update, ctx, id_alvo, campo, raw)

async def aplicar_edicao(update, ctx, id_alvo, campo, novo_valor):
    campo_real = campo.replace("_custom","")
    if campo_real == "casa" and isinstance(novo_valor, str) and novo_valor not in CASAS:
        novo_valor = novo_valor.strip().title()
    atualizar_campo(id_alvo, campo_real, novo_valor)
    label = CAMPOS_LABEL.get(campo_real, campo_real)
    exibe = novo_valor
    if campo_real == "data" and isinstance(novo_valor,str) and len(novo_valor)==10:
        exibe = datetime.strptime(novo_valor,"%Y-%m-%d").strftime("%d/%m/%Y")
    elif campo_real in ("odd","stake"):
        exibe = f"{float(novo_valor):.2f}"
    # Escolher emoji baseado no resultado se campo for resultado
    emoji_conf = "✅"
    if campo_real == "resultado":
        if novo_valor == "perdeu": emoji_conf = "❌"
        elif novo_valor == "void": emoji_conf = "↩️"
        elif novo_valor == "pendente": emoji_conf = "⏳"
    await update.message.reply_text(f"{emoji_conf} *Aposta #{id_alvo} atualizada!*\n{label} → *{exibe}*", parse_mode="Markdown")
    return await voltar_menu(update, ctx)


# ── GERAR DASHBOARD ───────────────────────────────────────────────────────────
async def gerar_dashboard(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Gerando dashboard, aguarde...")

    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.chart import LineChart, Reference
    from openpyxl.utils import get_column_letter
    from datetime import timedelta

    apostas = carregar()
    apostas_ord = sorted(apostas, key=lambda a: (str(a["data"]), a.get("horario") or "99:99", int(a["id"])))
    df_res = [a for a in apostas_ord if a["resultado"] in ("ganhou","perdeu")]

    BANCA = 5000
    DARK  = "1E293B"; GREEN = "16A34A"; RED = "DC2626"; AMBER = "D97706"
    WHITE = "FFFFFF"; ALT   = "EFF6FF"; BORDER_C = "CBD5E1"

    def est(cell, bold=False, fc="FFFFFF", bg=None, size=11, align="center"):
        cell.font = Font(name="Arial", bold=bold, color=fc, size=size)
        cell.alignment = Alignment(horizontal=align, vertical="center")
        if bg: cell.fill = PatternFill("solid", start_color=bg)

    def brd():
        s = Side(style="thin", color=BORDER_C)
        return Border(left=s, right=s, top=s, bottom=s)

    def cor(val):
        try: return GREEN if float(val) >= 0 else RED
        except: return "000000"

    # Métricas
    lucro_total = sum(
        lucro_aposta(a) for a in df_res
    )
    stake_total = sum(float(a["stake"]) for a in df_res)
    vitorias    = sum(1 for a in df_res if a["resultado"]=="ganhou")
    win_rate    = vitorias/len(df_res) if df_res else 0
    roi         = lucro_total/stake_total if stake_total else 0
    progressao  = lucro_total/BANCA
    total       = len(apostas)
    resolvidas  = len(df_res)
    pendentes   = total - resolvidas

    # Banca acumulada
    banca_acum = []
    acum = 0
    for a in df_res:
        acum += lucro_aposta(a)
        banca_acum.append(acum)

    wb = openpyxl.Workbook()

    # ── ABA 1: DASHBOARD ──
    ws = wb.active; ws.title = "Dashboard"
    ws.merge_cells("A1:L1"); ws["A1"] = "DASHBOARD DE APOSTAS"
    est(ws["A1"], bold=True, bg=DARK, size=16); ws.row_dimensions[1].height = 40
    ws.row_dimensions[2].height = 8

    stake_curso = sum(float(a["stake"]) for a in apostas if a["resultado"]=="pendente")
    cards = [
        ("Total",str(total),DARK),("Resolvidas",str(resolvidas),DARK),
        ("Pendentes",str(pendentes),AMBER if pendentes else DARK),
        ("Win Rate",f"{win_rate:.1%}",GREEN if win_rate>=0.5 else RED),
        ("Lucro",f"R$ {lucro_total:.2f}",GREEN if lucro_total>=0 else RED),
        ("ROI",f"{roi:+.1%}",GREEN if roi>=0 else RED),
        ("Progressao",f"{progressao:+.2%}",GREEN if progressao>=0 else RED),
        ("Em Curso",f"R$ {stake_curso:.2f}",AMBER if stake_curso>0 else DARK),
    ]
    for i,(label,val,bg) in enumerate(cards,1):
        c=ws.cell(row=3,column=i,value=label); est(c,bg=bg,size=9,fc="DBEAFE"); ws.row_dimensions[3].height=20
        v=ws.cell(row=4,column=i,value=val);   est(v,bold=True,bg=bg,size=13);  ws.row_dimensions[4].height=32
        d=ws.cell(row=5,column=i);              est(d,bg="0F172A");              ws.row_dimensions[5].height=4
    ws.row_dimensions[6].height=10

    HDR=7; DAT=8
    headers=["#","Data","Hora","Descricao","Odd","Stake","Esporte","Casa","Resultado","Lucro","Banca","Progressao"]
    ws.row_dimensions[HDR].height=22
    for c,h in enumerate(headers,1):
        cell=ws.cell(row=HDR,column=c,value=h); est(cell,bold=True,bg=DARK,size=10); cell.border=brd()

    acum2=0
    for i,a in enumerate(apostas_ord):
        er=DAT+i; ws.row_dimensions[er].height=18
        rb=WHITE if i%2==0 else ALT
        res=a["resultado"]
        lucro_a=banca_a=prog_a=""
        if res in ("ganhou","perdeu"):
            lucro_a=lucro_aposta(a) if res in ("ganhou","perdeu") else ""
            acum2+=lucro_a; banca_a=round(acum2,2); prog_a=round(acum2/BANCA,4)
        res_d={"ganhou":"Ganhou","perdeu":"Perdeu","void":"Void","pendente":"Pendente"}.get(res,res)
        data_fmt=a["data"].strftime("%d/%m/%Y") if hasattr(a["data"],"strftime") else str(a["data"])[:10]
        vals=[a["id"],data_fmt,a.get("horario",""),a["descricao"],a["odd"],a["stake"],
              a.get("esporte",""),a.get("casa",""),res_d,lucro_a,banca_a,prog_a]
        for c,val in enumerate(vals,1):
            cell=ws.cell(row=er,column=c,value=val)
            fc="000000"
            if c in (10,11) and val!="": fc=cor(val)
            if c==12 and val!="": fc=cor(val)
            cell.font=Font(name="Arial",size=10,color=fc)
            cell.alignment=Alignment(horizontal="left" if c==4 else "center",vertical="center")
            cell.fill=PatternFill("solid",start_color=rb); cell.border=brd()
            if c in (5,6,10,11) and val!="": cell.number_format="#,##0.00"
            if c==12 and val!="": cell.number_format="+0.00%;-0.00%;0.00%"

    tr=DAT+len(apostas_ord); ws.row_dimensions[tr].height=20
    for c in range(1,13):
        cell=ws.cell(row=tr,column=c)
        if c==4: cell.value="TOTAL"
        elif c==6: cell.value=f"=SUM(F{DAT}:F{tr-1})"; cell.number_format="#,##0.00"
        elif c==10: cell.value=f"=SUM(J{DAT}:J{tr-1})"; cell.number_format="#,##0.00"
        est(cell,bold=True,bg=DARK,size=10); cell.border=brd()

    widths=[5,12,7,34,7,11,14,14,12,12,14,13]
    for i,w in enumerate(widths,1): ws.column_dimensions[get_column_letter(i)].width=w

    # ── ABA 2: LUCRO POR DIA ──
    from collections import defaultdict
    por_dia=defaultdict(list)
    for a in df_res:
        por_dia[str(a["data"])[:10]].append(a)
    wd=wb.create_sheet("Lucro por Dia")
    wd.merge_cells("A1:D1"); wd["A1"]="LUCRO POR DIA"
    est(wd["A1"],bold=True,bg=DARK,size=14); wd.row_dimensions[1].height=34; wd.row_dimensions[2].height=8
    for c,h in enumerate(["Data","Apostas","Lucro do Dia","Acumulado"],1):
        cell=wd.cell(row=3,column=c,value=h); est(cell,bold=True,bg=DARK,size=10); cell.border=brd()
    wd.row_dimensions[3].height=22
    acum3=0
    for i,(data_k,ap) in enumerate(sorted(por_dia.items())):
        er=4+i; rb=WHITE if i%2==0 else ALT
        lucro_d=sum(lucro_aposta(a) for a in ap)
        acum3+=lucro_d
        try: data_f=__import__("datetime").datetime.strptime(data_k,"%Y-%m-%d").strftime("%d/%m/%Y")
        except: data_f=data_k
        for c,val in enumerate([data_f,len(ap),lucro_d,acum3],1):
            cell=wd.cell(row=er,column=c,value=val); fc="000000"
            if c in (3,4): fc=cor(val)
            cell.font=Font(name="Arial",size=10,color=fc)
            cell.alignment=Alignment(horizontal="center",vertical="center")
            cell.fill=PatternFill("solid",start_color=rb); cell.border=brd()
            if c in (3,4): cell.number_format="#,##0.00"
        wd.row_dimensions[er].height=18
    for i,w in enumerate([14,10,18,18],1): wd.column_dimensions[get_column_letter(i)].width=w

    # ── ABA 3: POR CASA ──
    casas={}
    for a in df_res:
        casa=normalizar_casa(a.get("casa"))
        if casa not in casas: casas[casa]={"ap":0,"g":0,"stake":0.0,"lucro":0.0}
        c=casas[casa]; c["ap"]+=1; c["stake"]+=float(a["stake"])
        if a["resultado"]=="ganhou": c["g"]+=1; c["lucro"]+=lucro_aposta(a)
        else: c["lucro"]+=lucro_aposta(a)
    wc=wb.create_sheet("Por Casa")
    wc.merge_cells("A1:H1"); wc["A1"]="POR CASA"
    est(wc["A1"],bold=True,bg=DARK,size=14); wc.row_dimensions[1].height=34; wc.row_dimensions[2].height=8
    for c,h in enumerate(["Casa","Apostas","Ganhou","Perdeu","Stake","Lucro","ROI","Win Rate"],1):
        cell=wc.cell(row=3,column=c,value=h); est(cell,bold=True,bg=DARK,size=10); cell.border=brd()
    wc.row_dimensions[3].height=22
    for i,(nome,c) in enumerate(sorted(casas.items(),key=lambda x:-x[1]["lucro"])):
        er=4+i; rb=WHITE if i%2==0 else ALT
        roi_c=c["lucro"]/c["stake"] if c["stake"] else 0
        wr_c=c["g"]/c["ap"] if c["ap"] else 0
        for col,val in enumerate([nome,c["ap"],c["g"],c["ap"]-c["g"],c["stake"],c["lucro"],roi_c,wr_c],1):
            cell=wc.cell(row=er,column=col,value=val); fc="000000"
            if col==6: fc=cor(val)
            if col==7: fc=cor(val)
            cell.font=Font(name="Arial",size=10,color=fc)
            cell.alignment=Alignment(horizontal="left" if col==1 else "center",vertical="center")
            cell.fill=PatternFill("solid",start_color=rb); cell.border=brd()
            if col in (5,6): cell.number_format="#,##0.00"
            if col in (7,8): cell.number_format="+0.0%;-0.0%;0.0%"
        wc.row_dimensions[er].height=18
    for i,w in enumerate([18,10,10,10,14,14,10,12],1): wc.column_dimensions[get_column_letter(i)].width=w

    # ── ABA 4: POR ESPORTE ──
    esportes={}
    for a in df_res:
        esp=a.get("esporte") or "Sem esporte"
        if esp not in esportes: esportes[esp]={"ap":0,"g":0,"stake":0.0,"lucro":0.0}
        e=esportes[esp]; e["ap"]+=1; e["stake"]+=float(a["stake"])
        if a["resultado"]=="ganhou": e["g"]+=1; e["lucro"]+=lucro_aposta(a)
        else: e["lucro"]+=lucro_aposta(a)
    we=wb.create_sheet("Por Esporte")
    we.merge_cells("A1:H1"); we["A1"]="POR ESPORTE"
    est(we["A1"],bold=True,bg=DARK,size=14); we.row_dimensions[1].height=34; we.row_dimensions[2].height=8
    for c,h in enumerate(["Esporte","Apostas","Ganhou","Perdeu","Stake","Lucro","ROI","Win Rate"],1):
        cell=we.cell(row=3,column=c,value=h); est(cell,bold=True,bg=DARK,size=10); cell.border=brd()
    we.row_dimensions[3].height=22
    for i,(nome,e) in enumerate(sorted(esportes.items(),key=lambda x:-x[1]["lucro"])):
        er=4+i; rb=WHITE if i%2==0 else ALT
        roi_e=e["lucro"]/e["stake"] if e["stake"] else 0
        wr_e=e["g"]/e["ap"] if e["ap"] else 0
        for col,val in enumerate([nome,e["ap"],e["g"],e["ap"]-e["g"],e["stake"],e["lucro"],roi_e,wr_e],1):
            cell=we.cell(row=er,column=col,value=val); fc="000000"
            if col==6: fc=cor(val)
            if col==7: fc=cor(val)
            cell.font=Font(name="Arial",size=10,color=fc)
            cell.alignment=Alignment(horizontal="left" if col==1 else "center",vertical="center")
            cell.fill=PatternFill("solid",start_color=rb); cell.border=brd()
            if col in (5,6): cell.number_format="#,##0.00"
            if col in (7,8): cell.number_format="+0.0%;-0.0%;0.0%"
        we.row_dimensions[er].height=18
    for i,w in enumerate([18,10,10,10,14,14,10,12],1): we.column_dimensions[get_column_letter(i)].width=w

    # ── ABA 5: POR SEMANA ──
    def semana_num(dt):
        if hasattr(dt,"strftime"): d=dt
        else: d=__import__("datetime").datetime.strptime(str(dt)[:10],"%Y-%m-%d")
        dom=d-timedelta(days=(d.weekday()+1)%7)
        return dom.isocalendar()[0],dom.isocalendar()[1]
    por_sem=defaultdict(list)
    for a in df_res:
        ano,num=semana_num(a["data"])
        por_sem[(ano,num)].append(a)
    ws2=wb.create_sheet("Por Semana")
    ws2.merge_cells("A1:H1"); ws2["A1"]="POR SEMANA"
    est(ws2["A1"],bold=True,bg=DARK,size=14); ws2.row_dimensions[1].height=34; ws2.row_dimensions[2].height=8
    for c,h in enumerate(["Semana","Periodo","Apostas","Ganhou","Perdeu","Stake","Lucro","ROI"],1):
        cell=ws2.cell(row=3,column=c,value=h); est(cell,bold=True,bg=DARK,size=10); cell.border=brd()
    ws2.row_dimensions[3].height=22
    for i,((ano,num),ap) in enumerate(sorted(por_sem.items())):
        er=4+i; rb=WHITE if i%2==0 else ALT
        lucro_s=sum(lucro_aposta(a) for a in ap)
        stake_s=sum(float(a["stake"]) for a in ap)
        g_s=sum(1 for a in ap if a["resultado"]=="ganhou")
        roi_s=lucro_s/stake_s if stake_s else 0
        import datetime as dt_mod
        jan1=dt_mod.datetime(ano,1,1); iso1=jan1.isocalendar()
        primeira_seg=jan1-timedelta(days=iso1[2]-1)
        seg=primeira_seg+timedelta(weeks=num-1); dom=seg-timedelta(days=1); sab=dom+timedelta(days=6)
        periodo=f"{dom.strftime('%d/%m')} - {sab.strftime('%d/%m/%Y')}"
        for col,val in enumerate([f"Semana {num}",periodo,len(ap),g_s,len(ap)-g_s,stake_s,lucro_s,roi_s],1):
            cell=ws2.cell(row=er,column=col,value=val); fc="000000"
            if col==7: fc=cor(val)
            if col==8: fc=cor(val)
            cell.font=Font(name="Arial",size=10,color=fc)
            cell.alignment=Alignment(horizontal="left" if col==2 else "center",vertical="center")
            cell.fill=PatternFill("solid",start_color=rb); cell.border=brd()
            if col in (6,7): cell.number_format="#,##0.00"
            if col==8: cell.number_format="+0.0%;-0.0%;0.0%"
        ws2.row_dimensions[er].height=18
    for i,w in enumerate([12,22,10,10,10,14,14,10],1): ws2.column_dimensions[get_column_letter(i)].width=w

    # ── ABA 6: GRAFICO ──
    wg=wb.create_sheet("Evolucao da Banca")
    wg["A1"]="Aposta #"; wg["B1"]="Banca Acumulada"
    for i,(val) in enumerate(banca_acum,2):
        wg.cell(row=i,column=1,value=i-1); wg.cell(row=i,column=2,value=round(val,2))
    if len(banca_acum)>=2:
        chart=LineChart(); chart.title="Evolucao da Banca"; chart.style=10
        chart.y_axis.title="R$"; chart.x_axis.title="Aposta #"
        chart.y_axis.numFmt="#,##0.00"; chart.width=26; chart.height=14
        dr=Reference(wg,min_col=2,min_row=1,max_row=len(banca_acum)+1)
        cr=Reference(wg,min_col=1,min_row=2,max_row=len(banca_acum)+1)
        chart.add_data(dr,titles_from_data=True); chart.set_categories(cr)
        chart.series[0].graphicalProperties.line.solidFill="2563EB"
        chart.series[0].graphicalProperties.line.width=22000
        wg.add_chart(chart,"D2")

    # Salvar na memória e enviar
    buf=io.BytesIO(); wb.save(buf); buf.seek(0)
    from datetime import datetime as dt2
    nome_arq=f"dashboard_{dt2.now().strftime('%d%m%Y_%H%M')}.xlsx"
    await update.message.reply_document(
        document=InputFile(buf, filename=nome_arq),
        caption=f"Dashboard gerado! {total} apostas | R$ {lucro_total:.2f} lucro | ROI {roi:+.1%}"
    )

# ── EXPORTAR CSV ──────────────────────────────────────────────────────────────
async def exportar_csv(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    apostas   = carregar()
    total     = len(apostas)
    pendentes = sum(1 for a in apostas if a["resultado"] == "pendente")
    import io
    output = io.StringIO()
    import csv as csv_mod
    writer = csv_mod.writer(output, quoting=csv_mod.QUOTE_ALL)
    writer.writerow(["id","data","horario","descricao","odd","stake","resultado","casa","esporte"])
    for a in apostas:
        data = a["data"].strftime("%Y-%m-%d") if hasattr(a["data"],"strftime") else str(a["data"])
        writer.writerow([a["id"],data,a.get("horario",""),a["descricao"],a["odd"],a["stake"],a["resultado"],a.get("casa",""),a.get("esporte","")])
    csv_bytes = output.getvalue().encode("utf-8")
    caption   = f"\U0001f4ca apostas.csv\n{total} apostas | {pendentes} pendentes"
    await update.message.reply_document(document=InputFile(csv_bytes, filename="apostas.csv"), caption=caption)

# ── SERVIDOR KEEP-ALIVE ───────────────────────────────────────────────────────
class PingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
    def do_HEAD(self):
        self.send_response(200); self.end_headers()
    def log_message(self, *args): pass

def iniciar_servidor():
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(("0.0.0.0", port), PingHandler).serve_forever()

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    inicializar_db()
    app = Application.builder().token(TOKEN).build()

    fallbacks_padrao = [
        CommandHandler("cancelar", cancelar),
        MessageHandler(filters.Regex(f"^{CANCELAR_BTN}$"), cancelar),
    ]

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
        fallbacks=fallbacks_padrao,
    )

    conv_editar = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^✏️ Editar aposta$"), editar_inicio)],
        states={
            EDITAR_ID:    [MessageHandler(filters.TEXT & ~filters.COMMAND, editar_receber_id)],
            EDITAR_CAMPO: [MessageHandler(filters.TEXT & ~filters.COMMAND, editar_receber_campo)],
            EDITAR_VALOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, editar_receber_valor)],
            EDITAR_CASA:  [MessageHandler(filters.TEXT & ~filters.COMMAND, editar_receber_casa)],
        },
        fallbacks=fallbacks_padrao,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("exportar", exportar_csv))
    app.add_handler(conv_nova)
    app.add_handler(conv_editar)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_botao))

    threading.Thread(target=iniciar_servidor, daemon=True).start()
    print("🤖 Bot rodando! Abra o Telegram e mande /start")
    app.run_polling()

if __name__ == "__main__":
    main()
