"""Tool de leitura de capítulos do regulamento interno."""
from aurora.regulamento import capitulos
from aurora.tools._comum import erro


def ler_capitulo(numero: int) -> dict:
    """Lê o texto completo de um capítulo do regulamento interno do condomínio.

    Args:
        numero: número do capítulo, de 1 a 14, conforme o sumário.
    """
    for c in capitulos():
        if c.numero == numero:
            return {"numero": c.numero, "titulo": c.titulo, "texto": c.texto}
    return erro("Capítulo inexistente. Use um número de 1 a 14.")
