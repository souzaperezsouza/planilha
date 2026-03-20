import csv
import os
from datetime import datetime

CSV_FILE = "apostas.csv"
CAMPOS   = ["id", "data", "horario", "descricao", "odd", "stake", "resultado", "casa", "esporte"]

ESPORTES_COMUNS = [
    "⚽ Futebol", "🏀 Basquete", "🎾 Tênis", "🏒 Hóquei",
    "🏈 Futebol Americano", "⚾ Beisebol", "🥊 MMA/Boxe", "🏐 Vôlei", "Outro"
]

CASAS_COMUNS = [
    "Bet365", "Betano", "SportingBet", "Novibet", "Vaidebet",
    "Betfast", "BETesporte", "Betao", "Betnacional", "BetFair",
    "Stake", "Pagol", "Vupi", "MC Games", "EsportivaVip", "Outro"
]

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

def input_data(prompt):
    while True:
        raw = input(prompt).strip()
        for fmt in ("%d/%m/%Y", "%d/%m/%y"):
            try:
                return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
            except ValueError:
                pass
        print("  Data inválida. Use DD/MM/AAAA.")

def input_horario(prompt):
    while True:
        raw = input(prompt).strip()
        if raw == "":
            return ""
        try:
            return datetime.strptime(raw, "%H:%M").strftime("%H:%M")
        except ValueError:
            print("  Horário inválido. Use HH:MM ou deixe em branco.")

def input_float(prompt):
    while True:
        try:
            return float(input(prompt).strip().replace(",", "."))
        except ValueError:
            print("  Valor inválido.")

def escolher_esporte():
    print("  Esporte:")
    for i, e in enumerate(ESPORTES_COMUNS, 1):
        print(f"    {i:>2}. {e}")
    escolha = input("  Número ou digite direto: ").strip()
    try:
        esp = ESPORTES_COMUNS[int(escolha) - 1]
        if esp == "Outro":
            esp = input("  Nome do esporte: ").strip()
    except (ValueError, IndexError):
        esp = escolha
    return esp

def escolher_casa():
    print("  Casa de aposta:")
    for i, c in enumerate(CASAS_COMUNS, 1):
        print(f"    {i:>2}. {c}")
    escolha = input("  Número ou digite o nome direto: ").strip()
    try:
        casa = CASAS_COMUNS[int(escolha) - 1]
        if casa == "Outro":
            casa = input("  Nome da casa: ").strip()
    except (ValueError, IndexError):
        casa = escolha
    return casa

def cadastrar(apostas):
    print("\n── NOVA APOSTA ──────────────────────")
    data      = input_data("  Data do jogo (DD/MM/AAAA): ")
    horario   = input_horario("  Horário (HH:MM, opcional): ")
    descricao = input("  Descrição: ").strip()
    odd       = input_float("  Odd: ")
    stake     = input_float("  Stake (R$): ")
    casa      = escolher_casa()
    esporte   = escolher_esporte()
    aposta = {
        "id":        proximo_id(apostas),
        "data":      data,
        "horario":   horario,
        "descricao": descricao,
        "odd":       odd,
        "stake":     stake,
        "resultado": "pendente",
        "casa":      casa,
        "esporte":   esporte,
    }
    apostas.append(aposta)
    salvar(apostas)
    print(f"  ✅ Aposta #{aposta['id']} salva!")

def listar_pendentes(apostas):
    pendentes = [a for a in apostas if a["resultado"] == "pendente"]
    if not pendentes:
        print("\n  Nenhuma aposta pendente.")
        return
    pendentes.sort(key=lambda a: (a["data"], a.get("horario", "")))
    print(f"\n── PENDENTES ({len(pendentes)}) ──────────────────────")
    print(f"  {'#':<4} {'Data':<12} {'Hora':<6} {'Odd':<6} {'Stake':>8} {'Casa':<14}  Descrição")
    print("  " + "─" * 75)
    for a in pendentes:
        data_fmt = datetime.strptime(a["data"], "%Y-%m-%d").strftime("%d/%m/%Y")
        hora     = a.get("horario", "")[:5]
        casa     = a.get("casa", "")[:13]
        print(f"  {a['id']:<4} {data_fmt:<12} {hora:<6} {float(a['odd']):<6.2f} R${float(a['stake']):>7.2f} {casa:<14}  {a['descricao']}")

def atualizar_resultado(apostas):
    listar_pendentes(apostas)
    pendentes = [a for a in apostas if a["resultado"] == "pendente"]
    if not pendentes:
        return
    print()
    try:
        id_alvo = int(input("  ID da aposta a atualizar (0 para cancelar): ").strip())
    except ValueError:
        return
    if id_alvo == 0:
        return
    aposta = next((a for a in apostas if int(a["id"]) == id_alvo), None)
    if not aposta or aposta["resultado"] != "pendente":
        print("  ID não encontrado ou aposta já resolvida.")
        return
    while True:
        res = input("  Resultado [g = ganhou / p = perdeu / v = void]: ").strip().lower()
        if res in ("g", "p", "v"):
            break
        print("  Digite g, p ou v.")
    aposta["resultado"] = {"g": "ganhou", "p": "perdeu", "v": "void"}[res]
    salvar(apostas)
    print(f"  ✅ Aposta #{id_alvo} atualizada para '{aposta['resultado']}'!")

def listar_todas(apostas):
    if not apostas:
        print("\n  Nenhuma aposta cadastrada.")
        return
    ordenadas = sorted(apostas, key=lambda a: (a["data"], a.get("horario", "")))
    print(f"\n── TODAS AS APOSTAS ({len(apostas)}) ────────────────────")
    print(f"  {'#':<4} {'Data':<12} {'Hora':<6} {'Odd':<6} {'Stake':>8} {'Casa':<14} {'Resultado':<12}  Descrição")
    print("  " + "─" * 85)
    for a in ordenadas:
        data_fmt = datetime.strptime(a["data"], "%Y-%m-%d").strftime("%d/%m/%Y")
        hora     = a.get("horario", "")[:5]
        res      = a["resultado"].upper()
        casa     = a.get("casa", "")[:13]
        print(f"  {a['id']:<4} {data_fmt:<12} {hora:<6} {float(a['odd']):<6.2f} R${float(a['stake']):>7.2f} {casa:<14} {res:<12}  {a['descricao']}")

def excluir(apostas):
    listar_todas(apostas)
    if not apostas:
        return
    print()
    try:
        id_alvo = int(input("  ID da aposta a excluir (0 para cancelar): ").strip())
    except ValueError:
        return
    if id_alvo == 0:
        return
    antes = len(apostas)
    apostas[:] = [a for a in apostas if int(a["id"]) != id_alvo]
    if len(apostas) < antes:
        salvar(apostas)
        print(f"  ✅ Aposta #{id_alvo} excluída.")
    else:
        print("  ID não encontrado.")

def menu():
    apostas = carregar()
    while True:
        pendentes = sum(1 for a in apostas if a["resultado"] == "pendente")
        print(f"\n╔══ GESTOR DE APOSTAS {'(' + str(pendentes) + ' pendentes)' if pendentes else ''}{'═' * (28 - len(str(pendentes)) if pendentes else 38)}╗")
        print("║  1. Nova aposta                           ║")
        print("║  2. Atualizar resultado                   ║")
        print("║  3. Ver pendentes                         ║")
        print("║  4. Ver todas                             ║")
        print("║  5. Excluir aposta                        ║")
        print("║  6. Gerar dashboard (Excel)               ║")
        print("║  0. Sair                                  ║")
        print("╚═══════════════════════════════════════════╝")
        opcao = input("  Opção: ").strip()
        if opcao == "1":
            cadastrar(apostas)
        elif opcao == "2":
            atualizar_resultado(apostas)
        elif opcao == "3":
            listar_pendentes(apostas)
        elif opcao == "4":
            listar_todas(apostas)
        elif opcao == "5":
            excluir(apostas)
        elif opcao == "6":
            os.system("python gerar_dashboard.py")
        elif opcao == "0":
            print("\n  Até mais!\n")
            break
        else:
            print("  Opção inválida.")

if __name__ == "__main__":
    menu()
