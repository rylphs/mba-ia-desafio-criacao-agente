"""Tools de reservas de áreas comuns."""
from google.adk.tools import ToolContext

from aurora import db
from aurora.tools._comum import apartamento_do_contexto, erro, resolver_area, validar_data

_ERRO_DATA = "Data inválida. Use o formato AAAA-MM-DD, por exemplo 2030-04-20."
_ERRO_AREA = "Área não encontrada. Use listar_areas para ver as áreas disponíveis."


def listar_areas() -> dict:
    """Lista as áreas comuns do condomínio com a taxa de reserva de cada uma."""
    with db.conexao() as conn:
        areas = db.listar_areas(conn)
    return {
        "areas": [
            {"id": a["id"], "nome": a["nome"], "taxa": a["taxa"], "gera_cobranca": a["taxa"] > 0}
            for a in areas
        ]
    }


def verificar_disponibilidade(area: str, data: str) -> dict:
    """Verifica se uma área comum está livre em uma data.

    Args:
        area: id ou nome da área (por exemplo "salao-de-festas" ou "Salão de Festas").
        data: data no formato AAAA-MM-DD.
    """
    data_ok = validar_data(data)
    if data_ok is None:
        return erro(_ERRO_DATA)
    with db.conexao() as conn:
        a = resolver_area(conn, area)
        if a is None:
            return erro(_ERRO_AREA)
        ocupada = db.data_ocupada(conn, a["id"], data_ok)
    return {"area": a["id"], "data": data_ok, "disponivel": not ocupada}


def reservar_area(area: str, data: str, tool_context: ToolContext) -> dict:
    """Reserva uma área comum para o apartamento do morador na data informada.

    Áreas com taxa exigem confirmação do morador antes de gravar.

    Args:
        area: id ou nome da área.
        data: data no formato AAAA-MM-DD.
    """
    apartamento = apartamento_do_contexto(tool_context)
    data_ok = validar_data(data)
    if data_ok is None:
        return erro(_ERRO_DATA)
    with db.conexao() as conn:
        a = resolver_area(conn, area)
        if a is None:
            return erro(_ERRO_AREA)
        if a["taxa"] > 0:
            confirmacao = tool_context.tool_confirmation
            if confirmacao is None:
                if db.data_ocupada(conn, a["id"], data_ok):
                    return {"status": "indisponivel", "area": a["id"], "data": data_ok}
                tool_context.request_confirmation(
                    hint=f"Reservar {a['nome']} em {data_ok} gera cobrança de R$ {a['taxa']:.2f}. Confirma?",
                    payload={"area": a["id"], "data": data_ok, "taxa": a["taxa"]},
                )
                return {
                    "status": "pendente_confirmacao",
                    "mensagem": "Aguardando o morador confirmar no aplicativo.",
                }
            if confirmacao.confirmed is False:
                return {"status": "negado", "mensagem": "Reserva não realizada: o morador não confirmou."}
        return db.inserir_reserva(conn, apartamento, a["id"], data_ok, tool_context.function_call_id)


def listar_minhas_reservas(tool_context: ToolContext) -> dict:
    """Lista as reservas ativas do apartamento do morador."""
    apartamento = apartamento_do_contexto(tool_context)
    with db.conexao() as conn:
        reservas = db.reservas_ativas(conn, apartamento)
    return {"apartamento": apartamento, "reservas": reservas}


def cancelar_reserva(
    tool_context: ToolContext, codigo: str = "", area: str = "", data: str = ""
) -> dict:
    """Cancela uma reserva ativa do apartamento do morador.

    Informe o código da reserva, ou a área e a data.

    Args:
        codigo: código da reserva (por exemplo "RSV-1377"); opcional.
        area: id ou nome da área; use junto com data quando não houver código.
        data: data da reserva no formato AAAA-MM-DD.
    """
    apartamento = apartamento_do_contexto(tool_context)
    with db.conexao() as conn:
        if codigo.strip():
            cancelada = db.cancelar_reserva(conn, apartamento, codigo=codigo.strip())
        elif area.strip() and data.strip():
            data_ok = validar_data(data)
            if data_ok is None:
                return erro(_ERRO_DATA)
            a = resolver_area(conn, area)
            if a is None:
                return erro(_ERRO_AREA)
            cancelada = db.cancelar_reserva(conn, apartamento, area_id=a["id"], data=data_ok)
        else:
            return erro("Informe o código da reserva, ou a área e a data.")
    if cancelada is None:
        return {
            "status": "nao_encontrada",
            "mensagem": "Não encontrei reserva ativa do seu apartamento com esses dados.",
        }
    return {"status": "cancelado", "reserva": cancelada}


TOOLS_RESERVAS = [
    listar_areas,
    verificar_disponibilidade,
    reservar_area,
    listar_minhas_reservas,
    cancelar_reserva,
]
