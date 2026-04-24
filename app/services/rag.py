"""
Policy RAG service.

The policy document is small (~4 KB) so we take a hybrid approach:
  1. The full document is always included in the system prompt.
  2. We also build a FAISS index over semantic chunks so the most relevant
     sections can be highlighted at the top of the context window.

The sentence-transformers model is loaded lazily on first use.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from app.core.config import get_settings
from app.core.logging import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)

_SECTION_PATTERN = re.compile(r"^#{1,3}\s+.+", re.MULTILINE)


@dataclass
class PolicyChunk:
    heading: str
    content: str
    index: int


class PolicyRAGService:
    """
    Semantic search over the QuickBites policy & FAQ document.

    Thread-safe lazy initialisation: the embedding model and FAISS index are
    built once on first access and then cached.
    """

    def __init__(self, policy_path: Path | None = None) -> None:
        self._policy_path = policy_path or get_settings().policy_doc_path
        self._chunks: list[PolicyChunk] = []
        self._full_text: str = ""
        self._index = None  # faiss.IndexFlatL2
        self._model = None  # SentenceTransformer
        self._lock = threading.Lock()
        self._ready = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def full_text(self) -> str:
        """Return the complete policy document text."""
        self._ensure_loaded()
        return self._full_text

    def search(self, query: str, top_k: int = 3) -> list[PolicyChunk]:
        """Return the top-k most semantically relevant policy chunks."""
        self._ensure_ready()
        import numpy as np

        query_vec = self._model.encode([query], normalize_embeddings=True).astype("float32")
        distances, indices = self._index.search(query_vec, min(top_k, len(self._chunks)))
        results = []
        for idx in indices[0]:
            if idx >= 0:
                results.append(self._chunks[idx])
        return results

    def get_relevant_context(self, query: str, top_k: int = 3) -> str:
        """
        Return a formatted string with the most relevant policy sections.

        This is appended to the system prompt to surface the sections most
        pertinent to the current customer issue.
        """
        try:
            chunks = self.search(query, top_k=top_k)
            if not chunks:
                return self._full_text
            sections = "\n\n".join(f"## {c.heading}\n{c.content}" for c in chunks)
            return f"[Most relevant policy sections for this query]\n\n{sections}"
        except Exception as exc:
            logger.warning("RAG search failed, falling back to full text: %s", exc)
            return self._full_text

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> None:
        if self._full_text:
            return
        with self._lock:
            if self._full_text:
                return
            text = self._policy_path.read_text(encoding="utf-8")
            self._full_text = text
            self._chunks = self._split_into_chunks(text)
            logger.info("Policy document loaded: %d chars, %d chunks", len(text), len(self._chunks))

    def _ensure_ready(self) -> None:
        self._ensure_loaded()
        if self._ready:
            return
        with self._lock:
            if self._ready:
                return
            self._build_index()
            self._ready = True

    def _split_into_chunks(self, text: str) -> list[PolicyChunk]:
        """Split policy text into sections based on markdown headings."""
        matches = list(_SECTION_PATTERN.finditer(text))
        if not matches:
            return [PolicyChunk(heading="Policy", content=text, index=0)]

        chunks: list[PolicyChunk] = []
        for i, match in enumerate(matches):
            heading = match.group(0).lstrip("#").strip()
            start = match.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            content = text[start:end].strip()
            if content:
                chunks.append(PolicyChunk(heading=heading, content=content, index=i))

        return chunks

    def _build_index(self) -> None:
        """Build a FAISS flat-L2 index over chunk embeddings."""
        try:
            import faiss
            import numpy as np
            from sentence_transformers import SentenceTransformer

            logger.info("Loading sentence-transformers model…")
            self._model = SentenceTransformer("all-MiniLM-L6-v2")

            texts = [f"{c.heading}\n{c.content}" for c in self._chunks]
            embeddings = self._model.encode(texts, normalize_embeddings=True).astype("float32")

            dim = embeddings.shape[1]
            self._index = faiss.IndexFlatIP(dim)  # inner product on normalised vecs = cosine sim
            self._index.add(embeddings)
            logger.info("FAISS index built: %d vectors, dim=%d", len(self._chunks), dim)
        except ImportError as exc:
            logger.warning("faiss/sentence-transformers not available (%s); RAG disabled", exc)
            self._index = None
            self._model = None


# Module-level singleton
_rag_service: PolicyRAGService | None = None
_rag_lock = threading.Lock()


def get_rag_service() -> PolicyRAGService:
    global _rag_service
    if _rag_service is None:
        with _rag_lock:
            if _rag_service is None:
                _rag_service = PolicyRAGService()
    return _rag_service
