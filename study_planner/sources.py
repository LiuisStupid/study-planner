"""实时抓取 arXiv 与 GitHub，作为课程表之外的新鲜度补充。

设计原则（很重要）：
- **只用标准库**（urllib + xml.etree），不引入 requests。少一个依赖少一个坑。
- **绝不抛异常**。断网、限流、接口改版都只返回空列表 + 错误信息，
  让上层退化成「纯课程计划」继续可用。计划生成不能被网络绑架。
- **当日缓存**。结果按日期落盘，同一天内反复刷新页面不会重复请求。
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from typing import Any, Optional

from . import config

ATOM_NS = {"a": "http://www.w3.org/2005/Atom"}

# 伪装一下 UA。arXiv 和 GitHub 都会拒绝空 UA 的请求。
USER_AGENT = "study-planner/0.1 (+https://arxiv.org)"


# ---------------------------------------------------------------------------
# 底层 HTTP
# ---------------------------------------------------------------------------
def _get(url: str, headers: Optional[dict[str, str]] = None) -> Optional[bytes]:
    """发一个 GET。失败返回 None，绝不抛异常。"""
    hdrs = {"User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)

    last_err: Optional[str] = None
    for attempt in range(config.HTTP_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=config.HTTP_TIMEOUT) as resp:
                return resp.read()
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as e:
            last_err = str(e)
            if attempt < config.HTTP_RETRIES:
                time.sleep(1.5 * (attempt + 1))  # 简单退避
    _warn(f"抓取失败 {url}：{last_err}")
    return None


def _warn(msg: str) -> None:
    """抓取失败的提示走 stderr，不污染 CLI 的正常输出。"""
    import sys

    print(f"[sources] {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# 缓存
# ---------------------------------------------------------------------------
def _cache_path(name: str, day: date) -> Path:
    return config.CACHE_DIR / f"{name}-{day.isoformat()}.json"


def _read_cache(name: str, day: date) -> Optional[Any]:
    path = _cache_path(name, day)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_cache(name: str, day: date, data: Any) -> None:
    path = _cache_path(name, day)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass  # 缓存写不了不影响主流程


def clear_cache(day: Optional[date] = None) -> int:
    """清缓存。给定 day 就只清那天的。返回删掉的文件数。"""
    if not config.CACHE_DIR.exists():
        return 0
    pattern = f"*-{day.isoformat()}.json" if day else "*.json"
    count = 0
    for p in config.CACHE_DIR.glob(pattern):
        try:
            p.unlink()
            count += 1
        except OSError:
            pass
    return count


# ---------------------------------------------------------------------------
# arXiv
# ---------------------------------------------------------------------------
def _score(text: str) -> int:
    """按关键词命中数打分，标题权重更高（调用方已经区分了 title/summary）。"""
    low = text.lower()
    return sum(1 for kw in config.ARXIV_KEYWORDS if kw in low)


def _parse_arxiv(xml_bytes: bytes) -> list[dict]:
    """解析 arXiv Atom 响应。"""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []

    out: list[dict] = []
    for entry in root.findall("a:entry", ATOM_NS):
        title_el = entry.find("a:title", ATOM_NS)
        summary_el = entry.find("a:summary", ATOM_NS)
        id_el = entry.find("a:id", ATOM_NS)
        published_el = entry.find("a:published", ATOM_NS)
        if title_el is None or id_el is None:
            continue

        title = _clean(title_el.text or "")
        summary = _clean(summary_el.text or "") if summary_el is not None else ""
        link = (id_el.text or "").strip()

        score = _score(title) * 2 + _score(summary)
        if score <= 0:
            continue  # 和你的方向无关，不要浪费注意力

        authors = [
            (a.find("a:name", ATOM_NS).text or "").strip()
            for a in entry.findall("a:author", ATOM_NS)
            if a.find("a:name", ATOM_NS) is not None
        ]

        out.append({
            "id": "arxiv-" + link.rsplit("/", 1)[-1],
            "title": title,
            # arXiv 接口返回的是 http，统一升到 https
            "url": link.replace("http://", "https://", 1),
            "summary": summary[:280],
            "authors": authors[:3],
            "published": (published_el.text or "")[:10] if published_el is not None else "",
            "score": score,
            "source": "arXiv",
        })

    # 两级排序：先按发布时间倒序（新的在前），再按相关度倒序。
    # 分两步是因为第二次排序是稳定的，能保留第一次的顺序作为次级键。
    out.sort(key=lambda d: d.get("published", ""), reverse=True)
    out.sort(key=lambda d: d["score"], reverse=True)
    return out


def _clean(s: str) -> str:
    """arXiv 的标题和摘要里有换行和多余空格，压平。"""
    return re.sub(r"\s+", " ", s).strip()


def fetch_arxiv(day: date, use_cache: bool = True) -> tuple[list[dict], Optional[str]]:
    """抓 arXiv 最新论文。返回 (条目列表, 错误信息)。"""
    if use_cache:
        cached = _read_cache("arxiv", day)
        if cached is not None:
            return cached, None

    cats = "+OR+".join(f"cat:{c}" for c in config.ARXIV_CATEGORIES)
    url = (
        "https://export.arxiv.org/api/query"
        f"?search_query={cats}"
        "&sortBy=submittedDate&sortOrder=descending"
        f"&start=0&max_results={config.ARXIV_FETCH_LIMIT}"
    )

    raw = _get(url)
    if raw is None:
        # 抓不到就用过期缓存兜底（有总比没有好），并如实报告
        return [], "arXiv 抓取失败（网络不通或接口限流）"

    items = _parse_arxiv(raw)
    _write_cache("arxiv", day, items)
    return items, None


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------
def fetch_github(day: date, use_cache: bool = True) -> tuple[list[dict], Optional[str]]:
    """搜 GitHub 上相关的热门仓库。返回 (条目列表, 错误信息)。"""
    if use_cache:
        cached = _read_cache("github", day)
        if cached is not None:
            return cached, None

    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    out: list[dict] = []
    error: Optional[str] = None

    for query in config.GITHUB_QUERIES:
        q = urllib.parse.quote(f"{query} stars:>30")
        url = (
            "https://api.github.com/search/repositories"
            f"?q={q}&sort=stars&order=desc&per_page=5"
        )
        raw = _get(url, headers=headers)
        if raw is None:
            error = "GitHub 抓取失败（网络不通或触发限流）"
            continue
        try:
            data = json.loads(raw)
        except ValueError:
            error = "GitHub 返回了无法解析的内容"
            continue

        for repo in (data.get("items") or [])[:5]:
            full_name = repo.get("full_name") or ""
            if not full_name:
                continue
            out.append({
                "id": "gh-" + full_name.replace("/", "-").lower(),
                "title": full_name,
                "url": repo.get("html_url") or "",
                "summary": (repo.get("description") or "").strip()[:280],
                "stars": repo.get("stargazers_count", 0),
                "source": "GitHub",
            })

    # 同一仓库可能被多个查询命中，去重
    seen: set[str] = set()
    deduped = []
    for r in sorted(out, key=lambda d: -d.get("stars", 0)):
        if r["id"] in seen:
            continue
        seen.add(r["id"])
        deduped.append(r)

    if deduped:
        _write_cache("github", day, deduped)
    return deduped, error


# ---------------------------------------------------------------------------
# 合并入口
# ---------------------------------------------------------------------------
def fetch_fresh(day: date, limit: int, use_cache: bool = True) -> dict:
    """取今天的「新鲜事」，给每日计划用。

    返回 {"items": [...], "errors": [...], "from_network": bool}。
    任何情况下都不抛异常；出错时 items 为空、errors 有值，上层照常出计划。
    """
    arxiv_items, arxiv_err = fetch_arxiv(day, use_cache=use_cache)
    gh_items, gh_err = fetch_github(day, use_cache=use_cache)

    errors = [e for e in (arxiv_err, gh_err) if e]

    # arXiv 论文和 GitHub 仓库各占一半名额，保证两类都露面
    half = max(1, limit // 2)
    picked = arxiv_items[:half] + gh_items[:limit - half]
    picked = picked[:limit]

    for it in picked:
        it["fresh"] = True

    return {
        "items": picked,
        "errors": errors,
        "from_network": not errors,
    }
