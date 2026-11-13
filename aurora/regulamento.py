"""Regulamento interno do condomínio dividido por capítulos."""
import re
from dataclasses import dataclass
from functools import cache

from aurora.config import dados_dir

_CABECALHO = re.compile(r"^## Capítulo [IVXLC]+: (.+)$")


@dataclass(frozen=True)
class Capitulo:
    numero: int
    titulo: str
    texto: str


@cache
def capitulos() -> list[Capitulo]:
    linhas = (dados_dir() / "regulamento.md").read_text(encoding="utf-8").splitlines()
    cabecalhos = [
        (i, m.group(1).strip()) for i, linha in enumerate(linhas) if (m := _CABECALHO.match(linha))
    ]
    resultado = []
    for n, (inicio, titulo) in enumerate(cabecalhos, start=1):
        fim = cabecalhos[n][0] if n < len(cabecalhos) else len(linhas)
        texto = "\n".join(linhas[inicio + 1 : fim]).strip()
        resultado.append(Capitulo(numero=n, titulo=titulo, texto=texto))
    return resultado


def sumario() -> str:
    return "\n".join(f"{c.numero}. {c.titulo}" for c in capitulos())
