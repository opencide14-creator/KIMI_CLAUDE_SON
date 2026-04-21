"""
obsidian/ingest.py — Obsidian vault → ChromaDB ingest pipeline.

Pipeline per file:
  1. Parse: ObsidianParser → ObsidianPage
  2. Delta check: ManifestManager.needs_ingest()
  3. Delete stale chunks: remove old chunk IDs from ChromaDB
  4. Chunk: SemanticChunker → list[Chunk]
  5. Embed + store: ChromaDB collection.add()
  6. Update manifest

Two modes:
  boot_ingest(vault_path) — called during boot Phase RAG
    Full scan, respects manifest delta.
    Raises IngestFailed if ANY file fails. No partial ingestion.

  incremental_ingest(changed_paths) — called by VaultWatcher
    Processes only changed files.
    Logs errors but does not halt on single-file failures.

Embedding strategy:
  Production: pass embedding_function=None → ChromaDB DefaultEmbeddingFunction (ONNX, local).
  Tests: pass embedding_function=DeterministicEF() → zero network calls.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import chromadb

from core.exceptions import IngestFailed, VaultNotFound
from obsidian.chunker import SemanticChunker
from obsidian.manifest import ManifestManager
from obsidian.parser import ObsidianParser
from obsidian.vault import ObsidianVault

log = logging.getLogger(__name__)

COLLECTION_NAME = "obsidian_knowledge"


@dataclass
class IngestReport:
    files_processed: int   = 0
    files_skipped:   int   = 0   # unchanged since last ingest
    chunks_created:  int   = 0
    chunks_deleted:  int   = 0   # stale chunks removed before re-ingest
    errors:          list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return len(self.errors) == 0

    def summary(self) -> str:
        return (
            f"Ingest: {self.files_processed} processed, "
            f"{self.files_skipped} skipped, "
            f"{self.chunks_created} chunks created, "
            f"{self.chunks_deleted} stale chunks deleted"
            + (f", {len(self.errors)} errors" if self.errors else "")
        )


class ObsidianIngestPipeline:
    """
    Full Obsidian vault → ChromaDB ingest pipeline.

    pipeline = ObsidianIngestPipeline(
        chroma_collection=substrate.get_collection("obsidian_knowledge"),
        vault_path=Path("/my/vault"),
        embedding_function=DeterministicEF(),  # or None for production
    )
    report = pipeline.boot_ingest()
    """

    def __init__(
        self,
        chroma_collection: chromadb.Collection,
        vault_path: Path,
        embedding_function: Any = None,
    ) -> None:
        self._collection   = chroma_collection
        self._vault_path   = vault_path
        self._ef           = embedding_function
        self._vault        = ObsidianVault(vault_path)
        self._parser       = ObsidianParser()
        self._chunker      = SemanticChunker()
        self._manifest_mgr = ManifestManager()

    # ── Boot ingest ───────────────────────────────────────────────────────────

    def boot_ingest(self) -> IngestReport:
        """
        Full vault scan at boot. Respects manifest delta (skips unchanged files).
        Raises IngestFailed if vault is missing or any file raises an unexpected error.
        """
        self._vault.verify()
        md_files = self._vault.list_markdown_files()
        log.info("Boot ingest: %d markdown files found in %s", len(md_files), self._vault_path)

        manifest = self._manifest_mgr.load(self._vault_path)
        report = IngestReport()

        for file_path in md_files:
            try:
                ingested = self._ingest_file(file_path, manifest, report)
                if not ingested:
                    report.files_skipped += 1
            except Exception as e:
                msg = f"Failed to ingest {file_path.name}: {e}"
                log.error(msg)
                report.errors.append(msg)

        if report.errors:
            raise IngestFailed(
                f"Boot ingest completed with {len(report.errors)} error(s): "
                + "; ".join(report.errors[:3])
            )

        self._manifest_mgr.save(self._vault_path, manifest)
        log.info("Boot ingest complete: %s", report.summary())
        return report

    # ── Incremental ingest ────────────────────────────────────────────────────

    def incremental_ingest(self, changed_paths: list[Path]) -> IngestReport:
        """
        Process only the specified changed files.
        Logs errors but does not halt on single-file failures (watcher resilience).
        """
        manifest = self._manifest_mgr.load(self._vault_path)
        report   = IngestReport()

        for file_path in changed_paths:
            if not file_path.exists():
                # File was deleted — remove from manifest and ChromaDB
                self._remove_file(file_path, manifest, report)
                continue
            try:
                self._ingest_file(file_path, manifest, report, force=True)
            except Exception as e:
                msg = f"Incremental ingest failed for {file_path.name}: {e}"
                log.warning(msg)
                report.errors.append(msg)

        self._manifest_mgr.save(self._vault_path, manifest)
        log.info("Incremental ingest: %s", report.summary())
        return report

    # ── Core file ingestion ───────────────────────────────────────────────────

    def _ingest_file(
        self,
        file_path: Path,
        manifest: dict,
        report: IngestReport,
        force: bool = False,
    ) -> bool:
        """
        Ingest a single file. Returns True if ingested, False if skipped.
        Updates manifest and report in place.
        """
        # Delta check (skip if unchanged, unless force=True)
        if not force and not self._manifest_mgr.needs_ingest(
            file_path, manifest, self._vault_path
        ):
            return False

        rel_path = file_path.relative_to(self._vault_path).as_posix()

        # Delete stale chunks from previous ingest
        stale_ids = self._manifest_mgr.get_stale_chunk_ids(rel_path, manifest)
        if stale_ids:
            try:
                self._collection.delete(ids=stale_ids)
                report.chunks_deleted += len(stale_ids)
                log.debug("Deleted %d stale chunks for %s", len(stale_ids), rel_path)
            except Exception as e:
                log.warning("Could not delete stale chunks for %s: %s", rel_path, e)

        # Parse
        page = self._parser.parse(file_path)

        # Chunk
        chunks = self._chunker.chunk(page, self._vault_path)

        if chunks:
            # Prepare ChromaDB batch
            ids        = [c.chunk_id for c in chunks]
            documents  = [c.text for c in chunks]
            metadatas  = [c.metadata for c in chunks]

            # Add to ChromaDB — with or without explicit embeddings
            add_kwargs: dict[str, Any] = {
                "ids":       ids,
                "documents": documents,
                "metadatas": metadatas,
            }
            if self._ef is not None:
                add_kwargs["embeddings"] = self._ef(documents)

            self._collection.add(**add_kwargs)
            report.chunks_created += len(chunks)

        # Update manifest
        self._manifest_mgr.update_entry(
            manifest,
            rel_path=rel_path,
            content_hash=page.content_hash,
            chunk_ids=[c.chunk_id for c in chunks],
            title=page.title,
        )
        report.files_processed += 1
        log.debug("Ingested %s: %d chunks", rel_path, len(chunks))
        return True

    def _remove_file(
        self, file_path: Path, manifest: dict, report: IngestReport
    ) -> None:
        """Remove a deleted file's chunks from ChromaDB and manifest."""
        rel_path = file_path.relative_to(self._vault_path).as_posix()
        stale_ids = self._manifest_mgr.get_stale_chunk_ids(rel_path, manifest)
        if stale_ids:
            self._collection.delete(ids=stale_ids)
            report.chunks_deleted += len(stale_ids)
        manifest.pop(rel_path, None)
        log.debug("Removed deleted file %s from manifest", rel_path)

    # ── Query helpers ─────────────────────────────────────────────────────────

    def search(self, query: str, n_results: int = 5) -> list[dict[str, Any]]:
        """
        Semantic search over the obsidian_knowledge collection.
        Returns list of dicts with {id, text, metadata, distance}.
        """
        query_kwargs: dict[str, Any] = {"n_results": min(n_results, self._collection.count() or 1)}
        if self._ef is not None:
            query_kwargs["query_embeddings"] = self._ef([query])
        else:
            query_kwargs["query_texts"] = [query]

        results = self._collection.query(**query_kwargs)

        hits = []
        for i, doc_id in enumerate(results["ids"][0]):
            hits.append({
                "id":       doc_id,
                "text":     results["documents"][0][i],
                "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                "distance": results["distances"][0][i] if results.get("distances") else None,
            })
        return hits
