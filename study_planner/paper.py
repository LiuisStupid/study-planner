"""论文 / 仓库正文的抓取与解析，供阅读器使用。

左边要原文、右边的 AI 要上下文，这个模块负责把两者都准备好。

三条实测得出的硬约束（改这个文件前先读一遍，否则很容易踩回去）：

1. `arxiv.org/abs/<id>` 带 `frame-ancestors 'none'`，**不能**嵌 iframe。
   而 `arxiv.org/html/<id>` 没有任何防嵌入头，能嵌，而且 2015 年的老论文也有。
   所以左栏一律用 `/html/`，PDF 只作为备选视图。
2. `github.com` 和 `raw.githubusercontent.com` 都是 `x-frame-options: deny`，不能嵌。
   但**服务端抓取不受这个限制**，所以 README 走服务端拿下来自己渲染。
3. arXiv 的 HTML 版正文包在唯一的 `<article>` 里，章节标题是带编号的
   `<h2>` / `<h3>`（形如「3.1  Preliminaries」）。只收 article 里面的内容，
   否则导航栏、页脚的「Report issue」之类会被当成论文正文喂给模型。

论文内容不会变，所以这里的缓存是长期有效的——不同于 `sources.py` 那种按天过期的。
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from html.parser import HTMLParser

from . import config, sources
from .knowledge import ITEM_BY_ID, Item

# 从条目 URL 里认出 arXiv id
_ARXIV_RE = re.compile(r"arxiv\.org/(?:abs|pdf|html)/(\d{4}\.\d{4,5})")
# 从条目 URL 里认出 GitHub 仓库
_GITHUB_RE = re.compile(r"github\.com/([^/\s]+)/([^/#?\s]+)")

# README 可能叫这些名字，按顺序试
_README_NAMES = ("README.md", "README.rst", "README.txt", "README", "readme.md")


# ---------------------------------------------------------------------------
# HTML 解析
# ---------------------------------------------------------------------------
class _PaperParser(HTMLParser):
    """从 arXiv 的 HTML 版里抽出正文纯文本和章节标题。

    `inside_only=True` 时只收 <article> 内部的内容。这个开关由调用方
    先扫一遍原始 HTML 决定——不能等解析到 <article> 才开始过滤，
    那样开头那一堆导航内容已经被收进去了。

    整页没有 <article> 时（arXiv 万一改版）退化成收全文：
    宁可带点噪声，也好过什么都拿不到。

    **静音状态用显式栈维护，不能用计数器**。原因是 `handle_endtag` 只拿得到
    标签名、拿不到 class，没法判断这个 `</span>` 关的是不是我们想屏蔽的那个
    `ltx_note`——用计数器迟早会算错，实测会把整个页脚都收进摘要里。
    """

    _VOID = {"br", "img", "hr", "meta", "link", "input", "source", "col", "wbr", "area"}
    _SKIP_TAGS = {"script", "style", "noscript", "svg", "nav", "footer"}
    _SKIP_CLASSES = ("ltx_note",)   # 作者单位、通讯邮箱这类脚注
    _BLOCK = {"p", "div", "section", "li", "tr", "td", "th", "figcaption", "blockquote"}

    def __init__(self, inside_only: bool = True) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.headings: list[str] = []
        self._inside_only = inside_only
        # 每个未闭合元素一格：(是否 <article>, 是否静音)。静音会向下传递。
        self._stack: list[tuple[bool, bool]] = []
        self._article_depth = 0
        self._heading_tag: str | None = None
        self._heading_buf: list[str] = []

    def _muted(self) -> bool:
        return self._stack[-1][1] if self._stack else False

    def _collecting(self) -> bool:
        if self._muted():
            return False
        return self._article_depth > 0 if self._inside_only else True

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self._VOID:
            return

        cls = dict(attrs).get("class") or ""
        inherited = self._muted()
        muted = inherited or tag in self._SKIP_TAGS or any(k in cls for k in self._SKIP_CLASSES)
        is_article = tag == "article"

        self._stack.append((is_article, muted))
        if is_article:
            self._article_depth += 1

        if not self._collecting():
            return
        if tag in ("h2", "h3"):
            self._heading_tag = tag
            self._heading_buf = []
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._VOID or not self._stack:
            return
        is_article, _ = self._stack.pop()
        if is_article and self._article_depth:
            self._article_depth -= 1
            return
        if not self._collecting():
            return
        if tag in ("h2", "h3"):
            if self._heading_tag == tag:
                text = _squash("".join(self._heading_buf))
                if text:
                    self.headings.append(text)
                self._heading_tag = None
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._collecting():
            return
        if self._heading_tag:
            self._heading_buf.append(data)
        self.parts.append(data)

    def full_text(self) -> str:
        return _squash_lines("".join(self.parts))


def _squash(s: str) -> str:
    """把任意空白压成单个空格。"""
    return re.sub(r"\s+", " ", s).strip()


def _squash_lines(s: str) -> str:
    """压掉行内多余空格和连续空行，但保留段落结构。"""
    s = re.sub(r"[ \t ]+", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _parse_paper_html(html: str) -> tuple[str, list[str]]:
    """返回 (正文纯文本, 章节标题列表)。"""
    # 先扫一遍决定过滤模式：有 <article> 就只收它内部的内容
    parser = _PaperParser(inside_only="<article" in html.lower())
    try:
        parser.feed(html)
    except Exception:
        # HTMLParser 对畸形 HTML 偶尔会炸，能收多少算多少
        pass
    return parser.full_text(), parser.headings


def _abstract_from_text(full_text: str, sections: list[str]) -> str:
    """从正文里切出摘要：「Abstract」之后、第一个章节标题之前。

    比在 HTML 上定位 div 稳得多——`full_text` 已经是 article 范围内的可靠结果，
    而第一个章节标题就是现成的天然边界，不用再猜嵌套层级。
    """
    i = full_text.find("Abstract")
    if i < 0:
        return ""

    rest = full_text[i + len("Abstract"):]
    end = len(rest)
    if sections:
        j = rest.find(sections[0])
        if 0 < j < end:
            end = j

    text = _squash(rest[:end])
    # 太短说明切错了地方，宁可留空也不要塞一段莫名其妙的东西给模型
    return text[:4000] if len(text) > 40 else ""


def _extract_title(html: str) -> str:
    """从 HTML 里取论文真实标题。

    只用 `<title>`。`<h1 class="ltx_title_document">` 看着更「语义」，但实测
    那个容器里紧跟着全部作者和单位，切出来是 300 字的作者名单——不如不用。
    `<title>` 是单个自洽元素，没有嵌套问题。

    取不到就返回空串，调用方会保留课程表里的中文标题（那个也是可读的）。
    """
    m = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
    if not m:
        return ""

    title = _squash(m.group(1).split("\n")[0])
    # 去掉 arXiv 偶尔会加的后缀
    title = re.sub(r"\s*arXiv:\d{4}\.\d{4,5}.*$", "", title).strip()
    return title if 10 <= len(title) <= 300 else ""


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass
class Doc:
    """一份材料的全部信息。"""

    item_id: str
    kind: str = "other"          # "arxiv" | "github" | "other"
    title: str = ""
    url: str = ""                # 条目原本的链接
    embed_url: str = ""          # 左栏 iframe 地址；github/other 为空
    pdf_url: str = ""            # 备选视图
    abstract: str = ""
    sections: list[str] = field(default_factory=list)
    full_text: str = ""          # 懒加载，默认空
    full_truncated: bool = False
    readme_html: str = ""        # GitHub README 渲染结果
    error: str = ""
    fetched_at: str = ""

    def to_dict(self, with_full: bool = True) -> dict:
        d = asdict(self)
        if not with_full:
            d.pop("full_text", None)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Doc":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


# ---------------------------------------------------------------------------
# 各来源的抓取
# ---------------------------------------------------------------------------
def arxiv_id_of(item: Item) -> str:
    """从条目的 URL 里取出 arXiv id，取不到返回空串。"""
    m = _ARXIV_RE.search(item.url)
    return m.group(1) if m else ""


def _fetch_arxiv_meta(arxiv_id: str) -> tuple[str, str]:
    """取标题和摘要。返回 (标题, 摘要)。"""
    url = f"https://export.arxiv.org/api/query?id_list={arxiv_id}"
    raw = sources._get(url)
    if raw is None:
        return "", ""

    try:
        import xml.etree.ElementTree as ET

        root = ET.fromstring(raw)
    except Exception:
        return "", ""

    for entry in root.findall("a:entry", sources.ATOM_NS):
        title_el = entry.find("a:title", sources.ATOM_NS)
        summary_el = entry.find("a:summary", sources.ATOM_NS)
        title = _squash(title_el.text or "") if title_el is not None else ""
        abstract = _squash(summary_el.text or "") if summary_el is not None else ""
        return title, abstract
    return "", ""


def _fetch_arxiv_html(arxiv_id: str) -> str:
    """取 arXiv 的 HTML 版正文。没有 HTML 版时返回空串。"""
    raw = sources._get(f"https://arxiv.org/html/{arxiv_id}")
    if raw is None:
        return ""
    return raw.decode("utf-8", "ignore")


def _github_parts(item: Item) -> tuple[str, str]:
    """从条目 URL 里取出 (owner, repo)。"""
    m = _GITHUB_RE.search(item.url)
    if not m:
        return "", ""
    return m.group(1), m.group(2).removesuffix(".git")


def _fetch_readme(owner: str, repo: str) -> str:
    """抓 README 的原始文本。按常见文件名依次尝试。"""
    for name in _README_NAMES:
        url = f"https://raw.githubusercontent.com/{owner}/{repo}/HEAD/{name}"
        raw = sources._get(url)
        if raw is not None and raw.strip():
            return raw.decode("utf-8", "ignore")
    return ""


# ---------------------------------------------------------------------------
# 缓存
# ---------------------------------------------------------------------------
def _cache_path(item_id: str):
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", item_id)
    return config.DOC_CACHE_DIR / f"{safe}.json"


def _read_cache(item_id: str) -> Doc | None:
    path = _cache_path(item_id)
    if not path.exists():
        return None
    try:
        return Doc.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def _write_cache(doc: Doc) -> None:
    path = _cache_path(doc.item_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(doc.to_dict(), ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        pass  # 写不了缓存不影响正常使用


# ---------------------------------------------------------------------------
# 总入口
# ---------------------------------------------------------------------------
def load(item_id: str, use_cache: bool = True) -> Doc:
    """取一份材料的全部信息。失败不抛异常，错误放在 Doc.error 里。

    首次会抓一次网络（arXiv 那页约 400KB），之后走磁盘缓存。
    """
    item = ITEM_BY_ID.get(item_id)
    if item is None:
        return Doc(item_id=item_id, error=f"课程表里没有这个条目：{item_id}")

    doc = _read_cache(item_id) if use_cache else None
    if doc is None:
        doc = Doc(
            item_id=item_id,
            title=item.title,
            url=item.url,
            fetched_at=date.today().isoformat(),
        )
        _fill(doc, item)
        _write_cache(doc)

    return doc


def _fill(doc: Doc, item: Item) -> None:
    """填全部信息：标题、原文地址、摘要、目录、全文。

    只发**一次**请求——arXiv 的 HTML 版里标题、摘要、章节、正文全都有。
    之前分两次（先调 arXiv API 取摘要、再抓 HTML），实测那个 API 经常超时，
    有一次光等它就花了 40 秒，而且多这一次往返完全没必要。

    全文的「懒加载」省的是 token（发给模型的成本），不是网络——剥标签是纯本地计算，
    所以这里一次性都解析好，缓存在磁盘上。
    """
    arxiv_id = arxiv_id_of(item)
    if arxiv_id:
        doc.kind = "arxiv"
        doc.embed_url = f"https://arxiv.org/html/{arxiv_id}"
        doc.pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"

        html = _fetch_arxiv_html(arxiv_id)
        if not html:
            # 万一没有 HTML 版，退回 API 至少拿到标题和摘要（问答还能用）
            doc.error = "arXiv 没有提供这篇的 HTML 版，左栏打不开；问答只能基于下面的摘要。"
            title, abstract = _fetch_arxiv_meta(arxiv_id)
            if title:
                doc.title = title
            doc.abstract = abstract
            return

        title = _extract_title(html)
        if title:
            doc.title = title

        text, headings = _parse_paper_html(html)
        doc.sections = headings
        doc.abstract = _abstract_from_text(text, headings)

        limit = config.FULLTEXT_MAX_CHARS
        if len(text) > limit:
            doc.full_text = text[:limit]
            doc.full_truncated = True
        else:
            doc.full_text = text
        return

    owner, repo = _github_parts(item)
    if owner and repo:
        doc.kind = "github"
        readme = _fetch_readme(owner, repo)
        if readme:
            from . import render  # 延迟导入，避免循环依赖

            doc.readme_html = render.md_to_html(readme)
            doc.abstract = _squash(readme)[:2000]

            limit = config.FULLTEXT_MAX_CHARS
            if len(readme) > limit:
                doc.full_text = readme[:limit]
                doc.full_truncated = True
            else:
                doc.full_text = readme
        else:
            doc.error = "没能抓到 README，可以直接在新标签页打开仓库。"
        return

    doc.kind = "other"
    doc.error = "这类资源不支持内嵌，直接在新标签页打开吧。"


def clear_cache() -> int:
    """清掉全部文档缓存，返回删掉的文件数。"""
    if not config.DOC_CACHE_DIR.exists():
        return 0
    count = 0
    for p in config.DOC_CACHE_DIR.glob("*.json"):
        try:
            p.unlink()
            count += 1
        except OSError:
            pass
    return count
