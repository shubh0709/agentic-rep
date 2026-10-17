
# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false, reportGeneralTypeIssues=false
"""SRA-901 — ``delete_user_cascade`` removes ``reference_materials`` rows.

Mirrors the SRA-804 pattern in ``tests/integration/test_phase8_cascade.py``.
The SRA-603 cascade must clear the new table alongside ``study_entries``
(where the FK is declared with ``ondelete="CASCADE"``; SQLite ignores FK
ON DELETE without an explicit PRAGMA, so the cascade loop is the
portable path).
"""

from __future__ import annotations

import pytest
from sqlmodel import select

from sra.db import repository
from sra.db.models import (
    StudyEntry,
    StudyEntryCreate,
    User,
    UserCreate,
)

# ``ReferenceMaterial`` is the SRA-901 model; import lazily inside tests so a
# missing symbol fails that test, not the whole module.

pytestmark = pytest.mark.phase9


def _make_user(session) -> User:
    user = User.model_validate(
        UserCreate(
            email="cascade9@example.com",
            display_name="Cascade9",
            google_sub="cascade9-sub",
        ),
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    assert user.id is not None
    return user


def _make_entry(session, user_id: int) -> StudyEntry:
    entry = StudyEntry.model_validate(
        StudyEntryCreate(topic="Topic", summary="s", raw_transcript="t"),
        update={"user_id": user_id},
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def test_delete_user_cascade_removes_reference_materials(session):
    from sra.db.models import ReferenceMaterial

    user = _make_user(session)
    entry = _make_entry(session, user.id)  # type: ignore[arg-type]

    # Two references on this user's entry.
    repository.add_reference(
        entry_id=entry.id,  # type: ignore[arg-type]
        kind="paste",
        title="first",
        pasted_text="body one",
        session=session,
    )
    repository.add_reference(
        entry_id=entry.id,  # type: ignore[arg-type]
        kind="url",
        title="second",
        url="https://example.com",
        extracted_text="body two",
        session=session,
    )

    # Sanity: rows exist
    before = session.exec(
        select(ReferenceMaterial).where(ReferenceMaterial.entry_id == entry.id)  # type: ignore[arg-type]
    ).all()
    assert len(before) == 2

    repository.delete_user_cascade(user, session)
    session.commit()

    # All reference_materials rows for that user must be gone.
    after = session.exec(select(ReferenceMaterial)).all()
    assert after == []
    # And the entry / user are gone too.
    assert session.get(StudyEntry, entry.id) is None  # type: ignore[arg-type]
    assert session.get(User, user.id) is None  # type: ignore[arg-type]


def test_delete_user_cascade_keeps_other_users_references_intact(session):
    """The cascade must scope by user_id — other users' rows must survive."""
    from sra.db.models import ReferenceMaterial

    owner = _make_user(session)
    other = User.model_validate(
        UserCreate(email="other9@example.com", display_name="Other9", google_sub="other9-sub")
    )
    session.add(other)
    session.commit()
    session.refresh(other)

    owner_entry = _make_entry(session, owner.id)  # type: ignore[arg-type]
    other_entry = _make_entry(session, other.id)  # type: ignore[arg-type]

    repository.add_reference(
        entry_id=owner_entry.id,  # type: ignore[arg-type]
        kind="paste",
        title="owner-ref",
        pasted_text="a",
        session=session,
    )
    repository.add_reference(
        entry_id=other_entry.id,  # type: ignore[arg-type]
        kind="paste",
        title="other-ref",
        pasted_text="b",
        session=session,
    )

    repository.delete_user_cascade(owner, session)
    session.commit()

    survivors = session.exec(select(ReferenceMaterial)).all()
    assert len(survivors) == 1
    assert survivors[0].title == "other-ref"
    assert survivors[0].entry_id == other_entry.id  # type: ignore[attr-defined]
