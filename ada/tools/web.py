"""Yerel web arama ve sayfa okuma.

Claude'da bunların yerine Anthropic'in sunucu araçları (web_search / web_fetch) kullanılır;
bu modül Gemini gibi sunucu aracı olmayan sağlayıcılar içindir (bkz. agent.build_tools).
Tor açıksa (ADA_TOR, varsayılan) istekler Tor üzerinden gider; Tor yoksa doğrudan gönderilmez (bkz. tor.py).
"""

from __future__ import annotations

import httpx

from .. import tor
from . import ToolError, tool


def _proxy() -> str | None:
    try:
        return tor.proxy_url()
    except tor.TorError as e:
        raise ToolError(str(e))

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
MAX_CHARS = 40_000


@tool(
    "web_search",
    "İnternette arama yapar (DuckDuckGo). Başlık, URL ve özet döner. Ayrıntı için sonuçtaki "
    "URL'yi web_fetch ile okuyun. Güncel bilgi, haber, fiyat, şirket bilgisi vb. için kullanın.",
    {
        "query": {"type": "string"},
        "max_results": {"type": "integer", "description": "Varsayılan 8, en fazla 20"},
        "region": {"type": "string", "description": "ör. tr-tr (varsayılan), us-en, wt-wt (tümü)"},
    },
    ["query"],
)
def web_search(query: str, max_results: int = 8, region: str = "tr-tr") -> str:
    from ddgs import DDGS

    proxy = _proxy()
    try:
        results = DDGS(proxy=proxy, timeout=30 if proxy else 5).text(query, region=region, max_results=min(max(max_results, 1), 20))
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"Arama başarısız: {e}")
    if not results:
        return "Sonuç bulunamadı."
    return "\n\n".join(
        f"{i + 1}. {r.get('title', '')}\n   {r.get('href', '')}\n   {r.get('body', '')}" for i, r in enumerate(results)
    )


@tool(
    "web_fetch",
    "Bir web sayfasını indirip okunabilir metnini çıkarır (makale, dokümantasyon, haber). "
    "PDF bağlantıları da desteklenir.",
    {"url": {"type": "string"}},
    ["url"],
)
def web_fetch(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        raise ToolError("URL http:// veya https:// ile başlamalı.")
    proxy = _proxy()
    try:
        resp = httpx.get(url, headers={"User-Agent": UA}, follow_redirects=True, timeout=60 if proxy else 30, proxy=proxy)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise ToolError(f"Sayfa alınamadı: {e}")

    ctype = resp.headers.get("content-type", "")
    if "pdf" in ctype or url.lower().endswith(".pdf"):
        import io

        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(resp.content))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    elif "html" in ctype or not ctype:
        import trafilatura

        text = trafilatura.extract(resp.text, include_links=True, include_tables=True, url=str(resp.url)) or ""
        if not text.strip():
            import re

            text = re.sub(r"<[^>]+>", " ", resp.text)
            text = re.sub(r"\s+", " ", text)
    else:
        text = resp.text

    text = text.strip()
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + f"\n\n[... sayfa {len(text):,} karakter; ilk {MAX_CHARS:,} gösterildi]"
    return f"Kaynak: {resp.url}\n\n{text or '(metin çıkarılamadı)'}"
