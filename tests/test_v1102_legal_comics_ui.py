from pathlib import Path


def test_legal_ui_hides_technical_hashes():
    handler = Path("app/handlers/legal.py").read_text(encoding="utf-8")
    service = Path("app/services/legal_documents.py").read_text(encoding="utf-8")
    template = Path("templates/legal.html").read_text(encoding="utf-8")
    assert "Контрольная сумма:" not in handler
    assert "SHA-256" not in service
    assert "document.digest" not in template
    assert "_telegram_filename" in handler


def test_comics_have_dedicated_catalog_and_author_entry():
    webapp = Path("app/webapp.py").read_text(encoding="utf-8")
    base = Path("templates/base.html").read_text(encoding="utf-8")
    comics = Path("templates/comics.html").read_text(encoding="utf-8")
    author = Path("templates/author.html").read_text(encoding="utf-8")
    js = Path("static/js/author.js").read_text(encoding="utf-8")
    assert '@app.get("/comics"' in webapp
    assert 'href="/comics"' in base
    for code in ("comic", "manga", "manhwa", "webtoon", "graphic_novel"):
        assert f'data-catalog-filter="{code}"' in comics
    assert 'newGraphicProject' in author
    assert "openNewProjectForm('graphic')" in js
