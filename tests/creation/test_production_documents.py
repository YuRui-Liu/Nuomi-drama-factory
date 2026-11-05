from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib

import pytest

from novelvideo.creation.production_documents import (
    DocumentConflict,
    DocumentValidation,
    ProductionDocuments,
)


def test_document_round_trip_preserves_utf8_and_returns_content_hash(tmp_path):
    documents = ProductionDocuments(tmp_path)

    written = documents.write("episodes/003/script.md", "第一稿\n雪山与海", expected_hash=None)

    assert written == {
        "path": "episodes/003/script.md",
        "text": "第一稿\n雪山与海",
        "hash": hashlib.sha256("第一稿\n雪山与海".encode("utf-8")).hexdigest(),
    }
    assert documents.read("episodes/003/script.md") == written


def test_stale_editor_cannot_overwrite_a_newer_document(tmp_path):
    documents = ProductionDocuments(tmp_path)
    first = documents.write("episodes/003/script.md", "初稿", expected_hash=None)
    documents.write(first["path"], "画布修改", expected_hash=first["hash"])

    with pytest.raises(DocumentConflict):
        documents.write(first["path"], "旧的 Codex 内容", expected_hash=first["hash"])

    assert documents.read(first["path"])["text"] == "画布修改"


@pytest.mark.parametrize("path", ["../outside.md", "/tmp/outside.md", "episodes/../outside.md"])
def test_document_paths_must_be_relative_and_stay_inside_the_project(tmp_path, path):
    documents = ProductionDocuments(tmp_path / "project")

    with pytest.raises(DocumentValidation):
        documents.write(path, "outside", expected_hash=None)


def test_symlinked_directory_and_file_cannot_escape_the_project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "script.md").write_text("untouched", encoding="utf-8")
    (project / "linked-directory").symlink_to(outside, target_is_directory=True)
    (project / "linked-file.md").symlink_to(outside / "script.md")
    documents = ProductionDocuments(project)

    with pytest.raises(DocumentValidation):
        documents.read("linked-directory/script.md")
    with pytest.raises(DocumentValidation):
        documents.write("linked-directory/new.md", "escape", expected_hash=None)
    with pytest.raises(DocumentValidation):
        documents.write("linked-file.md", "escape", expected_hash=None)

    assert (outside / "script.md").read_text(encoding="utf-8") == "untouched"
    assert not (outside / "new.md").exists()


def test_none_hash_only_creates_a_missing_document(tmp_path):
    documents = ProductionDocuments(tmp_path)
    first = documents.write("episodes/003/script.md", "初稿", expected_hash=None)

    with pytest.raises(DocumentConflict):
        documents.write(first["path"], "覆盖", expected_hash=None)


def test_immutable_receipt_is_create_only_and_reserved_from_editor_writes(tmp_path):
    documents = ProductionDocuments(tmp_path)
    path = "episodes/003/handoff-receipts/0123456789abcdef0123456789abcdef.md"

    written = documents.write_immutable(path, "已确认剧本快照")

    assert written["path"] == path
    assert written["text"] == "已确认剧本快照"
    assert written["hash"] == hashlib.sha256("已确认剧本快照".encode("utf-8")).hexdigest()
    assert documents.write_immutable(path, "已确认剧本快照") == written
    with pytest.raises(DocumentConflict):
        documents.write_immutable(path, "不同的剧本快照")
    with pytest.raises(DocumentValidation, match="immutable"):
        documents.write(path, "画布不能改回执", expected_hash=written["hash"])
    assert documents.read(path) == written


def test_two_writers_with_the_same_hash_have_only_one_winner(tmp_path):
    documents = ProductionDocuments(tmp_path)
    first = documents.write("episodes/003/script.md", "初稿", expected_hash=None)

    def write(text):
        try:
            documents.write(first["path"], text, expected_hash=first["hash"])
            return "written"
        except DocumentConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(write, ["画布修改", "Codex 修改"]))

    assert sorted(outcomes) == ["conflict", "written"]
    assert documents.read(first["path"])["text"] in {"画布修改", "Codex 修改"}
