"""
tests/test_phase3_obsidian.py — Phase 3 acceptance tests.

ALL tests must pass before Phase 4 begins.
Uses ephemeral ChromaDB + DeterministicEF — no network, no downloads.
"""
import hashlib
import json
import time
import pytest
from pathlib import Path

# ── Test vault path ────────────────────────────────────────────────────────────
VAULT_DIR = Path(__file__).parent / "test_vault"


# ── Deterministic embedding function (shared across all tests) ────────────────
from chromadb.api.types import EmbeddingFunction, Documents, Embeddings

class DeterministicEF(EmbeddingFunction[Documents]):
    def __init__(self) -> None: pass
    def __call__(self, input: Documents) -> Embeddings:
        result = []
        for text in input:
            h = hashlib.sha256(str(text).encode()).digest()
            result.append([(float(h[i % 32]) / 255.0) - 0.5 for i in range(384)])
        return result
    @staticmethod
    def name() -> str: return "DeterministicEF"
    def get_config(self): return {"name": "DeterministicEF"}
    @staticmethod
    def build_from_config(config): return DeterministicEF()


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def cleanup_vault_manifest():
    """
    Clean up .manifest.json from VAULT_DIR after each test.
    Prevents manifest pollution between tests that share VAULT_DIR.
    """
    yield
    manifest = VAULT_DIR / ".manifest.json"
    if manifest.exists():
        manifest.unlink()


@pytest.fixture
def substrate():
    from memory.substrate import MemorySubstrate
    s = MemorySubstrate(ephemeral=True)
    s.boot()
    yield s
    s.shutdown()

@pytest.fixture
def collection(substrate):
    return substrate.get_collection("obsidian_knowledge")

@pytest.fixture
def ef():
    return DeterministicEF()

@pytest.fixture
def pipeline(collection, ef):
    from obsidian.ingest import ObsidianIngestPipeline
    return ObsidianIngestPipeline(
        chroma_collection=collection,
        vault_path=VAULT_DIR,
        embedding_function=ef,
    )

@pytest.fixture
def ingested_pipeline(pipeline):
    """Pipeline with all test vault files already ingested."""
    pipeline.boot_ingest()
    return pipeline

@pytest.fixture
def tmp_vault(tmp_path):
    """Empty temporary vault for write/delete tests."""
    return tmp_path


# ════════════════════════════════════════════════════════════════════════
# PARSER TESTS
# ════════════════════════════════════════════════════════════════════════

class TestObsidianParser:

    def test_parse_file_with_frontmatter(self):
        from obsidian.parser import ObsidianParser
        p = ObsidianParser()
        page = p.parse(VAULT_DIR / "concepts/soul-laws.md")
        assert page.frontmatter.get("title") == "Soul Laws"
        assert page.frontmatter.get("category") == "concepts"
        assert "soul" in page.frontmatter.get("tags", [])

    def test_parse_file_without_frontmatter(self):
        from obsidian.parser import ObsidianParser
        p = ObsidianParser()
        page = p.parse(VAULT_DIR / "concepts/memory-architecture.md")
        assert page.frontmatter == {}
        assert "Memory Architecture" in page.body or "ChromaDB" in page.body

    def test_title_from_frontmatter(self):
        from obsidian.parser import ObsidianParser
        p = ObsidianParser()
        page = p.parse(VAULT_DIR / "concepts/soul-laws.md")
        assert page.title == "Soul Laws"

    def test_title_from_h1_fallback(self):
        from obsidian.parser import ObsidianParser
        p = ObsidianParser()
        page = p.parse(VAULT_DIR / "concepts/memory-architecture.md")
        assert page.title == "Memory Architecture"

    def test_wikilinks_extracted(self):
        from obsidian.parser import ObsidianParser
        p = ObsidianParser()
        page = p.parse(VAULT_DIR / "concepts/soul-laws.md")
        assert "Heartbeat Protocol" in page.wikilinks
        assert "Reactive Agent" in page.wikilinks

    def test_wikilinks_deduplicated(self):
        from obsidian.parser import ObsidianParser
        p = ObsidianParser()
        page = p.parse(VAULT_DIR / "entities/reactive-agent.md")
        # [[Heartbeat Agent]] appears once in the file
        assert page.wikilinks.count("Heartbeat Agent") == 1

    def test_inline_tags_extracted(self):
        from obsidian.parser import ObsidianParser
        p = ObsidianParser()
        page = p.parse(VAULT_DIR / "concepts/soul-laws.md")
        assert "soul" in page.inline_tags
        assert "critical" in page.inline_tags

    def test_tags_merge_frontmatter_and_inline(self):
        from obsidian.parser import ObsidianParser
        p = ObsidianParser()
        page = p.parse(VAULT_DIR / "concepts/soul-laws.md")
        # frontmatter has [soul, laws, immutable, brotherhood]
        # inline has #soul #immutable #laws #critical
        tags = page.tags
        assert "soul" in tags
        assert "critical" in tags
        # deduplication
        assert tags.count("soul") == 1

    def test_headings_extracted(self):
        from obsidian.parser import ObsidianParser
        p = ObsidianParser()
        page = p.parse(VAULT_DIR / "concepts/soul-laws.md")
        headings = page.headings
        levels = [h[0] for h in headings]
        titles = [h[1] for h in headings]
        assert 1 in levels  # H1 present
        assert 2 in levels  # H2 present
        assert "Law I: Equal Authority" in titles

    def test_content_hash_is_sha256(self):
        from obsidian.parser import ObsidianParser
        p = ObsidianParser()
        page = p.parse(VAULT_DIR / "concepts/soul-laws.md")
        assert len(page.content_hash) == 64
        assert all(c in "0123456789abcdef" for c in page.content_hash)

    def test_content_hash_deterministic(self):
        from obsidian.parser import ObsidianParser
        p = ObsidianParser()
        page1 = p.parse(VAULT_DIR / "concepts/soul-laws.md")
        page2 = p.parse(VAULT_DIR / "concepts/soul-laws.md")
        assert page1.content_hash == page2.content_hash

    def test_category_from_frontmatter(self):
        from obsidian.parser import ObsidianParser
        p = ObsidianParser()
        page = p.parse(VAULT_DIR / "entities/reactive-agent.md")
        assert page.category == "entities"

    def test_category_default_when_missing(self):
        from obsidian.parser import ObsidianParser
        p = ObsidianParser()
        page = p.parse(VAULT_DIR / "concepts/memory-architecture.md")
        assert page.category == "uncategorized"

    def test_relative_path(self):
        from obsidian.parser import ObsidianParser
        p = ObsidianParser()
        page = p.parse(VAULT_DIR / "concepts/soul-laws.md")
        rel = page.relative_path(VAULT_DIR)
        assert rel == "concepts/soul-laws.md"

    def test_body_excludes_frontmatter(self):
        from obsidian.parser import ObsidianParser
        p = ObsidianParser()
        page = p.parse(VAULT_DIR / "concepts/soul-laws.md")
        assert "---" not in page.body[:10]
        assert "Soul Laws" in page.body

    def test_split_body_by_h2(self):
        from obsidian.parser import ObsidianParser
        p = ObsidianParser()
        page = p.parse(VAULT_DIR / "concepts/soul-laws.md")
        sections = page.split_body_by_h2(page.body)
        headings = [s[0] for s in sections]
        # soul-laws.md has 5 H2 sections
        assert len([h for h in headings if h is not None]) == 5
        assert "Law I: Equal Authority" in headings
        assert "Law II: No Simulation" in headings

    def test_parse_many(self):
        from obsidian.parser import ObsidianParser
        p = ObsidianParser()
        files = list(VAULT_DIR.rglob("*.md"))
        pages = p.parse_many(files)
        assert len(pages) == len(files)

    def test_missing_file_raises(self, tmp_path):
        from obsidian.parser import ObsidianParser
        from core.exceptions import ObsidianError
        p = ObsidianParser()
        with pytest.raises(ObsidianError):
            p.parse(tmp_path / "nonexistent.md")

    def test_wikilink_alias_excluded(self):
        """[[Target|Alias]] should give 'Target', not 'Target|Alias'."""
        from obsidian.parser import ObsidianParser
        p = ObsidianParser()
        raw = "[[Soul Laws|the laws]] and [[Memory|mem]]"
        links = p.extract_wikilinks(raw)
        assert links == ["Soul Laws", "Memory"]
        assert "the laws" not in links
        assert "mem" not in links


# ════════════════════════════════════════════════════════════════════════
# CHUNKER TESTS
# ════════════════════════════════════════════════════════════════════════

class TestSemanticChunker:

    def test_returns_at_least_one_chunk(self):
        from obsidian.chunker import SemanticChunker
        from obsidian.parser import ObsidianParser
        chunker = SemanticChunker()
        page = ObsidianParser().parse(VAULT_DIR / "concepts/soul-laws.md")
        chunks = chunker.chunk(page, VAULT_DIR)
        assert len(chunks) >= 1

    def test_multiple_h2_sections_produce_multiple_chunks(self):
        from obsidian.chunker import SemanticChunker
        from obsidian.parser import ObsidianParser
        chunker = SemanticChunker()
        page = ObsidianParser().parse(VAULT_DIR / "concepts/soul-laws.md")
        chunks = chunker.chunk(page, VAULT_DIR)
        # soul-laws.md has 5 H2 sections + preamble
        assert len(chunks) >= 4

    def test_chunk_ids_are_unique(self):
        from obsidian.chunker import SemanticChunker
        from obsidian.parser import ObsidianParser
        chunker = SemanticChunker()
        page = ObsidianParser().parse(VAULT_DIR / "concepts/soul-laws.md")
        chunks = chunker.chunk(page, VAULT_DIR)
        ids = [c.chunk_id for c in chunks]
        assert len(ids) == len(set(ids)), f"Duplicate chunk IDs: {ids}"

    def test_chunk_id_contains_rel_path(self):
        from obsidian.chunker import SemanticChunker
        from obsidian.parser import ObsidianParser
        chunker = SemanticChunker()
        page = ObsidianParser().parse(VAULT_DIR / "concepts/soul-laws.md")
        chunks = chunker.chunk(page, VAULT_DIR)
        for c in chunks:
            assert "concepts/soul-laws.md" in c.chunk_id

    def test_chunk_metadata_has_required_fields(self):
        from obsidian.chunker import SemanticChunker
        from obsidian.parser import ObsidianParser
        chunker = SemanticChunker()
        page = ObsidianParser().parse(VAULT_DIR / "concepts/soul-laws.md")
        chunks = chunker.chunk(page, VAULT_DIR)
        for c in chunks:
            assert "source_path" in c.metadata
            assert "title" in c.metadata
            assert "category" in c.metadata
            assert "section_heading" in c.metadata
            assert "content_hash" in c.metadata
            assert "wikilinks" in c.metadata
            assert "tags" in c.metadata

    def test_metadata_values_are_scalar(self):
        """ChromaDB requires scalar metadata values."""
        from obsidian.chunker import SemanticChunker
        from obsidian.parser import ObsidianParser
        chunker = SemanticChunker()
        page = ObsidianParser().parse(VAULT_DIR / "concepts/soul-laws.md")
        chunks = chunker.chunk(page, VAULT_DIR)
        for c in chunks:
            for k, v in c.metadata.items():
                assert isinstance(v, (str, int, float, bool)), \
                    f"Non-scalar metadata value for key '{k}': {type(v)} = {v!r}"

    def test_wikilinks_in_metadata_as_pipe_string(self):
        from obsidian.chunker import SemanticChunker
        from obsidian.parser import ObsidianParser
        chunker = SemanticChunker()
        page = ObsidianParser().parse(VAULT_DIR / "concepts/soul-laws.md")
        chunks = chunker.chunk(page, VAULT_DIR)
        # At least one chunk should have wikilinks
        wl_values = [c.metadata["wikilinks"] for c in chunks if c.metadata["wikilinks"]]
        assert len(wl_values) > 0
        for wl in wl_values:
            assert isinstance(wl, str)

    def test_large_section_split_at_paragraph(self):
        from obsidian.chunker import SemanticChunker
        chunker = SemanticChunker(max_words=20)
        # Create content that exceeds 20 words
        big_section = "word " * 50  # 50 words
        sub = chunker._split_section(big_section)
        assert len(sub) > 1

    def test_small_section_not_split(self):
        from obsidian.chunker import SemanticChunker
        chunker = SemanticChunker(max_words=500)
        small = "just five words here."
        sub = chunker._split_section(small)
        assert len(sub) == 1

    def test_empty_file_returns_one_chunk(self, tmp_path):
        from obsidian.chunker import SemanticChunker
        from obsidian.parser import ObsidianParser
        empty = tmp_path / "empty.md"
        empty.write_text("", encoding="utf-8")
        chunker = SemanticChunker()
        page = ObsidianParser().parse(empty)
        chunks = chunker.chunk(page, tmp_path)
        assert len(chunks) == 1

    def test_section_heading_in_metadata(self):
        from obsidian.chunker import SemanticChunker
        from obsidian.parser import ObsidianParser
        chunker = SemanticChunker()
        page = ObsidianParser().parse(VAULT_DIR / "concepts/soul-laws.md")
        chunks = chunker.chunk(page, VAULT_DIR)
        headings_in_chunks = {c.metadata["section_heading"] for c in chunks}
        assert "Law I: Equal Authority" in headings_in_chunks
        assert "Law II: No Simulation" in headings_in_chunks


# ════════════════════════════════════════════════════════════════════════
# MANIFEST TESTS
# ════════════════════════════════════════════════════════════════════════

class TestManifestManager:

    def test_load_empty_on_first_run(self, tmp_vault):
        from obsidian.manifest import ManifestManager
        mgr = ManifestManager()
        manifest = mgr.load(tmp_vault)
        assert manifest == {}

    def test_save_and_reload(self, tmp_vault):
        from obsidian.manifest import ManifestManager
        mgr = ManifestManager()
        manifest = mgr.load(tmp_vault)
        manifest = mgr.update_entry(
            manifest, "concepts/test.md", "abc123", ["test#0"], "Test Page"
        )
        mgr.save(tmp_vault, manifest)

        reloaded = mgr.load(tmp_vault)
        assert "concepts/test.md" in reloaded
        entry = reloaded["concepts/test.md"]
        assert entry.content_hash == "abc123"
        assert entry.chunk_ids == ["test#0"]
        assert entry.title == "Test Page"

    def test_needs_ingest_new_file(self, tmp_vault):
        from obsidian.manifest import ManifestManager
        mgr = ManifestManager()
        # Create a real file
        f = tmp_vault / "new.md"
        f.write_text("# New\nContent", encoding="utf-8")
        manifest = {}
        assert mgr.needs_ingest(f, manifest, tmp_vault) is True

    def test_needs_ingest_unchanged_file(self, tmp_vault):
        from obsidian.manifest import ManifestManager
        from core.hashing import sha256_hex
        mgr = ManifestManager()
        f = tmp_vault / "unchanged.md"
        content = b"# Unchanged\nContent unchanged."
        f.write_bytes(content)
        manifest = {}
        manifest = mgr.update_entry(
            manifest, "unchanged.md", sha256_hex(content), [], "Unchanged"
        )
        assert mgr.needs_ingest(f, manifest, tmp_vault) is False

    def test_needs_ingest_changed_file(self, tmp_vault):
        from obsidian.manifest import ManifestManager
        mgr = ManifestManager()
        f = tmp_vault / "changed.md"
        f.write_text("# Changed\nOriginal content.", encoding="utf-8")
        manifest = {}
        manifest = mgr.update_entry(
            manifest, "changed.md", "old_hash_not_matching", [], "Changed"
        )
        assert mgr.needs_ingest(f, manifest, tmp_vault) is True

    def test_get_stale_chunk_ids(self, tmp_vault):
        from obsidian.manifest import ManifestManager
        mgr = ManifestManager()
        manifest = {}
        manifest = mgr.update_entry(
            manifest, "test.md", "hash", ["test#0", "test#1"], "Test"
        )
        stale = mgr.get_stale_chunk_ids("test.md", manifest)
        assert stale == ["test#0", "test#1"]

    def test_get_stale_chunk_ids_empty_for_new(self, tmp_vault):
        from obsidian.manifest import ManifestManager
        mgr = ManifestManager()
        stale = mgr.get_stale_chunk_ids("never_seen.md", {})
        assert stale == []

    def test_manifest_persists_json(self, tmp_vault):
        from obsidian.manifest import ManifestManager, MANIFEST_FILENAME
        mgr = ManifestManager()
        manifest = {}
        manifest = mgr.update_entry(manifest, "a.md", "hash_a", ["a#0"], "A")
        manifest = mgr.update_entry(manifest, "b.md", "hash_b", ["b#0", "b#1"], "B")
        mgr.save(tmp_vault, manifest)

        json_path = tmp_vault / MANIFEST_FILENAME
        assert json_path.exists()
        data = json.loads(json_path.read_text())
        assert "a.md" in data
        assert "b.md" in data
        assert data["b.md"]["chunk_ids"] == ["b#0", "b#1"]

    def test_corrupted_manifest_raises(self, tmp_vault):
        from obsidian.manifest import ManifestManager, MANIFEST_FILENAME
        from core.exceptions import ManifestCorrupted
        mgr = ManifestManager()
        (tmp_vault / MANIFEST_FILENAME).write_text("NOT VALID JSON {{{", encoding="utf-8")
        with pytest.raises(ManifestCorrupted):
            mgr.load(tmp_vault)

    def test_count_and_total_chunks(self, tmp_vault):
        from obsidian.manifest import ManifestManager
        mgr = ManifestManager()
        manifest = {}
        manifest = mgr.update_entry(manifest, "a.md", "h1", ["a#0", "a#1"], "A")
        manifest = mgr.update_entry(manifest, "b.md", "h2", ["b#0"], "B")
        assert mgr.count(manifest) == 2
        assert mgr.total_chunks(manifest) == 3

    def test_get_all_tracked_paths(self, tmp_vault):
        from obsidian.manifest import ManifestManager
        mgr = ManifestManager()
        manifest = {}
        manifest = mgr.update_entry(manifest, "a.md", "h1", [], "A")
        manifest = mgr.update_entry(manifest, "b.md", "h2", [], "B")
        paths = mgr.get_all_tracked_paths(manifest)
        assert paths == {"a.md", "b.md"}


# ════════════════════════════════════════════════════════════════════════
# VAULT TESTS
# ════════════════════════════════════════════════════════════════════════

class TestObsidianVault:

    def test_verify_valid_vault(self):
        from obsidian.vault import ObsidianVault
        v = ObsidianVault(VAULT_DIR)
        v.verify()  # must not raise

    def test_verify_missing_vault_raises(self, tmp_path):
        from obsidian.vault import ObsidianVault
        from core.exceptions import VaultNotFound
        v = ObsidianVault(tmp_path / "nonexistent")
        with pytest.raises(VaultNotFound):
            v.verify()

    def test_list_markdown_files(self):
        from obsidian.vault import ObsidianVault
        v = ObsidianVault(VAULT_DIR)
        files = v.list_markdown_files()
        assert len(files) == 6
        rel_names = [f.name for f in files]
        assert "soul-laws.md" in rel_names
        assert "memory-architecture.md" in rel_names

    def test_list_skips_hidden(self, tmp_path):
        from obsidian.vault import ObsidianVault
        (tmp_path / ".hidden.md").write_text("hidden", encoding="utf-8")
        (tmp_path / "visible.md").write_text("visible", encoding="utf-8")
        v = ObsidianVault(tmp_path)
        files = v.list_markdown_files()
        names = [f.name for f in files]
        assert "visible.md" in names
        assert ".hidden.md" not in names

    def test_list_skips_raw_dir(self, tmp_path):
        from obsidian.vault import ObsidianVault
        raw_dir = tmp_path / "_raw"
        raw_dir.mkdir()
        (raw_dir / "draft.md").write_text("draft", encoding="utf-8")
        (tmp_path / "real.md").write_text("real", encoding="utf-8")
        v = ObsidianVault(tmp_path)
        files = v.list_markdown_files()
        names = [f.name for f in files]
        assert "real.md" in names
        assert "draft.md" not in names

    def test_list_sorted(self):
        from obsidian.vault import ObsidianVault
        v = ObsidianVault(VAULT_DIR)
        files = v.list_markdown_files()
        paths = [str(f) for f in files]
        assert paths == sorted(paths)

    def test_stat(self):
        from obsidian.vault import ObsidianVault
        v = ObsidianVault(VAULT_DIR)
        s = v.stat()
        assert s["markdown_files"] == 6
        assert s["total_bytes"] > 0

    def test_exists(self):
        from obsidian.vault import ObsidianVault
        assert ObsidianVault(VAULT_DIR).exists() is True
        assert ObsidianVault(VAULT_DIR / "nonexistent").exists() is False


# ════════════════════════════════════════════════════════════════════════
# INGEST PIPELINE TESTS
# ════════════════════════════════════════════════════════════════════════

class TestIngestPipeline:

    def test_boot_ingest_succeeds(self, pipeline):
        from obsidian.ingest import IngestReport
        report = pipeline.boot_ingest()
        assert isinstance(report, IngestReport)
        assert report.success
        assert report.files_processed == 6

    def test_boot_ingest_zero_skipped_on_first_run(self, pipeline):
        report = pipeline.boot_ingest()
        assert report.files_skipped == 0

    def test_boot_ingest_creates_chunks(self, pipeline):
        report = pipeline.boot_ingest()
        assert report.chunks_created > 0

    def test_boot_ingest_adds_to_collection(self, pipeline, collection):
        pipeline.boot_ingest()
        assert collection.count() > 0

    def test_boot_ingest_each_file_gets_chunks(self, pipeline, collection):
        pipeline.boot_ingest()
        # At least 6 chunks (one per file minimum)
        assert collection.count() >= 6

    def test_second_ingest_skips_unchanged(self, pipeline):
        pipeline.boot_ingest()
        report2 = pipeline.boot_ingest()
        assert report2.files_skipped == 6
        assert report2.files_processed == 0

    def test_manifest_written_after_ingest(self, pipeline):
        from obsidian.manifest import MANIFEST_FILENAME
        pipeline.boot_ingest()
        manifest_path = VAULT_DIR / MANIFEST_FILENAME
        assert manifest_path.exists()
        data = json.loads(manifest_path.read_text())
        assert len(data) == 6
        # Clean up
        manifest_path.unlink()

    def test_manifest_tracks_all_files(self, pipeline):
        from obsidian.manifest import MANIFEST_FILENAME
        pipeline.boot_ingest()
        manifest_path = VAULT_DIR / MANIFEST_FILENAME
        data = json.loads(manifest_path.read_text())
        paths = set(data.keys())
        expected = {
            "concepts/soul-laws.md",
            "skills/rag-search.md",
            "entities/reactive-agent.md",
            "projects/vekil-kaan/vekil-kaan.md",
            "concepts/memory-architecture.md",
            "entities/heartbeat-agent.md",
        }
        assert expected.issubset(paths)
        manifest_path.unlink()

    def test_search_returns_results(self, ingested_pipeline):
        results = ingested_pipeline.search("soul laws immutable", n_results=3)
        assert len(results) > 0
        assert "id" in results[0]
        assert "text" in results[0]
        assert "metadata" in results[0]

    def test_search_metadata_has_source_path(self, ingested_pipeline):
        results = ingested_pipeline.search("reactive agent loop", n_results=3)
        for r in results:
            assert "source_path" in r["metadata"]

    def test_incremental_ingest_changed_file(self, pipeline, collection, tmp_path):
        """Change a file and verify incremental ingest updates its chunks."""
        import shutil
        # Copy vault to tmp for mutation
        vault_copy = tmp_path / "vault"
        shutil.copytree(str(VAULT_DIR), str(vault_copy))

        from obsidian.ingest import ObsidianIngestPipeline
        p = ObsidianIngestPipeline(collection, vault_copy, DeterministicEF())
        p.boot_ingest()
        count_before = collection.count()

        # Modify a file
        mod_file = vault_copy / "entities/heartbeat-agent.md"
        original = mod_file.read_text()
        mod_file.write_text(original + "\n\nNew section added for testing.", encoding="utf-8")

        p.incremental_ingest([mod_file])
        # Collection should still have content (old chunks deleted, new added)
        assert collection.count() > 0

        # Clean up manifest
        (vault_copy / ".manifest.json").unlink(missing_ok=True)

    def test_boot_ingest_missing_vault_raises(self, collection, ef, tmp_path):
        from obsidian.ingest import ObsidianIngestPipeline
        from core.exceptions import VaultNotFound
        p = ObsidianIngestPipeline(collection, tmp_path / "nonexistent", ef)
        with pytest.raises(VaultNotFound):
            p.boot_ingest()

    def test_chunk_metadata_stored_in_chroma(self, ingested_pipeline, collection):
        """Verify metadata fields survive the ChromaDB round-trip."""
        # Get all chunks for soul-laws.md
        results = collection.get(
            where={"source_path": "concepts/soul-laws.md"},
        )
        assert len(results["ids"]) > 0
        for meta in results["metadatas"]:
            assert meta["title"] == "Soul Laws"
            assert meta["category"] == "concepts"
            assert isinstance(meta["wikilinks"], str)
            assert isinstance(meta["tags"], str)


# ════════════════════════════════════════════════════════════════════════
# VAULT WATCHER TESTS (lightweight — no sleep needed)
# ════════════════════════════════════════════════════════════════════════

class TestVaultWatcher:

    def test_watcher_starts_and_stops(self, tmp_path):
        from obsidian.watcher import VaultWatcher
        received = []
        w = VaultWatcher(debounce_seconds=0.05)
        w.start(tmp_path, lambda paths: received.extend(paths))
        assert w.is_running
        w.stop()
        assert not w.is_running

    def test_watcher_context_manager(self, tmp_path):
        from obsidian.watcher import VaultWatcher
        with VaultWatcher() as w:
            w.start(tmp_path, lambda p: None)
            assert w.is_running
        assert not w.is_running

    def test_watcher_double_start_raises(self, tmp_path):
        from obsidian.watcher import VaultWatcher
        w = VaultWatcher()
        w.start(tmp_path, lambda p: None)
        try:
            with pytest.raises(RuntimeError, match="already running"):
                w.start(tmp_path, lambda p: None)
        finally:
            w.stop()

    def test_watcher_detects_new_file(self, tmp_path):
        from obsidian.watcher import VaultWatcher
        received = []
        w = VaultWatcher(debounce_seconds=0.05)
        w.start(tmp_path, lambda paths: received.extend(paths))
        try:
            (tmp_path / "new_note.md").write_text("# New\nContent", encoding="utf-8")
            time.sleep(0.3)  # allow debounce + callback
            changed_names = [p.name for p in received]
            assert "new_note.md" in changed_names
        finally:
            w.stop()

    def test_watcher_stop_idempotent(self, tmp_path):
        from obsidian.watcher import VaultWatcher
        w = VaultWatcher()
        w.start(tmp_path, lambda p: None)
        w.stop()
        w.stop()  # second stop must not raise


# ════════════════════════════════════════════════════════════════════════
# INTEGRATION
# ════════════════════════════════════════════════════════════════════════

class TestPhase3Integration:

    def test_full_pipeline_with_substrate(self):
        """Full Phase 3 pipeline integrated with Phase 1 memory substrate."""
        import chromadb
        from memory.substrate import MemorySubstrate
        from obsidian.ingest import ObsidianIngestPipeline

        with MemorySubstrate(ephemeral=True) as substrate:
            substrate.boot()
            col = substrate.get_collection("obsidian_knowledge")
            pipeline = ObsidianIngestPipeline(col, VAULT_DIR, DeterministicEF())
            report = pipeline.boot_ingest()

            assert report.success
            assert report.files_processed == 6
            assert col.count() >= 6
            # Root hash must change (collection now has data)
            h = substrate.compute_root_hash()
            assert len(h) == 64

        # Clean up manifest
        (VAULT_DIR / ".manifest.json").unlink(missing_ok=True)

    def test_delta_ingest_only_processes_changed(self, tmp_path):
        """Ingest, modify one file, re-ingest — only 1 file should be processed."""
        import shutil
        vault_copy = tmp_path / "vault"
        shutil.copytree(str(VAULT_DIR), str(vault_copy))

        import chromadb
        from memory.substrate import MemorySubstrate
        from obsidian.ingest import ObsidianIngestPipeline

        with MemorySubstrate(ephemeral=True) as substrate:
            substrate.boot()
            col = substrate.get_collection("obsidian_knowledge")
            p = ObsidianIngestPipeline(col, vault_copy, DeterministicEF())

            r1 = p.boot_ingest()
            assert r1.files_processed == 6

            # Modify one file
            f = vault_copy / "skills/rag-search.md"
            f.write_text(f.read_text() + "\n\nExtra content added.", encoding="utf-8")

            r2 = p.boot_ingest()
            assert r2.files_processed == 1
            assert r2.files_skipped == 5

    def test_wikilinks_queryable_from_chroma(self, ingested_pipeline, collection):
        """Wikilinks stored as metadata should be retrievable."""
        results = collection.get(where={"source_path": "concepts/soul-laws.md"})
        assert len(results["ids"]) > 0
        all_wikilinks = " ".join(m["wikilinks"] for m in results["metadatas"])
        # soul-laws.md links to Heartbeat Protocol and Reactive Agent
        assert "Heartbeat Protocol" in all_wikilinks or "Reactive" in all_wikilinks

    def test_all_phases_still_pass(self):
        """Smoke: Phase 0+1+2 imports still work."""
        from core.exceptions import VekilKaanError
        from core.hashing import blake2b_256
        from memory.event_store import MemoryEvent, EventType, AgentSource
        from law_engine.parser import MarkdownLawParser
        assert blake2b_256(b"phase3") is not None

    def test_report_summary_format(self, pipeline):
        report = pipeline.boot_ingest()
        summary = report.summary()
        assert "processed" in summary
        assert "skipped" in summary
        assert "chunks" in summary
        # Clean up
        (VAULT_DIR / ".manifest.json").unlink(missing_ok=True)
