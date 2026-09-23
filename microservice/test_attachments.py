"""
Tests for the attachment temp-file helpers. Run from inside microservice/ with either:
    python test_attachments.py
    pytest test_attachments.py
No API calls.
"""
import io
import os
import tempfile

import attachments
from attachments import safe_extension, save_and_extract


def test_supported_extensions_are_kept():
    assert safe_extension("report.pdf") == ".pdf"
    assert safe_extension("cv.docx") == ".docx"


def test_extension_is_lowercased():
    assert safe_extension("Report.PDF") == ".pdf"
    assert safe_extension("CV.DocX") == ".docx"


def test_path_parts_are_ignored():
    assert safe_extension("../../etc/report.pdf") == ".pdf"
    assert safe_extension("..\\..\\evil.docx") == ".docx"
    assert safe_extension("x.pdf/../../y") == ""


def test_unsupported_extensions_become_empty():
    assert safe_extension("notes.txt") == ""
    assert safe_extension("old.doc") == ""
    assert safe_extension("a.pdf.exe") == ""
    assert safe_extension("README") == ""


def test_missing_filename_becomes_empty():
    assert safe_extension("") == ""
    assert safe_extension(None) == ""


def test_save_and_extract_uses_temp_dir_and_deletes_file():
    created = []
    real_mkstemp = tempfile.mkstemp

    def spy_mkstemp(*args, **kwargs):
        fd, path = real_mkstemp(*args, **kwargs)
        created.append(path)
        return fd, path

    attachments.tempfile.mkstemp = spy_mkstemp
    try:
        # .txt isn't parsed, so this only exercises the temp-file handling.
        result = save_and_extract("../../notes.txt", io.BytesIO(b"hello"))
    finally:
        attachments.tempfile.mkstemp = real_mkstemp

    assert result == ("", True)
    assert len(created) == 1
    path = created[0]
    assert os.path.dirname(path) == tempfile.gettempdir()
    assert "notes" not in os.path.basename(path)
    assert not os.path.exists(path)


if __name__ == "__main__":
    tests = [(name, fn) for name, fn in list(globals().items())
             if name.startswith("test_") and callable(fn)]
    for name, fn in tests:
        fn()
        print(f"ok   {name}")
    print(f"all {len(tests)} tests passed")
