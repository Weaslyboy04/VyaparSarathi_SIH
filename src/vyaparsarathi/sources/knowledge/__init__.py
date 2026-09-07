"""The committed Phase 5 knowledge corpus — documents, chunks, and the
reviewed parameter registry (CLAUDE.md §18, §19, §23).

A local, offline artifact set: the only ingestion path is an operator running
``scripts/build_knowledge_corpus.py`` / ``scripts/build_parameter_registry.py``
against downloaded documents (see ``data/knowledge/SOURCES.md``). Nothing in
this package makes a network call at request time.
"""

from vyaparsarathi.sources.knowledge.loader import FileCorpusStore

__all__ = ["FileCorpusStore"]
