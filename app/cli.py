"""Admin commands. Usage:

    docker compose exec api python -m app.cli create-admin --email you@example.com --name "Your Name"
"""

import argparse
import asyncio
import getpass
import sys

from sqlalchemy import func, select

from app.auth import hash_password
from app.db import SessionLocal
from app.models import User


async def create_admin(email: str, name: str, password: str) -> None:
    async with SessionLocal() as session:
        if await session.scalar(select(User.id).where(func.lower(User.email) == email.lower())):
            sys.exit(f"A user with email {email} already exists")
        session.add(User(email=email.lower(), name=name, password_hash=hash_password(password), is_admin=True))
        await session.commit()
    print(f"Admin {email} created")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("create-admin", help="create an admin user for the web app")
    p.add_argument("--email", required=True)
    p.add_argument("--name", required=True)
    args = parser.parse_args()

    password = getpass.getpass("Password (min 8 characters): ")
    if not 8 <= len(password.encode()) <= 72:
        sys.exit("Password must be 8-72 characters")
    if getpass.getpass("Repeat password: ") != password:
        sys.exit("Passwords don't match")
    asyncio.run(create_admin(args.email, args.name, password))


if __name__ == "__main__":
    main()
