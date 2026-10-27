"""Interactive WhatsApp chat: menu, folder captions, and admin-only rename/delete.

External services are the labelled FAKE providers.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import AuditEvent, AuthorizedSender, ConversationState, Document, DocumentStatus
from app.services.tenancy import add_authorized_sender
from tests import synthetic as syn
from tests.conftest import World, drain, login, media_event, post_events, sent_texts, text_event

ADMIN = "919811112222"
INVOICE_PDF = syn.pdf_with_text(["TAX INVOICE", "ABC Traders", "Invoice date 15/09/2026"])


def _admin(db: Session, world: World) -> str:
    add_authorized_sender(db, world.tenant.id, ADMIN, "Owner", "admin")
    db.commit()
    return ADMIN


def say(client: TestClient, world: World, sender: str, text: str) -> str:
    post_events(client, world, text_event(sender, text))
    drain()
    return sent_texts(world)[-1]


def upload(
    client: TestClient, world: World, sender: str, caption: str, name: str = "inv.pdf"
) -> None:
    post_events(
        client,
        world,
        media_event(sender, INVOICE_PDF, filename=name, mime="application/pdf", caption=caption),
    )
    drain()


def stored_docs(db: Session, world: World) -> list[Document]:
    db.expire_all()
    return list(
        db.scalars(
            select(Document)
            .where(Document.tenant_id == world.tenant.id, Document.status == DocumentStatus.stored)
            .order_by(Document.created_at)
        )
    )


def test_menu_differs_for_members_and_admins(client: TestClient, db: Session, world: World) -> None:
    admin = _admin(db, world)
    member_menu = say(client, world, world.sender, "hi")
    assert "1️⃣ Show my latest documents" in member_menu and "Delete" not in member_menu
    admin_menu = say(client, world, admin, "menu")
    assert "5️⃣ Rename a document" in admin_menu and "6️⃣ Delete a document" in admin_menu
    # Members cannot reach admin entries by number either.
    assert "numbers from the menu" in say(client, world, world.sender, "6")


def test_menu_numbers_drive_actions(client: TestClient, db: Session, world: World) -> None:
    upload(client, world, world.sender, "invoice from ABC Traders")
    say(client, world, world.sender, "menu")
    latest = say(client, world, world.sender, "1")
    assert "1 result(s)" in latest
    # After a result list, a bare number selects from it again (the menu step is closed).
    assert "Sending" in say(client, world, world.sender, "1")

    say(client, world, world.sender, "menu")
    assert "What are you looking for" in say(client, world, world.sender, "2")
    assert "1 result(s)" in say(client, world, world.sender, "ABC Traders")

    say(client, world, world.sender, "menu")
    assert "Folders" in say(client, world, world.sender, "3")
    say(client, world, world.sender, "menu")
    assert "#Invoices/2026" in say(client, world, world.sender, "4")


def test_hash_caption_saves_into_chosen_folder(
    client: TestClient, db: Session, world: World
) -> None:
    upload(client, world, world.sender, "#Clients/Acme/Contracts signed copy")
    [doc] = stored_docs(db, world)
    assert doc.folder == "Clients/Acme/Contracts"
    assert doc.description == "signed copy"
    assert any("Saving it to 📁 Clients/Acme/Contracts" in t for t in sent_texts(world))
    assert any("📁 Clients/Acme/Contracts" in t and "Saved" in t for t in sent_texts(world))
    # The fake storage created the nested folders.
    names = [name for (_parent, name) in world.fake_storage.folders]
    assert names == ["Clients", "Acme", "Contracts"]
    assert "Clients/Acme/Contracts (1)" in say(client, world, world.sender, "folders")


def test_folder_only_caption_needs_no_description(
    client: TestClient, db: Session, world: World
) -> None:
    upload(client, world, world.sender, "#Receipts")
    [doc] = stored_docs(db, world)
    assert doc.folder == "Receipts" and doc.description is None


def test_folder_tag_in_pending_description(client: TestClient, db: Session, world: World) -> None:
    upload(client, world, world.sender, "")
    assert "What is it?" in sent_texts(world)[-1]
    say(client, world, world.sender, "#Bills electricity bill")
    [doc] = stored_docs(db, world)
    assert doc.folder == "Bills" and doc.description == "electricity bill"


def test_members_cannot_rename_or_delete(client: TestClient, db: Session, world: World) -> None:
    upload(client, world, world.sender, "invoice from ABC Traders")
    say(client, world, world.sender, "show my latest documents")
    for cmd in ("rename 1 to Hacked", "delete 1", "rename", "delete"):
        assert "Only WhatsApp admins" in say(client, world, world.sender, cmd), cmd
    [doc] = stored_docs(db, world)
    assert "Hacked" not in (doc.standardized_filename or "")


def test_admin_rename_by_command_keeps_extension(
    client: TestClient, db: Session, world: World
) -> None:
    admin = _admin(db, world)
    upload(client, world, admin, "invoice from ABC Traders")
    say(client, world, admin, "show my latest documents")
    reply = say(client, world, admin, "rename 1 to ABC September bill")
    assert "Renamed" in reply and "*ABC September bill.pdf*" in reply
    [doc] = stored_docs(db, world)
    assert doc.standardized_filename == "ABC September bill.pdf"
    assert world.fake_storage.get_metadata(doc.storage_file_id).name == "ABC September bill.pdf"
    assert db.scalar(select(AuditEvent).where(AuditEvent.action == "document.renamed_whatsapp"))


def test_admin_guided_rename_flow(client: TestClient, db: Session, world: World) -> None:
    admin = _admin(db, world)
    upload(client, world, admin, "invoice from ABC Traders")
    say(client, world, admin, "menu")
    assert "Which document should I rename" in say(client, world, admin, "5")
    assert "New name for" in say(client, world, admin, "1")
    assert "Renamed" in say(client, world, admin, "Final invoice")
    assert stored_docs(db, world)[0].standardized_filename == "Final invoice.pdf"


def test_admin_delete_needs_yes(client: TestClient, db: Session, world: World) -> None:
    admin = _admin(db, world)
    upload(client, world, admin, "invoice from ABC Traders")
    say(client, world, admin, "show my latest documents")
    ask = say(client, world, admin, "delete 1")
    assert "Reply *YES* to delete" in ask
    assert len(stored_docs(db, world)) == 1
    assert "Kept it" in say(client, world, admin, "no")
    assert len(stored_docs(db, world)) == 1

    say(client, world, admin, "show my latest documents")
    say(client, world, admin, "delete 1")
    file_id = stored_docs(db, world)[0].storage_file_id
    assert "Deleted" in say(client, world, admin, "YES")
    assert stored_docs(db, world) == []
    assert file_id not in world.fake_storage.files
    assert db.scalar(select(AuditEvent).where(AuditEvent.action == "document.deleted_whatsapp"))


def test_unrelated_reply_cancels_pending_delete(
    client: TestClient, db: Session, world: World
) -> None:
    admin = _admin(db, world)
    upload(client, world, admin, "invoice from ABC Traders")
    say(client, world, admin, "show my latest documents")
    say(client, world, admin, "delete 1")
    say(client, world, admin, "find all invoices")
    # A later "yes" no longer deletes anything.
    say(client, world, admin, "yes")
    assert len(stored_docs(db, world)) == 1


def test_demoted_admin_job_is_refused(client: TestClient, db: Session, world: World) -> None:
    admin = _admin(db, world)
    upload(client, world, admin, "invoice from ABC Traders")
    say(client, world, admin, "show my latest documents")
    say(client, world, admin, "delete 1")
    post_events(client, world, text_event(admin, "yes"))
    drain(max_jobs=1)  # handle the inbound "yes" only; the delete job is still queued
    db.execute(
        update(AuthorizedSender).where(AuthorizedSender.phone == admin).values(role="member")
    )
    db.commit()
    drain()
    assert len(stored_docs(db, world)) == 1
    assert any("couldn't delete" in t for t in sent_texts(world))


def test_states_are_per_sender(client: TestClient, db: Session, world: World) -> None:
    admin = _admin(db, world)
    say(client, world, admin, "menu")
    say(client, world, world.sender, "menu")
    db.expire_all()
    states = db.scalars(
        select(ConversationState).where(ConversationState.tenant_id == world.tenant.id)
    )
    assert sorted(s.sender for s in states) == sorted([admin, world.sender])


def test_sender_role_api(client: TestClient, db: Session, world: World) -> None:
    csrf = login(client, world.user.email)
    base = f"/api/v1/tenants/{world.tenant.id}/senders"
    r = client.post(
        base,
        json={"phone": "+91 98111 33333", "display_name": "Boss", "role": "admin"},
        headers={"x-csrf-token": csrf},
    )
    assert r.status_code == 201 and r.json()["role"] == "admin"
    r = client.patch(
        f"{base}/{r.json()['id']}", json={"role": "member"}, headers={"x-csrf-token": csrf}
    )
    assert r.status_code == 200 and r.json()["role"] == "member"
    r = client.post(
        base, json={"phone": "+91 98111 44444", "role": "owner"}, headers={"x-csrf-token": csrf}
    )
    assert r.status_code == 422
    roles = {s["phone"]: s["role"] for s in client.get(base).json()}
    assert roles[world.sender] == "member"


def test_parse_folder_caption_variants() -> None:
    from app.services.guided import parse_folder_caption

    assert parse_folder_caption("#Invoices/2026 bill").folder == ["Invoices", "2026"]
    c = parse_folder_caption("#Bank Statements/2026: March HDFC")
    assert c.folder == ["Bank Statements", "2026"] and c.text == "March HDFC"
    assert parse_folder_caption("#Receipts").folder == ["Receipts"]
    assert parse_folder_caption("invoice #42 from ABC").folder is None
    assert parse_folder_caption("#a/b/c/d/e x").folder == ["a", "b", "c"]
    assert parse_folder_caption("#../../etc x").folder == ["etc"]  # sanitized, no traversal


def test_rename_word_boundary(client: TestClient, db: Session, world: World) -> None:
    admin = _admin(db, world)
    upload(client, world, admin, "invoice from ABC Traders")
    say(client, world, admin, "show my latest documents")
    assert "*tomorrow plan.pdf*" in say(client, world, admin, "rename 1 tomorrow plan")
