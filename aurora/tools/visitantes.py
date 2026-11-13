"""Tools de autorização de visitantes."""
from google.adk.tools import ToolContext

from aurora import db
from aurora.tools._comum import apartamento_do_contexto, erro, validar_data


def autorizar_visitante(nome: str, data: str, tool_context: ToolContext) -> dict:
    """Autoriza a entrada de um visitante do morador em uma data. Sempre exige confirmação.

    Args:
        nome: nome completo do visitante.
        data: data da visita no formato AAAA-MM-DD.
    """
    apartamento = apartamento_do_contexto(tool_context)
    nome = (nome or "").strip()
    if not nome:
        return erro("Informe o nome do visitante.")
    data_ok = validar_data(data)
    if data_ok is None:
        return erro("Data inválida. Use o formato AAAA-MM-DD, por exemplo 2030-04-21.")
    confirmacao = tool_context.tool_confirmation
    if confirmacao is None:
        tool_context.request_confirmation(
            hint=f"Autorizar a entrada de {nome} em {data_ok}. Confirma?",
            payload={"nome": nome, "data": data_ok},
        )
        return {
            "status": "pendente_confirmacao",
            "mensagem": "Aguardando o morador confirmar no aplicativo.",
        }
    if confirmacao.confirmed is False:
        return {"status": "negado", "mensagem": "Autorização não realizada: o morador não confirmou."}
    with db.conexao() as conn:
        return db.inserir_visitante(conn, apartamento, nome, data_ok, tool_context.function_call_id)


def listar_meus_visitantes(tool_context: ToolContext) -> dict:
    """Lista os visitantes autorizados do apartamento do morador."""
    apartamento = apartamento_do_contexto(tool_context)
    with db.conexao() as conn:
        visitantes = db.visitantes_do_apartamento(conn, apartamento)
    return {"apartamento": apartamento, "visitantes": visitantes}


TOOLS_VISITANTES = [autorizar_visitante, listar_meus_visitantes]
