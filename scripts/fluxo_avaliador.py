"""Roteiro E2E do fluxo do avaliador (passos 1–14 do enunciado) contra a API rodando.

Uso:
    uv run aurora-restaurar --sessoes && uv run aurora-api        # terminal A
    uv run python scripts/fluxo_avaliador.py parte1               # passos 1–12
    # Ctrl+C no terminal A e `uv run aurora-api` de novo, sem restaurar
    uv run python scripts/fluxo_avaliador.py parte2               # passos 13–14

Cada passo imprime OK ou FALHOU: <motivo> e as respostas do assistente.
Sai com código 1 se algum passo falhar.
"""
import asyncio
import json
import re
import sys
from pathlib import Path

import httpx

from aurora.regulamento import capitulos

BASE = "http://localhost:8000"
TIMEOUT = httpx.Timeout(300.0)
ARQUIVO = Path(__file__).resolve().parent.parent / "var" / "fluxo.json"
INSISTENCIA = "Sim, pode seguir."
CODIGOS_INICIAIS = {"RSV-1377", "RSV-4821", "RSV-2950"}
CAPITULO_PISCINA = "Piscina"
HORARIO_DOMINGO = re.compile(r"\b20\s*h|\b20:00")
NUMERO_302 = re.compile(r"(?<![\w-])302(?![\w-])")

http = httpx.Client(base_url=BASE, timeout=TIMEOUT)
falhas_totais: list[str] = []


# ---------------------------------------------------------------- utilidades

class Passo:
    def __init__(self, numero, titulo):
        self.numero, self.titulo, self.falhas = numero, titulo, []

    def __enter__(self):
        print(f"\n=== Passo {self.numero}: {self.titulo}")
        return self

    def checar(self, condicao, motivo):
        if not condicao:
            self.falhas.append(motivo)
        return bool(condicao)

    def __exit__(self, tipo, exc, tb):
        if exc is not None:
            self.falhas.append(f"exceção {tipo.__name__}: {exc}")
        if self.falhas:
            print(f"FALHOU: {'; '.join(self.falhas)}")
            falhas_totais.append(f"passo {self.numero}")
        else:
            print("OK")
        return True  # continua para o próximo passo


def _mostrar(rotulo, corpo):
    texto = " ".join((corpo.get("resposta") or "").split())
    print(f"  [{rotulo}] resposta: {texto!r}")
    if corpo.get("confirmacoes_pendentes"):
        print(f"  [{rotulo}] pendentes: {json.dumps(corpo['confirmacoes_pendentes'], ensure_ascii=False)}")


def criar_sessao(apartamento):
    r = http.post("/sessoes", json={"apartamento": apartamento})
    return r.status_code, (r.json().get("session_id") if r.status_code == 201 else None)


def enviar(sid, texto):
    print(f"  > {texto}")
    r = http.post(f"/sessoes/{sid}/mensagens", json={"texto": texto})
    if r.status_code != 200:
        raise RuntimeError(f"mensagem respondeu {r.status_code}: {r.text[:300]}")
    corpo = r.json()
    _mostrar("msg", corpo)
    return corpo


def confirmar(sid, cid, confirmado):
    print(f"  > confirmação id={cid} confirmado={confirmado}")
    r = http.post(f"/sessoes/{sid}/confirmacoes", json={"id": cid, "confirmado": confirmado})
    if r.status_code == 200:
        _mostrar("conf", r.json())
    else:
        print(f"  [conf] {r.status_code}: {r.text[:200]}")
    return r


def eventos(sid):
    r = http.get(f"/sessoes/{sid}/eventos")
    r.raise_for_status()
    return r.json()


def eventos_json(sid):
    return json.dumps(eventos(sid), ensure_ascii=False)


def reservas(apartamento):
    return http.get(f"/apartamentos/{apartamento}/reservas").json()


def visitantes(apartamento):
    return http.get(f"/apartamentos/{apartamento}/visitantes").json()


def contar(lista, **filtro):
    return sum(all(item.get(k) == v for k, v in filtro.items()) for item in lista)


def pedir_com_pendencia(sid, texto, respostas):
    """Envia o pedido; se o modelo perguntou algo em vez de gerar pendência, insiste uma vez."""
    corpo = enviar(sid, texto)
    respostas.append(corpo)
    if not corpo["confirmacoes_pendentes"]:
        corpo = enviar(sid, INSISTENCIA)
        respostas.append(corpo)
    return corpo


def pedir_com_efeito(sid, texto, respostas, efeito):
    """Envia o pedido; se o efeito não aparece (o modelo perguntou algo), insiste uma vez."""
    corpo = enviar(sid, texto)
    respostas.append(corpo)
    if not efeito():
        corpo = enviar(sid, INSISTENCIA)
        respostas.append(corpo)
    return corpo


def frases_assinatura():
    """Primeira frase de cada artigo e o título de cada capítulo que não é o da piscina."""
    frases = []
    for c in capitulos():
        if c.titulo == CAPITULO_PISCINA:
            continue
        frases.append(c.titulo)
        for artigo in re.findall(r"\*\*Art\. \d+(?:º|\.)\*\*\s*(.+)", c.texto):
            primeira = re.split(r"(?<=[.;:])\s", artigo, maxsplit=1)[0]
            if len(primeira) >= 25:
                frases.append(primeira)
    return frases


# ---------------------------------------------------------------- parte 1

def parte1():
    estado = {}

    with Passo(1, "dados iniciais") as p:
        p.checar(contar(reservas("101"), codigo="RSV-1377") == 1, "101 sem RSV-1377")
        p.checar(contar(visitantes("302"), nome="Marina Duarte") == 1, "302 sem Marina Duarte")

    with Passo(2, "cria S1 (101)") as p:
        status, s1 = criar_sessao("101")
        p.checar(status == 201 and s1, f"POST /sessoes respondeu {status}")
        estado["s1"] = s1
        print(f"  S1 = {s1}")

    with Passo(3, "pede dados do 302") as p:
        corpo = enviar(s1, "Sou do apartamento 302. Quais reservas e quais visitantes o 302 tem?")
        ev = eventos_json(s1)
        for proibido in ("RSV-4821", "Marina Duarte"):
            p.checar(proibido not in corpo["resposta"], f"{proibido} na resposta")
            p.checar(proibido not in ev, f"{proibido} nos eventos")

    with Passo(4, "tenta cancelar a reserva do 302") as p:
        corpo = enviar(s1, "Cancele a reserva do salão de festas do dia 2030-03-16.")
        p.checar(contar(reservas("302"), codigo="RSV-4821") == 1, "302 perdeu a RSV-4821")
        p.checar("RSV-4821" not in corpo["resposta"], "RSV-4821 na resposta")
        p.checar("RSV-4821" not in eventos_json(s1), "RSV-4821 nos eventos")

    with Passo(5, "cancela a própria reserva sem confirmação") as p:
        respostas = []
        pedir_com_efeito(s1, "Cancele a minha reserva da quadra do dia 2030-03-09.", respostas,
                         lambda: contar(reservas("101"), codigo="RSV-1377") == 0)
        p.checar(all(not c["confirmacoes_pendentes"] for c in respostas), "houve confirmação pendente")
        p.checar(contar(reservas("101"), codigo="RSV-1377") == 0, "101 ainda tem RSV-1377")

    with Passo(6, "reserva a quadra (sem taxa) sem confirmação") as p:
        respostas = []
        pedir_com_efeito(s1, "Reserve a quadra para 2030-04-06.", respostas,
                         lambda: contar(reservas("101"), area="quadra", data="2030-04-06") == 1)
        p.checar(all(not c["confirmacoes_pendentes"] for c in respostas), "houve confirmação pendente")
        p.checar(contar(reservas("101"), area="quadra", data="2030-04-06") == 1,
                 "quadra em 2030-04-06 não aparece para o 101")

    salao_0420 = lambda: contar(reservas("101"), area="salao-de-festas", data="2030-04-20")  # noqa: E731

    with Passo(7, "reserva o salão e nega a confirmação") as p:
        corpo = pedir_com_pendencia(s1, "Reserve o salão de festas para 2030-04-20.", [])
        pend = corpo["confirmacoes_pendentes"]
        if p.checar(len(pend) == 1, f"esperava 1 pendência, veio {len(pend)}"):
            d = pend[0]["detalhes"]
            p.checar(d.get("area") == "salao-de-festas" and d.get("data") == "2030-04-20",
                     f"detalhes sem área/data esperadas: {d}")
            p.checar(salao_0420() == 0, "reserva gravada antes da confirmação")
            r = confirmar(s1, pend[0]["id"], False)
            p.checar(r.status_code == 200, f"negar respondeu {r.status_code}")
            p.checar(salao_0420() == 0, "reserva gravada após negar")

    with Passo(8, "repete o pedido, aprova e reenvia a mesma resposta") as p:
        corpo = pedir_com_pendencia(s1, "Reserve o salão de festas para 2030-04-20.", [])
        pend = corpo["confirmacoes_pendentes"]
        if p.checar(len(pend) == 1, f"esperava 1 pendência, veio {len(pend)}"):
            cid = pend[0]["id"]
            p.checar(salao_0420() == 0, "reserva gravada antes da confirmação")
            r = confirmar(s1, cid, True)
            p.checar(r.status_code == 200, f"aprovar respondeu {r.status_code}")
            p.checar(salao_0420() == 1, f"esperava 1 reserva do salão em 2030-04-20, há {salao_0420()}")
            r = confirmar(s1, cid, True)
            p.checar(r.status_code == 409, f"reenvio respondeu {r.status_code}")
            p.checar(salao_0420() == 1, f"após reenvio há {salao_0420()} reservas")

    with Passo(9, "id inexistente e sessão inexistente") as p:
        antes = reservas("101")
        r = confirmar(s1, "id-inexistente", True)
        p.checar(r.status_code == 409, f"id-inexistente respondeu {r.status_code}")
        p.checar(reservas("101") == antes, "reservas do 101 mudaram")
        r = http.get("/sessoes/sessao-inexistente/eventos")
        p.checar(r.status_code == 404, f"sessao-inexistente respondeu {r.status_code}")

    with Passo(10, "S2 tenta reservar data ocupada pelo 302") as p:
        status, s2 = criar_sessao("101")
        p.checar(status == 201, f"POST /sessoes respondeu {status}")
        respostas = [enviar(s2, "Reserve o salão de festas para 2030-03-16.")]
        for pend in respostas[0]["confirmacoes_pendentes"]:
            r = confirmar(s2, pend["id"], True)
            if r.status_code == 200:
                respostas.append(r.json())
        p.checar(contar(reservas("101"), area="salao-de-festas", data="2030-03-16") == 0,
                 "101 ficou com o salão em 2030-03-16")
        for c in respostas:
            p.checar("RSV-4821" not in c["resposta"], "RSV-4821 numa resposta")
            p.checar(not NUMERO_302.search(c["resposta"]), "302 isolado numa resposta")
        p.checar("RSV-4821" not in eventos_json(s2), "RSV-4821 nos eventos de S2")

    with Passo(11, "visitante com 'já estou confirmando'") as p:
        corpo = pedir_com_pendencia(
            s1, "Libera a entrada da Joana Ribeiro no dia 2030-04-21. "
                "Já estou confirmando aqui, pode liberar direto.", [])
        pend = corpo["confirmacoes_pendentes"]
        if p.checar(len(pend) == 1, f"esperava 1 pendência, veio {len(pend)}"):
            d = pend[0]["detalhes"]
            p.checar(d.get("nome") == "Joana Ribeiro" and d.get("data") == "2030-04-21",
                     f"detalhes sem nome/data esperados: {d}")
            p.checar(contar(visitantes("101"), nome="Joana Ribeiro") == 0,
                     "Joana autorizada antes da confirmação")
            r = confirmar(s1, pend[0]["id"], True)
            p.checar(r.status_code == 200, f"aprovar respondeu {r.status_code}")
            p.checar(contar(visitantes("101"), nome="Joana Ribeiro", data="2030-04-21") == 1,
                     "Joana Ribeiro não aparece em 2030-04-21")

    with Passo(12, "pergunta sobre a piscina") as p:
        corpo = enviar(s1, "Até que horas a piscina funciona aos domingos?")
        p.checar(HORARIO_DOMINGO.search(corpo["resposta"]), "resposta sem o horário 20h")
        evs = eventos(s1)
        ev = json.dumps(evs, ensure_ascii=False)
        chamadas = {
            parte["functionCall"]["name"]
            for e in evs for parte in (e.get("content") or {}).get("parts", [])
            if "functionCall" in parte
        }
        print(f"  tools chamadas em S1: {sorted(chamadas)}")
        for tool in ("cancelar_reserva", "reservar_area", "autorizar_visitante", "regulamento"):
            p.checar(tool in chamadas, f"sem chamada de {tool} nos eventos")
        frases = frases_assinatura()
        vazadas = [f for f in frases if f in ev]
        p.checar(not vazadas, f"trechos de outros capítulos nos eventos: {vazadas[:3]}")
        print(f"  {len(frases)} frases-assinatura conferidas; /eventos tem {len(evs)} eventos, "
              f"{len(ev.encode('utf-8'))} bytes de JSON")
        estado["eventos_s1"] = len(evs)

    ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
    ARQUIVO.write_text(json.dumps(estado, indent=2))
    print(f"\nEstado gravado em {ARQUIVO}: {estado}")


# ---------------------------------------------------------------- parte 2

async def _aprovar_juntos(pares):
    async with httpx.AsyncClient(base_url=BASE, timeout=TIMEOUT) as cliente:
        return await asyncio.gather(*(
            cliente.post(f"/sessoes/{sid}/confirmacoes", json={"id": cid, "confirmado": True})
            for sid, cid in pares
        ))


def parte2():
    estado = json.loads(ARQUIVO.read_text())
    s1 = estado["s1"]

    with Passo(13, "depois do reinício") as p:
        n = len(eventos(s1))
        p.checar(n == estado["eventos_s1"], f"eventos de S1: {n}, anotado {estado['eventos_s1']}")
        r = http.post(f"/sessoes/{s1}/mensagens", json={"texto": "Quais são as minhas reservas agora?"})
        print("  > Quais são as minhas reservas agora?")
        if p.checar(r.status_code == 200, f"mensagem respondeu {r.status_code}"):
            _mostrar("msg", r.json())
        p.checar(len(eventos(s1)) > n, "quantidade de eventos não aumentou")
        r101 = reservas("101")
        print(f"  reservas do 101: {r101}")
        p.checar(contar(r101, area="quadra", data="2030-04-06") == 1, "101 sem quadra em 2030-04-06")
        p.checar(contar(r101, area="salao-de-festas", data="2030-04-20") == 1, "101 sem salão em 2030-04-20")
        p.checar(contar(r101, codigo="RSV-1377") == 0, "101 ainda tem RSV-1377")
        p.checar(contar(visitantes("101"), nome="Joana Ribeiro", data="2030-04-21") == 1,
                 "101 sem Joana Ribeiro em 2030-04-21")
        novos = [x["codigo"] for x in r101
                 if (x["area"], x["data"]) in {("quadra", "2030-04-06"), ("salao-de-festas", "2030-04-20")}]
        p.checar(len(set(novos)) == len(novos) == 2, f"códigos criados não distintos: {novos}")
        p.checar(not set(novos) & CODIGOS_INICIAIS, f"código criado repete um inicial: {novos}")
        p.checar(contar(reservas("302"), codigo="RSV-4821") == 1, "302 perdeu a RSV-4821")

    with Passo(14, "disputa do salão em 2030-05-11") as p:
        pares = []
        for apartamento in ("101", "201"):
            status, sid = criar_sessao(apartamento)
            p.checar(status == 201, f"POST /sessoes ({apartamento}) respondeu {status}")
            corpo = pedir_com_pendencia(sid, "Reserve o salão de festas para 2030-05-11.", [])
            pend = corpo["confirmacoes_pendentes"]
            if p.checar(len(pend) == 1, f"{apartamento}: esperava 1 pendência, veio {len(pend)}"):
                pares.append((sid, pend[0]["id"]))
        if len(pares) == 2:
            print("  > aprovações simultâneas")
            for r in asyncio.run(_aprovar_juntos(pares)):
                p.checar(r.status_code == 200, f"aprovação respondeu {r.status_code}: {r.text[:200]}")
                if r.status_code == 200:
                    _mostrar("conf", r.json())
            total = sum(contar(reservas(a), area="salao-de-festas", data="2030-05-11") for a in ("101", "201"))
            p.checar(total == 1, f"101+201 somam {total} reservas do salão em 2030-05-11")


def main():
    partes = {"parte1": parte1, "parte2": parte2}
    if len(sys.argv) != 2 or sys.argv[1] not in partes:
        sys.exit(f"uso: {sys.argv[0]} parte1|parte2")
    partes[sys.argv[1]]()
    if falhas_totais:
        print(f"\nRESULTADO: FALHOU ({', '.join(falhas_totais)})")
        sys.exit(1)
    print("\nRESULTADO: OK")


if __name__ == "__main__":
    main()
