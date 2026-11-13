"""Tool de leitura de capítulos do regulamento interno."""
from aurora.regulamento import capitulos
from aurora.tools._comum import erro


def ler_capitulo(numero: int) -> dict:
    """Lê o texto completo de um capítulo do regulamento interno do condomínio.

    Args:
        numero: número do capítulo, de 1 a {total}, conforme o sumário.
    """
    cs = capitulos()
    for c in cs:
        if c.numero == numero:
            return {"numero": c.numero, "titulo": c.titulo, "texto": c.texto}
    return erro(f"Capítulo inexistente. Use um número de 1 a {len(cs)}.")


ler_capitulo.__doc__ = ler_capitulo.__doc__.format(total=len(capitulos()))
