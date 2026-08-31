import re
from html.parser import HTMLParser
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
HTML_PATH = PROJECT_ROOT / "ui" / "index.html"
CSS_PATH = PROJECT_ROOT / "ui" / "styles.css"
JS_PATH = PROJECT_ROOT / "ui" / "app.js"


class _InterfaceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.translation_keys: set[str] = set()

    def handle_starttag(self, _tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if attributes.get("id"):
            self.ids.append(attributes["id"])
        for name in ("data-i18n", "data-i18n-placeholder", "data-i18n-aria"):
            if attributes.get(name):
                self.translation_keys.add(attributes[name])


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_ui_has_unique_ids_and_preserves_functional_mount_points() -> None:
    parser = _InterfaceParser()
    parser.feed(_read(HTML_PATH))

    assert len(parser.ids) == len(set(parser.ids))
    assert {
        "analysis-form",
        "address",
        "business-type",
        "catchment-select",
        "map",
        "kpis",
        "decision-content",
        "comparison-form",
        "comparison-result",
        "language-toggle",
        "theme-toggle",
        "exports",
    }.issubset(parser.ids)


def test_every_static_interface_string_has_chinese_and_english_copy() -> None:
    parser = _InterfaceParser()
    parser.feed(_read(HTML_PATH))
    javascript = _read(JS_PATH)

    missing = [key for key in sorted(parser.translation_keys) if javascript.count(f'"{key}"') < 2]
    assert missing == []
    assert 'nyc-lens-language' in javascript
    assert 'document.documentElement.lang' in javascript
    assert '<html lang="en"' in _read(HTML_PATH)
    assert 'safeStorageGet("nyc-lens-language") || "en"' in javascript


def test_ui_supports_dark_theme_and_responsive_reflow() -> None:
    html = _read(HTML_PATH)
    css = _read(CSS_PATH)
    javascript = _read(JS_PATH)

    assert 'data-theme="light"' in html
    assert '<details class="filter-panel panel-surface" open>' in html
    assert 'html[data-theme="dark"]' in css
    assert '.filter-panel > .filter-content { display:block !important; }' in css
    assert '.filter-panel { position:static; align-self:start; order:1; }' in css
    assert re.search(r"@media\s*\(max-width:\s*620px\)", css)
    assert 'nyc-lens-theme' in javascript
    assert 'window.matchMedia("(min-width: 1351px)")' in javascript
    assert '.dashboard-shell { grid-template-columns:minmax(0,1fr); }' in css
    assert 'response.text()' in javascript
    assert 'response.json()' not in javascript
