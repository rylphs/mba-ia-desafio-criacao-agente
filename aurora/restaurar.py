"""Comando `aurora-restaurar`: devolve o banco ao estado inicial de dados/."""
import argparse

from aurora import config, db


def main() -> None:
    parser = argparse.ArgumentParser(description="Restaura os dados iniciais do condomínio.")
    parser.add_argument(
        "--sessoes", action="store_true", help="apaga também as sessões de conversa"
    )
    args = parser.parse_args()

    if args.sessoes:
        (config.var_dir() / "sessoes.db").unlink(missing_ok=True)
    with db.conexao() as conn:
        db.criar_esquema(conn)
        db.restaurar(conn)
        if args.sessoes:
            conn.execute("DELETE FROM sessoes")
        n_res = conn.execute("SELECT COUNT(*) FROM reservas").fetchone()[0]
        n_vis = conn.execute("SELECT COUNT(*) FROM visitantes").fetchone()[0]
    print(f"Dados restaurados: {n_res} reservas, {n_vis} visitantes.")
    if args.sessoes:
        print("Sessões apagadas.")
