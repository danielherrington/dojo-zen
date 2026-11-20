"""DAN-404: the portal must never turn ClassDojo or LLM text into live HTML, and HTML pages carry a CSP."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

INDEX = Path(__file__).resolve().parents[1] / "src" / "dojo" / "web" / "index.html"
PAYLOADS = [
    "<script>alert(1)</script>",
    "<img src=x onerror=alert(1)>",
    "**<svg onload=alert(1)>**",
    "### <iframe src=javascript:alert(1)>",
    "- \"><a href=javascript:alert(1)>x</a>",
]


def _js_function(name: str) -> str:
    """Pull one top-level `function name(...) {...}` out of the portal's inline script."""
    src = INDEX.read_text(encoding="utf-8")
    start = src.index(f"function {name}(")
    depth, i = 0, src.index("{", start)
    while True:
        depth += {"{": 1, "}": -1}.get(src[i], 0)
        i += 1
        if depth == 0:
            return src[start:i]


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_format_markdown_escapes_injected_html():
    script = "\n".join([
        _js_function("escapeHtml"), _js_function("formatMarkdown"),
        f"process.stdout.write(JSON.stringify({json.dumps(PAYLOADS)}.map(formatMarkdown)));",
    ])
    out = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
    for rendered in out:
        tags = set(re.findall(r"<\s*([a-zA-Z0-9]+)", rendered))
        assert tags <= {"strong", "em", "h3", "h4", "li", "ul", "br"}, rendered
    assert "&lt;script&gt;" in out[0]
    assert out[2].startswith("<strong>&lt;svg")  # markdown still works around escaped text


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_format_markdown_keeps_formatting():
    script = "\n".join([
        _js_function("escapeHtml"), _js_function("formatMarkdown"),
        "process.stdout.write(formatMarkdown('### Due\\n**Book report** is *Friday*'));",
    ])
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout
    assert "<h4" in out and "<strong>Book report</strong>" in out and "<em>Friday</em>" in out


def test_answer_and_error_text_go_through_the_escaper():
    src = INDEX.read_text(encoding="utf-8")
    assert "escapeHtml(String(text))" in _js_function("formatMarkdown")
    # innerHTML sinks only (showToast uses innerText, so plain text is fine there).
    assert not re.search(r"innerHTML = `[^`]*\$\{(err|e)\.message\}", src)
    assert "${it.id}" not in src


def test_html_pages_carry_security_headers():
    from dojo.server import app

    client = TestClient(app)
    for path in ("/", "/login"):
        res = client.get(path, follow_redirects=False)
        if res.headers.get("content-type", "").startswith("text/html"):
            csp = res.headers["content-security-policy"]
            for directive in ("connect-src 'self'", "object-src 'none'", "base-uri 'none'", "frame-ancestors 'none'"):
                assert directive in csp
            assert res.headers["x-content-type-options"] == "nosniff"
    assert "content-security-policy" not in client.get("/api/status").headers  # JSON untouched
