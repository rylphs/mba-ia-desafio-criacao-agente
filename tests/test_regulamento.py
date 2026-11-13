from aurora.regulamento import capitulos, sumario
from aurora.tools import regulamento as tool_regulamento
from aurora.tools.regulamento import ler_capitulo


def test_quatorze_capitulos():
    cs = capitulos()
    assert len(cs) == 14 and cs[3].titulo == "Piscina" and cs[13].titulo == "Disposições finais"


def test_capitulo_piscina_tem_horario_de_domingo_e_nada_de_outros():
    t = ler_capitulo(4)["texto"]
    assert "Aos domingos e feriados, a piscina funciona das 9h às 20h." in t
    assert "Capítulo V" not in t and "Academia" not in t


def test_capitulo_inexistente():
    assert ler_capitulo(15)["status"] == "erro"


def test_faixa_de_capitulos_vem_do_regulamento(monkeypatch):
    assert f"de 1 a {len(capitulos())}," in ler_capitulo.__doc__
    monkeypatch.setattr(tool_regulamento, "capitulos", lambda: capitulos()[:3])
    assert "de 1 a 3." in ler_capitulo(4)["mensagem"]


def test_sumario_so_titulos():
    s = sumario()
    assert s.splitlines()[3] == "4. Piscina" and "Art." not in s
