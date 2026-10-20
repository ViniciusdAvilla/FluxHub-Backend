"""One-time CLI bootstrap. Run only from a trusted server terminal."""
import argparse
import getpass
from sqlalchemy import select
from app.core.security import hash_password
from app.database.models import UserModel
from app.database.session import SessionLocal


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", required=True)
    args = parser.parse_args()
    email = args.email.strip().lower()
    if "@" not in email:
        raise SystemExit("E-mail inválido.")
    password = getpass.getpass("Senha admin (mínimo 12 caracteres): ")
    if len(password) < 12:
        raise SystemExit("Senha curta.")
    confirmation = getpass.getpass("Confirme a senha: ")
    if password != confirmation:
        raise SystemExit("As senhas não coincidem.")
    with SessionLocal() as db:
        user = db.scalar(select(UserModel).where(UserModel.email == email))
        if user:
            raise SystemExit("E-mail já existe. Bootstrap não promove contas existentes.")
        user = UserModel(email=email, password_hash=hash_password(password),
                         is_admin=True, is_active=True)
        db.add(user)
        db.commit()
        print("Administrador criado:", user.id, user.email)


if __name__ == "__main__":
    main()
