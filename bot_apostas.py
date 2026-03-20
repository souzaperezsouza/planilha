import csv
import os
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InputFile
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ConversationHandler, ContextTypes, filters
)

TOKEN         = "8790751046:AAG-AsvU3V-K5j4U8IOUQrpT6NXX8K3FcjU"
CSV_FILE      = "apostas.csv"
CAMPOS        = ["id", "data", "horario", "descricao", "odd", "stake", "resultado", "casa", "esporte"]
BANCA_INICIAL = 5000

ESPORTES = [
    "⚽ Futebol", "🏀 Basquete", "🎾 Tênis", "🏒 Hóquei",
    "🏈 Futebol Americano", "⚾ Beisebol", "🥊 MMA/Boxe", "🏐 Vôlei", "Outro"
]

CASAS = [
    "Bet365", "Betano", "SportingBet", "Novibet", "Vaidebet",
    "Betfast", "BETesporte", "Betao", "Betnacional", "BetFair",
    "Stake", "Pagol", "Vupi", "Outra"
]

logging.basicConfig(level=logging.WARNING)

# Estados
DATA, HORARIO, DESCRICAO, ODD, STAKE, ESPORTE, CASA  = range(7)
ATUALIZAR_ID, ATUALIZAR_RES                          = range(7, 9)
EDITAR_ID, EDITAR_CAMPO, EDITAR_VALOR, EDITAR_CASA   = range(9, 13)

CANCELAR_BTN = "❌ Cancelar"

def teclado_cancelar():
    return ReplyKeyboardMarkup([[CANCELAR_BTN]], resize_keyboard=True)

def teclado_menu():
    return ReplyKeyboardMarkup([
        ["📝 Nova aposta",     "✅ Atualizar resultado"],
        ["⏳ Ver pendentes",   "📊 Resumo"],
        ["📋 Últimas apostas", "🏦 Por casa"],
        ["✏️ Editar aposta",  "📤 Exportar CSV"],
    ], resize_keyboard=True)

# ── CSV ───────────────────────────────────────────────────────────────────────
def carregar():
    if not os.path.exists(CSV_FILE):
        return []
    with open(CSV_FILE, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
        for r in rows:
            if "casa"    not in r: r["casa"]    = ""
            if "horario" not in r: r["horario"] = ""
            if "esporte" not in r: r["esporte"] = ""
        return rows

def salvar(apostas):
    with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS)
        w.writeheader()
        w.writerows(apostas)

def proximo_id(apostas):
    if not apostas:
        return 1
    return max(int(a["id"]) for a in apostas) + 1

# ── Métricas ─────────────────────────────────────────────────────────────────
def calcular_metricas(apostas):
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

# ── /start e menu ─────────────────────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Gestor de Apostas*\n\nEscolha uma opção:",
        reply_markup=teclado_menu(),
        parse_mode="Markdown"
    )

async def menu_botao(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text
    if txt == "📝 Nova aposta":        return await nova_aposta_inicio(update, ctx)
    if txt == "✅ Atualizar resultado": return await atualizar_inicio(update, ctx)
    if txt == "⏳ Ver pendentes":      return await ver_pendentes(update, ctx)
    if txt == "📊 Resumo":             return await resumo(update, ctx)
    if txt == "📋 Últimas apostas":    return await ultimas(update, ctx)
    if txt == "🏦 Por casa":           return await por_casa(update, ctx)
    if txt == "✏️ Editar aposta":      return await editar_inicio(update, ctx)
    if txt == "📤 Exportar CSV":        return await exportar_csv(update, ctx)

# ── NOVA APOSTA ───────────────────────────────────────────────────────────────
async def nova_aposta_inicio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    hoje = datetime.now().strftime("%d/%m/%Y")
    await update.message.reply_text(
        f"📅 *Data do jogo*\n"
        f"Hoje é *{hoje}* — mande *0* para confirmar ou digite outra data (DD/MM/AAAA):",
        reply_markup=teclado_cancelar(),
        parse_mode="Markdown"
    )
    return DATA

async def receber_data(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    if raw == CANCELAR_BTN:
        return await cancelar(update, ctx)
    if raw == "0":
        ctx.user_data["data"] = datetime.now().strftime("%Y-%m-%d")
    else:
        ok = False
        for fmt in ("%d/%m/%Y", "%d/%m/%y"):
            try:
                ctx.user_data["data"] = datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
                ok = True
                break
            except ValueError:
                pass
        if not ok:
            await update.message.reply_text("❌ Data inválida. Use DD/MM/AAAA ou 0 para hoje:")
            return DATA

    agora = datetime.now().strftime("%H:%M")
    await update.message.reply_text(
        f"⏰ *Horário do jogo*\n"
        f"Agora são *{agora}* — mande *0* para usar esse horário ou digite outro (HH:MM):",
        reply_markup=teclado_cancelar(),
        parse_mode="Markdown"
    )
    return HORARIO

async def receber_horario(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    if raw == CANCELAR_BTN:
        return await cancelar(update, ctx)
    if raw == "0":
        ctx.user_data["horario"] = datetime.now().strftime("%H:%M")
    else:
        try:
            ctx.user_data["horario"] = datetime.strptime(raw, "%H:%M").strftime("%H:%M")
        except ValueError:
            await update.message.reply_text("❌ Use HH:MM (ex: 21:30) ou 0 para agora:")
            return HORARIO

    await update.message.reply_text(
        "🏷 *Descrição da aposta:*",
        reply_markup=teclado_cancelar(),
        parse_mode="Markdown"
    )
    return DESCRICAO

async def receber_descricao(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message.text.strip() == CANCELAR_BTN:
        return await cancelar(update, ctx)
    ctx.user_data["descricao"] = update.message.text.strip()
    await update.message.reply_text(
        "🔢 *Odd:*",
        reply_markup=teclado_cancelar(),
        parse_mode="Markdown"
    )
    return ODD

async def receber_odd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    if raw == CANCELAR_BTN:
        return await cancelar(update, ctx)
    try:
        ctx.user_data["odd"] = float(raw.replace(",", "."))
        await update.message.reply_text(
            "💰 *Stake (R$):*",
            reply_markup=teclado_cancelar(),
            parse_mode="Markdown"
        )
        return STAKE
    except ValueError:
        await update.message.reply_text("❌ Odd inválida. Digite um número:")
        return ODD

async def receber_stake(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    if raw == CANCELAR_BTN:
        return await cancelar(update, ctx)
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
        await update.message.reply_text("❌ Stake inválido. Digite um número:")
        return STAKE

async def receber_esporte(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    if raw == CANCELAR_BTN:
        return await cancelar(update, ctx)
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
    if casa == CANCELAR_BTN:
        return await cancelar(update, ctx)
    if casa == "Outra":
        await update.message.reply_text("Digite o nome da casa:", reply_markup=teclado_cancelar())
        ctx.user_data["aguardando_casa_custom"] = True
        return CASA
    ctx.user_data.pop("aguardando_casa_custom", False)
    ctx.user_data["casa"] = casa

    apostas = carregar()
    nova = {
        "id":        proximo_id(apostas),
        "data":      ctx.user_data["data"],
        "horario":   ctx.user_data.get("horario", ""),
        "descricao": ctx.user_data["descricao"],
        "odd":       ctx.user_data["odd"],
        "stake":     ctx.user_data["stake"],
        "resultado": "pendente",
        "casa":      ctx.user_data["casa"],
        "esporte":   ctx.user_data.get("esporte", ""),
    }
    apostas.append(nova)
    salvar(apostas)

    data_fmt = datetime.strptime(nova["data"], "%Y-%m-%d").strftime("%d/%m/%Y")
    hora_txt = f" às {nova['horario']}" if nova.get("horario") else ""
    await update.message.reply_text(
        f"✅ *Aposta #{nova['id']} salva!*\n\n"
        f"📅 {data_fmt}{hora_txt}\n"
        f"🏅 {nova.get('esporte','')}\n"
        f"🏷 {nova['descricao']}\n"
        f"🔢 Odd: {nova['odd']}\n"
        f"💰 Stake: R$ {float(nova['stake']):.2f}\n"
        f"🏦 Casa: {nova['casa']}",
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

    pendentes.sort(key=lambda a: a["data"])
    linhas = ["⏳ *Apostas pendentes:*\n"]
    for a in pendentes:
        data_fmt = datetime.strptime(a["data"], "%Y-%m-%d").strftime("%d/%m")
        hora = f" {a.get('horario','')}h" if a.get("horario") else ""
        linhas.append(f"*#{a['id']}* — {data_fmt}{hora} — {a['descricao'][:35]} (odd {a['odd']})")
    linhas.append("\nDigite o *ID* da aposta a atualizar:")
    await update.message.reply_text(
        "\n".join(linhas),
        reply_markup=teclado_cancelar(),
        parse_mode="Markdown"
    )
    return ATUALIZAR_ID

async def receber_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    if raw == CANCELAR_BTN:
        return await cancelar(update, ctx)
    try:
        id_alvo = int(raw)
    except ValueError:
        await update.message.reply_text("❌ Digite só o número do ID:")
        return ATUALIZAR_ID

    apostas = carregar()
    aposta  = next((a for a in apostas if int(a["id"]) == id_alvo and a["resultado"] == "pendente"), None)
    if not aposta:
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
    if txt == CANCELAR_BTN:
        return await cancelar(update, ctx)
    mapa = {"✅ Ganhou": "ganhou", "❌ Perdeu": "perdeu", "↩️ Void": "void"}
    res  = mapa.get(txt)
    if not res:
        await update.message.reply_text("Escolha uma das opções:")
        return ATUALIZAR_RES

    id_alvo = ctx.user_data["id_alvo"]
    apostas = carregar()
    aposta  = next((a for a in apostas if int(a["id"]) == id_alvo), None)
    aposta["resultado"] = res
    salvar(apostas)

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
    pendentes = sorted([a for a in apostas if a["resultado"] == "pendente"], key=lambda a: (a["data"], a.get("horario","")))
    if not pendentes:
        await update.message.reply_text("✅ Nenhuma aposta pendente!")
        return
    linhas = [f"⏳ *{len(pendentes)} apostas pendentes:*\n"]
    for a in pendentes:
        data_fmt = datetime.strptime(a["data"], "%Y-%m-%d").strftime("%d/%m/%Y")
        hora = f" {a.get('horario','')}" if a.get("horario") else ""
        linhas.append(f"*#{a['id']}* {data_fmt}{hora} | odd {a['odd']} | R${float(a['stake']):.0f} | {a.get('casa','')}\n_{a['descricao']}_\n")
    await update.message.reply_text("\n".join(linhas), parse_mode="Markdown")

# ── RESUMO ────────────────────────────────────────────────────────────────────
async def resumo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    apostas = carregar()
    m = calcular_metricas(apostas)
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
    ultimas_10 = sorted(apostas, key=lambda a: (a["data"], int(a["id"])), reverse=True)[:10]
    emojis = {"ganhou": "✅", "perdeu": "❌", "pendente": "⏳", "void": "↩️"}
    linhas = ["📋 *Últimas 10 apostas:*\n"]
    for a in ultimas_10:
        data_fmt = datetime.strptime(a["data"], "%Y-%m-%d").strftime("%d/%m")
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
        casa = a.get("casa", "") or "Sem casa"
        if casa not in casas:
            casas[casa] = {"ap": 0, "g": 0, "stake": 0.0, "lucro": 0.0}
        c = casas[casa]
        c["ap"]    += 1
        c["stake"] += float(a["stake"])
        if a["resultado"] == "ganhou":
            c["g"]     += 1
            c["lucro"] += float(a["stake"]) * (float(a["odd"]) - 1)
        else:
            c["lucro"] -= float(a["stake"])
    ordenadas = sorted(casas.items(), key=lambda x: x[1]["lucro"], reverse=True)
    linhas = ["🏦 *Desempenho por Casa:*\n"]
    for nome, c in ordenadas:
        roi   = c["lucro"] / c["stake"] if c["stake"] else 0
        emoji = "🟢" if c["lucro"] >= 0 else "🔴"
        linhas.append(
            f"{emoji} *{nome}*\n"
            f"  {c['ap']} apostas | {c['g']}V/{c['ap']-c['g']}D\n"
            f"  Lucro: {'+'if c['lucro']>=0 else ''}R$ {c['lucro']:.2f} | ROI: {roi:+.1%}\n"
        )
    await update.message.reply_text("\n".join(linhas), parse_mode="Markdown")


# ── EDITAR APOSTA ─────────────────────────────────────────────────────────────
CAMPOS_EDITAVEIS = ["data", "horario", "descricao", "odd", "stake", "esporte", "casa"]
CAMPOS_LABEL     = {
    "data":      "📅 Data",
    "horario":   "⏰ Horário",
    "descricao": "🏷 Descrição",
    "odd":       "🔢 Odd",
    "stake":     "💰 Stake",
    "esporte":   "🏅 Esporte",
    "casa":      "🏦 Casa",
}

async def editar_inicio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    apostas   = carregar()
    pendentes = sorted([a for a in apostas if a["resultado"] == "pendente"],
                       key=lambda a: (a["data"], a.get("horario", "")))
    emojis    = {"ganhou": "✅", "perdeu": "❌", "pendente": "⏳", "void": "↩️"}
    linhas    = ["✏️ *Editar aposta*\n"]

    if pendentes:
        linhas.append("*Apostas pendentes:*\n")
        for a in pendentes:
            data_fmt = datetime.strptime(a["data"], "%Y-%m-%d").strftime("%d/%m")
            hora     = f" {a.get('horario','')}" if a.get("horario") else ""
            linhas.append(f"⏳ *#{a['id']}* {data_fmt}{hora} | odd {a['odd']} | _{a['descricao'][:30]}_")
        linhas.append("")
    else:
        linhas.append("Nenhuma aposta pendente.\n")

    linhas.append("Digite o *ID* da aposta que quer editar\n_(pode ser qualquer aposta, não só as pendentes)_:")
    await update.message.reply_text(
        "\n".join(linhas),
        reply_markup=teclado_cancelar(),
        parse_mode="Markdown"
    )
    return EDITAR_ID

async def editar_receber_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    if raw == CANCELAR_BTN:
        return await cancelar(update, ctx)
    try:
        id_alvo = int(raw)
    except ValueError:
        await update.message.reply_text("❌ Digite só o número do ID:")
        return EDITAR_ID

    apostas = carregar()
    aposta  = next((a for a in apostas if int(a["id"]) == id_alvo), None)
    if not aposta:
        await update.message.reply_text("❌ ID não encontrado.")
        return EDITAR_ID

    ctx.user_data["editar_id"] = id_alvo
    data_fmt = datetime.strptime(aposta["data"], "%Y-%m-%d").strftime("%d/%m/%Y")
    hora     = f" {aposta.get('horario','')}" if aposta.get("horario") else ""

    info = (
        f"✏️ *Aposta #{id_alvo}*\n\n"
        f"📅 {data_fmt}{hora}\n"
        f"🏷 {aposta['descricao']}\n"
        f"🔢 Odd: {aposta['odd']}\n"
        f"💰 Stake: R$ {float(aposta['stake']):.2f}\n"
        f"🏦 Casa: {aposta.get('casa','')}\n"
        f"📊 Resultado: {aposta['resultado']}\n\n"
        f"*Qual campo quer editar?*"
    )
    teclado = [[CAMPOS_LABEL[c]] for c in CAMPOS_EDITAVEIS] + [[CANCELAR_BTN]]
    await update.message.reply_text(
        info,
        reply_markup=ReplyKeyboardMarkup(teclado, resize_keyboard=True, one_time_keyboard=True),
        parse_mode="Markdown"
    )
    return EDITAR_CAMPO

async def editar_receber_campo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    if raw == CANCELAR_BTN:
        return await cancelar(update, ctx)

    campo = next((k for k, v in CAMPOS_LABEL.items() if v == raw), None)
    if not campo:
        await update.message.reply_text("❌ Escolha um dos campos:")
        return EDITAR_CAMPO

    ctx.user_data["editar_campo"] = campo

    if campo == "casa":
        teclado = [[c] for c in CASAS] + [[CANCELAR_BTN]]
        await update.message.reply_text(
            "🏦 *Escolha a nova casa:*",
            reply_markup=ReplyKeyboardMarkup(teclado, resize_keyboard=True, one_time_keyboard=True),
            parse_mode="Markdown"
        )
        return EDITAR_CASA

    dicas = {
        "data":      "Digite a nova data (DD/MM/AAAA) ou 0 para hoje:",
        "horario":   "Digite o novo horário (HH:MM) ou 0 para agora:",
        "descricao": "Digite a nova descrição:",
        "odd":       "Digite a nova odd:",
        "stake":     "Digite o novo stake (R$):",
    }
    await update.message.reply_text(
        dicas[campo],
        reply_markup=teclado_cancelar(),
        parse_mode="Markdown"
    )
    return EDITAR_VALOR

async def editar_receber_valor(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw    = update.message.text.strip()
    if raw == CANCELAR_BTN:
        return await cancelar(update, ctx)
    campo  = ctx.user_data["editar_campo"]
    id_alvo = ctx.user_data["editar_id"]

    # Validar e converter
    novo_valor = None
    if campo == "data":
        if raw == "0":
            novo_valor = datetime.now().strftime("%Y-%m-%d")
        else:
            for fmt in ("%d/%m/%Y", "%d/%m/%y"):
                try:
                    novo_valor = datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
                    break
                except ValueError:
                    pass
            if not novo_valor:
                await update.message.reply_text("❌ Data inválida. Use DD/MM/AAAA ou 0 para hoje:")
                return EDITAR_VALOR
    elif campo == "horario":
        if raw == "0":
            novo_valor = datetime.now().strftime("%H:%M")
        else:
            try:
                novo_valor = datetime.strptime(raw, "%H:%M").strftime("%H:%M")
            except ValueError:
                await update.message.reply_text("❌ Use HH:MM ou 0 para agora:")
                return EDITAR_VALOR
    elif campo in ("odd", "stake"):
        try:
            novo_valor = float(raw.replace(",", "."))
        except ValueError:
            await update.message.reply_text("❌ Digite um número válido:")
            return EDITAR_VALOR
    else:
        novo_valor = raw

    return await aplicar_edicao(update, ctx, id_alvo, campo, novo_valor)

async def editar_receber_casa(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    if raw == CANCELAR_BTN:
        return await cancelar(update, ctx)
    if raw == "Outra":
        await update.message.reply_text("Digite o nome da casa:", reply_markup=teclado_cancelar())
        ctx.user_data["editar_campo"] = "casa_custom"
        return EDITAR_VALOR
    return await aplicar_edicao(update, ctx, ctx.user_data["editar_id"], "casa", raw)

async def aplicar_edicao(update, ctx, id_alvo, campo, novo_valor):
    apostas = carregar()
    aposta  = next((a for a in apostas if int(a["id"]) == id_alvo), None)
    campo_real = "casa" if campo == "casa_custom" else campo
    aposta[campo_real] = novo_valor
    salvar(apostas)

    label = CAMPOS_LABEL.get(campo_real, campo_real)
    exibe = novo_valor
    if campo_real == "data":
        exibe = datetime.strptime(novo_valor, "%Y-%m-%d").strftime("%d/%m/%Y")
    elif campo_real in ("odd", "stake"):
        exibe = f"{float(novo_valor):.2f}"

    await update.message.reply_text(
        f"✅ *Aposta #{id_alvo} atualizada!*\n{label} → *{exibe}*",
        parse_mode="Markdown"
    )
    return await voltar_menu(update, ctx)

# ── EXPORTAR CSV ─────────────────────────────────────────────────────────────
async def exportar_csv(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not os.path.exists(CSV_FILE):
        await update.message.reply_text("Nenhuma aposta cadastrada ainda.")
        return
    apostas   = carregar()
    total     = len(apostas)
    pendentes = sum(1 for a in apostas if a["resultado"] == "pendente")
    caption   = f"📊 apostas.csv\n{total} apostas | {pendentes} pendentes"
    with open(CSV_FILE, "rb") as f:
        await update.message.reply_document(
            document=InputFile(f, filename="apostas.csv"),
            caption=caption,
            parse_mode="Markdown"
        )

# ── HELPERS ───────────────────────────────────────────────────────────────────
async def voltar_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("O que mais?", reply_markup=teclado_menu())
    return ConversationHandler.END

async def cancelar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await update.message.reply_text("❌ Cancelado.", reply_markup=teclado_menu())
    return ConversationHandler.END


# ── SERVIDOR HTTP (serve o CSV via URL) ──────────────────────────────────────
class CSVHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/apostas.csv":
            if os.path.exists(CSV_FILE):
                with open(CSV_FILE, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/csv; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_response(404)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # silencia logs do servidor

def iniciar_servidor():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), CSVHandler)
    server.serve_forever()

# ── SERVIDOR DE KEEP-ALIVE (Render exige porta aberta) ───────────────────────
class PingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, *args):
        pass  # silencia logs do servidor

def iniciar_servidor():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), PingHandler)
    server.serve_forever()

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
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
        fallbacks=[
            CommandHandler("cancelar", cancelar),
            MessageHandler(filters.Regex(f"^{CANCELAR_BTN}$"), cancelar),
        ],
    )

    conv_atualizar = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^✅ Atualizar resultado$"), atualizar_inicio)],
        states={
            ATUALIZAR_ID:  [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_id)],
            ATUALIZAR_RES: [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_resultado)],
        },
        fallbacks=[
            CommandHandler("cancelar", cancelar),
            MessageHandler(filters.Regex(f"^{CANCELAR_BTN}$"), cancelar),
        ],
    )

    conv_editar = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^✏️ Editar aposta$"), editar_inicio)],
        states={
            EDITAR_ID:    [MessageHandler(filters.TEXT & ~filters.COMMAND, editar_receber_id)],
            EDITAR_CAMPO: [MessageHandler(filters.TEXT & ~filters.COMMAND, editar_receber_campo)],
            EDITAR_VALOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, editar_receber_valor)],
            EDITAR_CASA:  [MessageHandler(filters.TEXT & ~filters.COMMAND, editar_receber_casa)],
        },
        fallbacks=[
            CommandHandler("cancelar", cancelar),
            MessageHandler(filters.Regex(f"^{CANCELAR_BTN}$"), cancelar),
        ],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("exportar", exportar_csv))
    app.add_handler(conv_nova)
    app.add_handler(conv_atualizar)
    app.add_handler(conv_editar)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_botao))

    t = threading.Thread(target=iniciar_servidor, daemon=True)
    t.start()
    t = threading.Thread(target=iniciar_servidor, daemon=True)
    t.start()
    print("🤖 Bot rodando! Abra o Telegram e mande /start")
    app.run_polling()

if __name__ == "__main__":
    main()
