"""
网页抓取与解析：使用 requests + readability-lxml + BeautifulSoup
函数 fetch_and_parse_webpage(url) 返回 dict:
  - title, excerpt (readability summary text), text (plain text), og_image
注意：网络请求受目标站点反爬与防护影响，适当设置超时与 UA。
"""

import requests
from readability import Document
from bs4 import BeautifulSoup
from typing import Dict, Any

HEADERS = {
    "User-Agent": "Mozilla/5.0 (XBparsing_bot/1.0; +https://example.com) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117 Safari/537.36"
}
REQUEST_TIMEOUT = 12.0


def fetch_and_parse_webpage(url: str) -> Dict[str, Any]:
    resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    content = resp.text
    doc = Document(content)
    title = doc.short_title()
    summary_html = doc.summary()
    # parse summary text
    soup = BeautifulSoup(summary_html, "lxml")
    excerpt = soup.get_text(separator="\n").strip()
    # full text fallback: clean up original content
    soup_full = BeautifulSoup(content, "lxml")
    full_text = soup_full.get_text(separator="\n")
    # try to get og:image
    og_image = None
    og = soup_full.find("meta", property="og:image")
    if og and og.get("content"):
        og_image = og.get("content")
    elif soup_full.find("meta", attrs={"name": "twitter:image"}):
        og_image = soup_full.find("meta", attrs={"name": "twitter:image"}).get("content", None)
    return {"title": title, "excerpt": excerpt, "text": full_text.strip()[:20000], "og_image": og_image}