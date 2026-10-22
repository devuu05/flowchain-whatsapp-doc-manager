"""Operator CLI: python -m app.cli <command>.

Commands:
  create-admin --email E [--name N]          create a platform admin (password via stdin)
  create-tenant --name N [--owner-email E]   provision a tenant with default categories
  add-sender --tenant-slug S --phone P       authorize a WhatsApp sender number
  gen-key                                    print a new Fernet key for FIELD_ENCRYPTION_KEYS
  rotate-keys                                re-encrypt integration secrets with the first key
  sweep                                      run maintenance once
  bootstrap                                  apply BOOTSTRAP_* env vars (idempotent)
"""

from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy import select

from app.db.session import session_scope
from app.models import IntegrationConfig, Tenant, User
from app.security.crypto import generate_fernet_key, rotate
from app.security.passwords import hash_password, validate_password_strength
from app.services.auth import normalize_email
from app.services.tenancy import add_authorized_sender, create_tenant


def _password() -> str:
    if not sys.stdin.isatty():
        return sys.stdin.readline().rstrip("\n")
    pw = getpass.getpass("Password: ")
    if pw != getpass.getpass("Repeat: "):
        sys.exit("Passwords do not match")
    return pw


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="app.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("create-admin")
    a.add_argument("--email", required=True)
    a.add_argument("--name", default="")
    t = sub.add_parser("create-tenant")
    t.add_argument("--name", required=True)
    t.add_argument("--owner-email")
    s = sub.add_parser("add-sender")
    s.add_argument("--tenant-slug", required=True)
    s.add_argument("--phone", required=True)
    s.add_argument("--name", default="")
    sub.add_parser("gen-key")
    sub.add_parser("rotate-keys")
    sub.add_parser("sweep")
    sub.add_parser("bootstrap")
    args = p.parse_args(argv)

    if args.cmd == "gen-key":
        print(generate_fernet_key())
        return
    with session_scope() as db:
        if args.cmd == "create-admin":
            pw = _password()
            if problem := validate_password_strength(pw):
                sys.exit(problem)
            email = normalize_email(args.email)
            if db.scalar(select(User).where(User.email == email)):
                sys.exit("User already exists")
            db.add(
                User(
                    email=email,
                    full_name=args.name,
                    password_hash=hash_password(pw),
                    is_platform_admin=True,
                )
            )
            print(f"Created platform admin {email}")
        elif args.cmd == "create-tenant":
            owner = None
            if args.owner_email:
                owner = db.scalar(
                    select(User).where(User.email == normalize_email(args.owner_email))
                )
                if owner is None:
                    sys.exit("Owner not found")
            tenant = create_tenant(db, args.name, owner)
            print(f"Created tenant {tenant.name} slug={tenant.slug} id={tenant.id}")
        elif args.cmd == "add-sender":
            found = db.scalar(select(Tenant).where(Tenant.slug == args.tenant_slug))
            if found is None:
                sys.exit("Tenant not found")
            add_authorized_sender(db, found.id, args.phone, args.name)
            print("Sender authorized")
        elif args.cmd == "rotate-keys":
            n = 0
            for cfg in db.scalars(
                select(IntegrationConfig).where(IntegrationConfig.encrypted_secrets.is_not(None))
            ):
                assert cfg.encrypted_secrets is not None
                cfg.encrypted_secrets = rotate(cfg.encrypted_secrets)
                n += 1
            print(f"Re-encrypted {n} integration secret blobs")
        elif args.cmd == "bootstrap":
            from app.services.bootstrap import bootstrap_from_env

            print(bootstrap_from_env(db))
        elif args.cmd == "sweep":
            from app.services.maintenance import sweep

            print(sweep(db))


if __name__ == "__main__":
    main()
