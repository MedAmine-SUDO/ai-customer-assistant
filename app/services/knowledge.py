"""Turn the business's documents into searchable chunks, and find the right ones for a question."""

import hashlib
import io
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.llm import Usage, get_llm
from app.models import Chunk, KnowledgeDocument
from app.tenants import TENANTS

log = logging.getLogger(__name__)
MAX_CHUNK_CHARS = 1400


@dataclass
class Section:
    heading: str
    content: str


@dataclass
class Hit:
    id: int
    source: str
    heading: str
    content: str
    distance: float


def pdf_to_text(data: bytes) -> str:
    from pypdf import PdfReader

    return "\n\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)


def split_sections(text: str, title: str) -> list[Section]:
    """One chunk per heading (## or ###), long sections split on paragraphs."""
    sections: list[Section] = []
    heading, buf = title, []
    parents: dict[int, str] = {}

    def flush():
        body = "\n".join(buf).strip()
        if body:
            sections.extend(_split_long(heading, body))

    for line in text.splitlines():
        m = re.match(r"^(#{1,3})\s+(.*)", line)
        if m:
            flush()
            level, name = len(m.group(1)), m.group(2).strip()
            parents[level] = name
            for deeper in [k for k in parents if k > level]:
                del parents[deeper]
            heading = " › ".join(parents[k] for k in sorted(parents) if k > 1) or name
            buf = []
        else:
            buf.append(line)
    flush()
    return sections


def _split_long(heading: str, body: str) -> list[Section]:
    if len(body) <= MAX_CHUNK_CHARS:
        return [Section(heading, body)]
    out, current = [], ""
    for para in re.split(r"\n\s*\n", body):
        if current and len(current) + len(para) > MAX_CHUNK_CHARS:
            out.append(Section(heading, current.strip()))
            current = ""
        current += para + "\n\n"
    if current.strip():
        out.append(Section(heading, current.strip()))
    return out


def ingest_text(db: Session, tenant: str, source: str, text: str, force: bool = False) -> tuple[bool, Usage]:
    """Store a document's chunks with their embeddings. Skips it if nothing changed."""
    content_hash = hashlib.sha256(text.encode()).hexdigest()
    model = f"{get_llm().name}:{settings.embedding_model}"
    doc = db.scalar(
        select(KnowledgeDocument).where(
            KnowledgeDocument.tenant == tenant, KnowledgeDocument.source == source
        )
    )
    if doc and not force and doc.content_hash == content_hash and doc.embedding_model == model:
        return False, Usage()

    first_heading = re.search(r"^#\s+(.*)", text, re.M)
    title = first_heading.group(1).strip() if first_heading else Path(source).stem
    sections = split_sections(text, title)
    if not sections:
        raise ValueError("The document has no text")
    vectors, usage = get_llm().embed([f"{title} — {s.heading}\n{s.content}" for s in sections])

    if doc:
        db.execute(delete(Chunk).where(Chunk.document_id == doc.id))
        doc.title, doc.content_hash, doc.embedding_model = title, content_hash, model
    else:
        doc = KnowledgeDocument(
            tenant=tenant, source=source, title=title, content_hash=content_hash, embedding_model=model
        )
        db.add(doc)
        db.flush()
    db.add_all(
        Chunk(document_id=doc.id, tenant=tenant, heading=s.heading[:300], content=s.content, embedding=v)
        for s, v in zip(sections, vectors, strict=True)
    )
    db.commit()
    log.info("ingested %s/%s: %d chunks", tenant, source, len(sections))
    return True, usage


def ingest_folder(db: Session, tenant: str) -> int:
    """Load knowledge/<tenant>/*.md. Returns how many documents were (re)embedded."""
    changed = 0
    for path in sorted(TENANTS[tenant].knowledge_dir.glob("*.md")):
        updated, _ = ingest_text(db, tenant, path.name, path.read_text(encoding="utf-8"))
        changed += updated
    return changed


def ingest_all(db: Session) -> int:
    return sum(ingest_folder(db, slug) for slug in TENANTS)


_checked: set[str] = set()


def ensure_embedded(db: Session, tenant: str) -> None:
    """Re-embed the bundled documents if they were embedded by another model (say the database was
    seeded with the mock, then the app switched to OpenAI). Checked once per process."""
    if tenant in _checked:
        return
    model = f"{get_llm().name}:{settings.embedding_model}"
    stale = db.scalar(
        select(KnowledgeDocument.id)
        .where(KnowledgeDocument.tenant == tenant, KnowledgeDocument.embedding_model != model)
        .limit(1)
    )
    has_docs = db.scalar(select(KnowledgeDocument.id).where(KnowledgeDocument.tenant == tenant).limit(1))
    if stale or not has_docs:
        ingest_folder(db, tenant)
    _checked.add(tenant)


def search(db: Session, tenant: str, query: str) -> tuple[list[Hit], Usage]:
    ensure_embedded(db, tenant)
    [vector], usage = get_llm().embed([query])
    distance = Chunk.embedding.cosine_distance(vector).label("distance")
    rows = db.execute(
        select(Chunk.id, KnowledgeDocument.source, Chunk.heading, Chunk.content, distance)
        .join(KnowledgeDocument)
        .where(Chunk.tenant == tenant)
        .order_by(distance)
        .limit(settings.retrieval_top_k)
    ).all()
    # The mock's hashed embeddings live on another scale: keep anything not opposite.
    limit = settings.retrieval_max_distance if get_llm().name != "mock" else 0.99
    hits = [Hit(*row) for row in rows if row.distance <= limit]
    return hits, usage
