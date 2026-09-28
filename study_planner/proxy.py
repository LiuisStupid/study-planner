"""把 arXiv 的 HTML 版改写成「能同源嵌入」的版本，供阅读器左栏 iframe 使用。

## 为什么非要代理不可

左栏原来直接嵌 `arxiv.org/html/<id>`，那是**跨域**的。跨域 iframe 的
`contentDocument`、`getSelection()` 一律读不到，也注入不了任何脚本。
所以「鼠标选中哪一段、哪一段就被识别」这个功能，唯一的实现路径是把
arXiv 的页面从我们自己的 origin 提供出去。没有别的办法，除非写浏览器插件。

顺带换来两个好处：可以隐藏 arXiv 的侧边栏和横幅做出干净的阅读栏；
以及页面缓存到本地之后，断网也能看正文。

## 三处注入，都在 <head> 开头

1. `<base href="https://arxiv.org/html/">` —— 让相对地址回指 arXiv。
   必须是 `.../html/` 而**不能**带 id：实测正文里图片写的是
   `2406.09246v3/openvla_teaser.png`，id 段已经在 src 里了，base 再带一次就是重复。
   样式表写的是 `/static/browse/...` 绝对路径，同一个 base 正好也解析对。

2. 一段 `<style>`：隐藏导航栏、公告横幅、页脚、水印，限制阅读栏宽度。
   每条规则都要 `!important`——我们的 style 在 head 顶部，arXiv 的 `<link>`
   在它后面，同优先级下后加载的 arXiv 赢，不加 `!important` 侧边栏根本藏不住。

3. 一段带 nonce 的 `<script>`：把选中的文字 postMessage 给父页面，
   外加一个**必需的**点击拦截器（见下）。

## <base> 会打断 425 个文内跳转，所以点击拦截器不是可选项

`href="#S3"` 这类纯片段地址是相对 **base URL** 解析的，不是相对文档地址。
注入 base 之后，正文里点一下引用 `[1]`：

- 不会滚到参考文献，而是**把 iframe 导航去 `https://arxiv.org/html/#bib1`**；
- 一旦导航走了，注入的脚本就没了，而且新文档是跨域的——划词功能就此静默失效，
  直到用户手动刷新。

所以 base 和点击拦截器必须成对出现。实测这篇论文里有 425 个这种锚点，
不是罕见情况。

## 安全边界是 CSP，不是自己写 HTML 清洗器

`_CSP` 里 `script-src 'nonce-…'` 一挡，arXiv 自己的 3 个 `<script>`、2 个 `on*=`
处理器、3 个 `javascript:` 地址全部失效，**不需要正则去删 `<script>`**。
手写清洗器正则误伤正文里的 `<` 反而是新 bug 的来源。

三个不能想当然的地方，改 `_CSP` 前先读一遍：

- **`base-uri https://arxiv.org` 是必需的。** `base-uri` 缺省时回落到
  `default-src`，而我们是 `default-src 'none'`——那样注入的 `<base>` 会被浏览器
  直接拦掉，所有图片和样式表都按 `127.0.0.1:8766` 去解析，全 404。写 `'self'` 也一样拦。
- **`style-src` 不能加 nonce。** CSP3 里同一指令出现 nonce/hash 会让 `'unsafe-inline'`
  失效，而 arXiv 有 217 个 `style="…"` 属性和 1 个内联 `<style>`，加 nonce 等于全禁掉。
- **不加 `sandbox`。** 会让 origin 变成 opaque，`event.origin` 变成 `"null"`，
  父子之间的来源校验就没法做了；而 nonce 已经把脚本管住了。
"""
from __future__ import annotations

import re
import secrets

from . import config, paper, sources

# arXiv 的 HTML 版正文的根。常量定义在 paper.py 里，这里复用同一个。
_BASE_HREF = paper.ARXIV_HTML_BASE
# base-uri 只写到源为止：带路径的 host-source 走的是路径前缀匹配，容易写错，
# 而且本来就该放行 arxiv.org 全站（图片和样式表都在别的路径下）。
_BASE_ORIGIN = _BASE_HREF.rsplit("/html/", 1)[0]

# 缓存格式版本。**改 _BRIDGE_JS / _HIDE_CSS / _BASE_HREF 之后必须 +1**，
# 否则你会从磁盘读到旧脚本，在浏览器里刷新半天看不到任何变化——
# 这是这块最浪费时间的一个坑，所以单独写一行提醒。
_PROXY_VERSION = 1
_MARK = f"<!--proxy v{_PROXY_VERSION}-->"

_BASE_TAG = f'<base href="{_BASE_HREF}">'

# 隐藏 arXiv 的站内家具，留出一条干净的阅读栏。
# 选择器来自实测的页面结构（2026-09 的 arxiv-html-papers 模板）。
# .ltx_page_main 在 ar5iv 里是 `width:100%` 的普通块，不是 grid，
# 所以把导航栏 display:none 掉之后内容会自然占满，不用额外改布局。
_HIDE_CSS = """<style>
.ds-announcement, .arxiv-html-header, .ltx_page_navbar, #infobox, #watermark-tr,
.arxiv-html-footer, .ltx_page_logo, .keyboard-glossary, .ds-site-footer,
#fixed-buttons-container, .ltx_rdf { display: none !important; }
html, body { background: #fff !important; }
.ltx_page_main { width: 100% !important; }
.ltx_page_content { max-width: 820px; margin-left: auto !important;
  margin-right: auto !important; padding: 0 26px 80px !important; }
::selection { background: #cfe6f3; }
</style>"""

# 子文档侧的桥接脚本。要点见模块 docstring 和下面各处的注释。
# 占位符 @@QUOTE_MAX@@ 在 serve() 时替换，所以调整上限不用 bump 缓存版本。
# 注意：这个字符串里**不能**出现 </script>，否则会提前闭合脚本标签。
_BRIDGE_JS = r"""
(function () {
  'use strict';
  var MAX = @@QUOTE_MAX@@;

  function squash(s) { return (s || '').replace(/\s+/g, ' ').trim(); }

  /* 章节标题：取「文档顺序里最后一个位于选区之前的 h1–h6」。
     LaTeXML 的 <section class="ltx_section"><h2> 和 render.md_to_html() 输出的
     扁平 <h3> 两套结构靠同一招通吃，不用为它们各写一套。
     LaTeXML 的标题里带 <span class="ltx_tag">3.1 </span>，textContent 正好给出
     「3.1 Preliminaries」。 */
  function headingBefore(node) {
    if (!node) return '';
    var hs = document.querySelectorAll('h1,h2,h3,h4,h5,h6');
    var best = '';
    for (var i = 0; i < hs.length; i++) {
      if (hs[i].contains(node)) return squash(hs[i].textContent);
      var pos = hs[i].compareDocumentPosition(node);
      if (pos & Node.DOCUMENT_POSITION_FOLLOWING) best = squash(hs[i].textContent);
      else if (pos & Node.DOCUMENT_POSITION_PRECEDING) break;  // 后面的标题只会更靠后
    }
    return best;
  }

  function current() {
    var sel = window.getSelection();
    if (!sel || sel.isCollapsed || !sel.rangeCount) return null;
    var range = sel.getRangeAt(0);
    var raw = range.toString();
    if (!raw || !raw.trim()) return null;

    var node = range.startContainer;
    if (node && node.nodeType === 3) node = node.parentNode;
    /* 划到导航栏、公告栏里就忽略：多半是顺手拖过去的，不是想问 */
    if (node && node.closest &&
        node.closest('.ltx_page_navbar,.ds-announcement,.arxiv-html-header,.ds-site-footer')) {
      return null;
    }

    /* 多段选区取**最后一个**矩形（鼠标松手处）。取并集包围盒的话，
       跨段落时会横跨整栏，浮标会飘到离谱的位置。 */
    var rects = range.getClientRects();
    var r = rects.length ? rects[rects.length - 1] : range.getBoundingClientRect();

    return {
      text: squash(raw).slice(0, MAX),
      truncated: raw.length > MAX,
      heading: headingBefore(node),
      rect: { top: r.top, bottom: r.bottom, left: r.left, right: r.right }
    };
  }

  function post() {
    try {
      parent.postMessage({ from: 'paper-bridge', sel: current() }, location.origin);
    } catch (e) { /* 直接被当普通页面打开时 parent 就是自己，忽略即可 */ }
  }

  function onReady() {
    document.addEventListener('mouseup', function () { setTimeout(post, 0); });
    document.addEventListener('keyup', post);          // Shift+方向键也能选
    document.addEventListener('selectionchange', function () {
      var s = window.getSelection();
      if (!s || s.isCollapsed) post();                 // 选区清空 → 收起浮标
    });
    /* iframe 内部滚动会让 rect 失效，重算一次。用 rAF 节流，滚动事件很密。 */
    var ticking = false;
    document.addEventListener('scroll', function () {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(function () { ticking = false; post(); });
    }, true);
    window.addEventListener('resize', post);

    /* 点击拦截器——必需，原因见模块 docstring：注入的 base 标签会让
       href="#S3" 这类纯片段地址按 base 解析，点一下引用就会把 iframe 导航去
       arxiv.org，注入的脚本随之消失，划词功能静默死掉。 */
    document.addEventListener('click', function (e) {
      var a = e.target && e.target.closest ? e.target.closest('a[href]') : null;
      if (!a) return;
      var href = a.getAttribute('href') || '';
      var isFragment = href.charAt(0) === '#';
      /* 带修饰键点外链 = 用户想在新标签页打开，交给浏览器自己处理 */
      var external = !isFragment && /^https?:/i.test(a.href);
      if (external && (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0)) return;

      e.preventDefault();
      if (isFragment) {
        /* 用 getElementById 而不是 querySelector：文内锚点是
           `#S2.SS0.SSS0.Px1` 这种 id，交给 CSS 选择器会被解析成
           「id=S2 且 class=SS0…」，选不中。 */
        var frag = href.slice(1);
        try { frag = decodeURIComponent(frag); } catch (err) { /* 保持原样 */ }
        var target = frag ? document.getElementById(frag) : null;
        if (target) target.scrollIntoView({ block: 'start' });
        else window.scrollTo(0, 0);
        return;
      }
      if (/^https?:/i.test(a.href)) window.open(a.href, '_blank', 'noopener');
    }, true);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', onReady);
  } else {
    onReady();
  }
})();
"""

_BRIDGE_TAG = '<script nonce="@@NONCE@@">' + _BRIDGE_JS + "</script>"

# 安全边界。为什么是这几个值，见模块 docstring 里那三条。
_CSP = (
    "default-src 'none'; "
    "script-src 'nonce-{nonce}'; "
    "style-src 'unsafe-inline' https:; "
    "img-src https: data:; "
    "font-src https: data:; "
    "connect-src 'none'; object-src 'none'; frame-src 'none'; "
    "worker-src 'none'; media-src 'none'; form-action 'none'; "
    f"base-uri {_BASE_ORIGIN}; frame-ancestors 'self'"
)


# ---------------------------------------------------------------------------
# 缓存
# ---------------------------------------------------------------------------
def _page_path(item_id: str):
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", item_id)
    return config.DOC_CACHE_DIR / "pages" / f"{safe}.html"


def _read(item_id: str) -> str | None:
    """读缓存。版本对不上就当没有，让调用方重抓。"""
    path = _page_path(item_id)
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return text if text.startswith(_MARK) else None


def _write(item_id: str, html: str) -> None:
    path = _page_path(item_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")
    except OSError:
        pass  # 写不了缓存不影响正常使用


# ---------------------------------------------------------------------------
# 改写
# ---------------------------------------------------------------------------
def _inject(html: str, payload: str) -> str:
    """把 payload 插到 <head> 紧跟其后。

    退路是插在 <body> 之前、再不行就前置到全文开头——HTML parser 会把游离的
    <base> 和 <script> 归位到 head，所以退化路径也是有效的。
    放在 head 而不是 </body> 前，是为了省掉「页面没有 </body> 怎么办」这条分支，
    也避免 arXiv 的横幅先闪一下再被 CSS 藏掉。
    """
    m = re.search(r"<head\b[^>]*>", html, re.I)
    if m:
        return html[: m.end()] + payload + html[m.end():]
    m = re.search(r"<body\b[^>]*>", html, re.I)
    if m:
        return html[: m.start()] + payload + html[m.start():]
    return payload + html


def _rewrite(html: str) -> str:
    # 页面上万一已经有 <base>（现在没有），先删掉：后出现的 base 会抢走它之后
    # 所有相对地址的解析权，我们的注入就白做了。防的是 arXiv 改版。
    html = re.sub(r"<base\b[^>]*>", "", html, count=1, flags=re.I)
    return _inject(html, _BASE_TAG + _HIDE_CSS + _BRIDGE_TAG)


# ---------------------------------------------------------------------------
# 对外
# ---------------------------------------------------------------------------
def serve(item_id: str, arxiv_id: str, force: bool = False) -> tuple[str, str] | None:
    """返回 (可以发给浏览器的 HTML, CSP 头)。拿不到论文时返回 None。

    nonce 每次响应当生成：缓存里存的是 `@@NONCE@@` 占位符，这里替换掉。
    这样缓存能长期复用，同时保证每次响应的 nonce 都不同。
    """
    html = None if force else _read(item_id)
    if html is None:
        raw = paper.fetch_arxiv_html(arxiv_id)
        if not raw:
            return None  # 失败绝不写缓存，否则一次断网就把条目永久钉成坏页
        html = _rewrite(raw)
        _write(item_id, _MARK + "\n" + html)

    nonce = secrets.token_urlsafe(24)
    body = html.replace("@@NONCE@@", nonce).replace(
        "@@QUOTE_MAX@@", str(config.QUOTE_MAX_CHARS)
    )
    return body, _CSP.format(nonce=nonce)


def fallback_page(item_id: str, title: str, pdf_url: str, url: str, reason: str) -> str:
    """论文打开时用的说明页。

    显示在左栏 iframe 内部，而不是让 iframe 去撞 arXiv 的 404 或者浏览器的
    网络错误页——那种情况下用户只会看到一片空白，不知道发生了什么。
    """
    import html as _html

    def e(s: str) -> str:
        return _html.escape(str(s or ""))

    links = []
    if pdf_url:
        links.append(f'<a class="btn" href="{e(pdf_url)}" target="_blank" rel="noopener">看 PDF ↗</a>')
    if url:
        # 标签写「原链接」而不是「arXiv」：这个兜底页对 GitHub 条目也会用到
        links.append(f'<a class="btn ghost" href="{e(url)}" target="_blank" rel="noopener">打开原链接 ↗</a>')
    links.append(f'<a class="btn ghost" href="/paper/{e(item_id)}?force=1">重新加载原文</a>')

    return f"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<style>
  body {{ margin:0; height:100vh; display:flex; align-items:center; justify-content:center;
    font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
    background:#fff; color:#25333d; }}
  .box {{ max-width:430px; text-align:center; line-height:1.8; padding:0 24px; }}
  h2 {{ font-size:15px; margin:0 0 10px; color:#2c6280; }}
  p {{ color:#7d8f9c; font-size:13.5px; margin:0 0 18px; }}
  a.btn {{ display:inline-block; margin:4px 5px 0; padding:8px 16px; border-radius:16px;
    background:#3b7ea1; color:#fff; text-decoration:none; font-size:13px; }}
  a.btn.ghost {{ background:#fff; color:#2c6280; border:1px solid #e2ebf0; }}
</style></head>
<body><div class="box">
  <h2>{e(title)}</h2>
  <p>{e(reason)}</p>
  <div>{''.join(links)}</div>
</div></body></html>"""


def clear_cache() -> int:
    """清掉全部代理页面缓存，返回删掉的文件数。"""
    pages = config.DOC_CACHE_DIR / "pages"
    if not pages.exists():
        return 0
    count = 0
    for p in pages.glob("*.html"):
        try:
            p.unlink()
            count += 1
        except OSError:
            pass
    return count
