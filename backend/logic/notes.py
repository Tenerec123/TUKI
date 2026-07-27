from fastapi import HTTPException
from sqlalchemy.orm import Session
from ..models import NoteMeta, get_embedding_model
from ..schemas import NoteMetaCreate, NoteMetaUpdate
from pathlib import Path
from typing import Any
import shutil
ROOT = Path(__file__).parent.parent
VAULT = Path(__file__).parent.parent / "vault"
DRAFT = Path(__file__).parent.parent / "draft"
def _note_path(noteM: NoteMeta) -> Path:
    return ROOT / noteM.path / f"{noteM.title}.md"

def get_note_logic(id:int, db: Session):
    noteM = db.query(NoteMeta).where(NoteMeta.id == id).first()
    if noteM is None: raise HTTPException(status_code=404, detail="Note not found")
    return _note_path(noteM).read_text(encoding='utf-8')


def search_notes_logic(text: str, limit: int, permission:bool, db: Session):
    model = get_embedding_model()
    embedding = list(model.encode(text))
    initial = "vault" if permission else "draft"
    return db.query(NoteMeta).where(NoteMeta.path.startswith(initial)).order_by(NoteMeta.embedding.cosine_distance(embedding)).limit(limit).all()

def create_note_logic(note:NoteMetaCreate, permission:bool, db: Session):
    extra = "vault/" if permission else "draft/"
    noteM = NoteMeta(
        title = note.title,
        path = extra + note.path,
    )
    db.add(noteM)
    db.commit()
    db.refresh(noteM)
    path = _note_path(noteM)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(note.content, encoding='utf-8')

    # Changelog: track creation for sync
    changelog_dir = VAULT if permission else DRAFT
    changelog = changelog_dir / 'changelog.txt'
    with changelog.open('a', encoding='utf-8') as f:
        f.write(f'{note.title} {note.path} c\n')

    return noteM

def update_note_logic(id:int, note_update:NoteMetaUpdate, db: Session):
    noteM = db.query(NoteMeta).where(NoteMeta.id == id).first()
    if noteM is None: raise HTTPException(status_code=404, detail="Note not found")

    old_path = _note_path(noteM)

    if note_update.title is not None:
        noteM.title = note_update.title
        new_path = _note_path(noteM)
        old_path.rename(new_path)

    if note_update.content is not None:
        new_path.write_text(note_update.content, encoding="utf-8")

    db.commit()
    return noteM

def delete_note_logic(id:int, db: Session):
    noteM = db.query(NoteMeta).where(NoteMeta.id == id).first()
    if noteM is None: raise HTTPException(status_code=404, detail="Note not found")

    # Changelog: track deletion for sync
    # Derive changelog dir from the note's own path
    changelog_dir = VAULT if noteM.path.startswith("vault") else DRAFT
    changelog = changelog_dir / 'changelog.txt'
    if changelog.exists():
        logs = changelog.read_text(encoding='utf-8').split("\n")
        first_len = len(logs)
        # If note was created in this sync session, just remove the "c" entry
        logs = [log for log in logs if log != f"{noteM.title} {noteM.path} c"]
        if len(logs) == first_len:
            logs.append(f"{noteM.title} {noteM.path} d")
        changelog.write_text("\n".join(logs), encoding='utf-8')

    _note_path(noteM).unlink()
    db.delete(noteM)
    db.commit()
    return noteM


def get_file_tree_logic(permission,db: Session) -> dict[str, Any]:
    """Build a nested file tree from the vault filesystem.

    NoteMeta IDs are resolved via a single DB lookup.
    Empty folders appear because we scan the filesystem directly.

    Returns:
        {
            "files": [{"id": int|None, "name": str}, ...],
            "folders": [{"name": str, "files": [...], "folders": [...]}]
        }
    """
    # Single query → dict lookup: "path/title" → id
    notes = db.query(NoteMeta.id, NoteMeta.path, NoteMeta.title).all()
    id_lookup = {(n.path.rstrip("/") or "", n.title): n.id for n in notes}

    tree: dict[str, Any] = {"files": [], "folders": []}

    TREEBASE = VAULT if permission else DRAFT

    if not TREEBASE.exists():
        return tree

    def _build_tree(folder: Path, node: dict):
        entries = sorted(folder.iterdir(), key=lambda e: e.name.lower())
        for entry in entries:
            if entry.is_dir():
                child = {"name": entry.name, "files": [], "folders": []}
                _build_tree(entry, child)
                node["folders"].append(child)
            elif entry.suffix == ".md":
                rel_path = str(entry.parent.relative_to(ROOT)).replace("\\", "/")
                if rel_path == ".":
                    rel_path = ""
                note_id = id_lookup.get((rel_path, entry.stem))
                node["files"].append({"id": note_id, "name": entry.stem})

    _build_tree(TREEBASE, tree)
    return tree


def delete_folder_logic(path: str, db: Session):
    folder = VAULT / path
    if folder.exists() and folder.is_dir():
        # Delete DB records for all notes inside this folder
        prefix = "vault/" + path.rstrip("/")
        notes = db.query(NoteMeta).where(
            NoteMeta.path.like(f"{prefix}%") | (NoteMeta.path == prefix)
        ).all()
        for note in notes:
            _note_path(note).unlink(missing_ok=True)
            db.delete(note)
        db.commit()
        shutil.rmtree(folder)
    return "Folder deleted"

def create_folder_logic(path: str):
    folder = VAULT / path
    folder.mkdir(parents=True, exist_ok=True)
    return "Folder created"

def decide_ai_changes_logic(accepted:bool, db:Session):
    if accepted:
        src, dst, dst_prefix = DRAFT, VAULT, "vault"
    else:
        src, dst, dst_prefix = VAULT, DRAFT, "draft"

    shutil.copytree(src, dst, dirs_exist_ok=True)

    changelog = dst / 'changelog.txt'
    if changelog.exists():
        logs = changelog.read_text(encoding='utf-8')
        logs = logs.split('\n')[:-1]
        for log in logs:
            splited_log = log.split(" ")
            title = splited_log[0]
            path = splited_log[1]
            action = splited_log[2]

            if action == "c":
                noteM = NoteMeta(
                    title = title,
                    path = f"{dst_prefix}/{path}"
                )
                db.add(noteM)
            else:
                noteM = db.query(NoteMeta).where(NoteMeta.path == f"{dst_prefix}/{path}", NoteMeta.title == title)
                db.delete(noteM)
            db.commit()
        changelog.unlink()

    # Clean up changelog from source
    src_changelog = src / 'changelog.txt'
    if src_changelog.exists():
        src_changelog.unlink()