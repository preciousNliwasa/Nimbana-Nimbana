"""Search USA.gov from the command line.

search.usa.gov sits behind an AWS WAF JavaScript challenge: plain HTTP
clients receive HTTP 202 with an empty body. This script drives Chromium
through Playwright so the challenge can complete.

Setup:
    py -m pip install playwright
    py -m playwright install chromium

Examples:
    py usa_gov_search.py "passport"
    py usa_gov_search.py "impuestos" --lang es --limit 10
    py usa_gov_search.py "student loans" --json
    py usa_gov_search.py "passport" --page 2 --limit 40

search.usa.gov rate-limits bursts, so run one instance at a time and keep
the default --delay when paging.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from urllib.parse import urlencode, urljoin

try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
except ImportError:
    print(
        "Playwright is required.\n"
        "  py -m pip install playwright\n"
        "  py -m playwright install chromium",
        file=sys.stderr,
    )
    raise SystemExit(2)

BASE_URL = "https://search.usa.gov/search"
AFFILIATES = {"en": "usagov_en_internal", "es": "usagov_es_internal"}
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
NO_RESULTS_RE = re.compile(
    r"no results found|no se encontraron resultados|no se encontró", re.I
)
COUNT_RE = re.compile(r"([\d.,]+)\s+(?:results|resultados)", re.I)
SEARCH_ERROR_RE = re.compile(r"search error|error de búsqueda", re.I)


@dataclass
class Result:
    rank: int
    title: str
    url: str
    display_url: str
    snippet: str


def build_url(query: str, affiliate: str, page: int) -> str:
    params = {"affiliate": affiliate, "query": query}
    if page > 1:
        params["page"] = str(page)
    return f"{BASE_URL}?{urlencode(params)}"


def clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def parse_total(text: str) -> int | None:
    match = COUNT_RE.search(text)
    if not match:
        return None
    digits = re.sub(r"\D", "", match.group(1))
    return int(digits) if digits else None


def goto_with_retry(page, url: str, timeout_ms: int, retries: int) -> None:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            # "commit" returns before the WAF challenge finishes; wait_for_results
            # below does the real waiting once the challenge redirect lands.
            page.goto(url, wait_until="commit", timeout=timeout_ms)
            return
        except PlaywrightTimeoutError as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(2 + attempt * 2)
    raise RuntimeError(
        "Navigation timed out. search.usa.gov may be rate-limiting this client; "
        "wait a minute and retry, or use --headed."
    ) from last_error


def wait_for_results(page, timeout_ms: int) -> None:
    page.wait_for_function(
        """() => {
            const body = document.body ? document.body.innerText : '';
            return Boolean(
                document.querySelector('#results a.no-underline')
                || document.querySelector('.results-count')
                || /no results found|no se encontraron resultados|no se encontró/i.test(body)
                || /search error|error de búsqueda/i.test(body)
            );
        }""",
        timeout=timeout_ms,
    )


def extract_page(page) -> tuple[list[Result], int | None, str | None]:
    body_text = page.inner_text("body")
    has_results = bool(page.query_selector("#results a.no-underline"))
    if SEARCH_ERROR_RE.search(body_text) and not has_results:
        raise RuntimeError(
            "search.usa.gov returned its search-error page; retry later or use --headed."
        )

    count_el = page.query_selector(".results-count")
    total = parse_total(count_el.inner_text()) if count_el else None

    next_el = page.query_selector(".usa-pagination__next-page")
    next_url = next_el.get_attribute("href") if next_el else None

    results: list[Result] = []
    for anchor in page.query_selector_all("#results a.no-underline"):
        href = anchor.get_attribute("href")
        if not href:
            continue
        title_el = anchor.query_selector(".result-title")
        snippet_el = anchor.query_selector(".result-desc p")
        display_el = anchor.query_selector(".result-url-text")
        title = clean(title_el.inner_text()) if title_el else ""
        if not title:
            continue
        results.append(
            Result(
                rank=0,
                title=title,
                url=urljoin("https://search.usa.gov", href),
                display_url=clean(display_el.inner_text()) if display_el else "",
                snippet=clean(snippet_el.inner_text()) if snippet_el else "",
            )
        )
    return results, total, next_url


def run_search(args) -> tuple[list[Result], int | None]:
    affiliate = args.affiliate or AFFILIATES[args.lang]
    timeout_ms = int(args.timeout * 1000)
    limit = args.limit if args.limit > 0 else None
    results: list[Result] = []
    seen: set[str] = set()
    total: int | None = None
    url = build_url(args.query, affiliate, args.page)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        context = browser.new_context(
            user_agent=USER_AGENT,
            locale="es-US" if args.lang == "es" else "en-US",
            viewport={"width": 1366, "height": 900},
        )
        page = context.new_page()
        page.set_default_navigation_timeout(timeout_ms)
        pages_fetched = 0
        try:
            while url and pages_fetched < args.max_pages:
                goto_with_retry(page, url, timeout_ms, args.retries)
                try:
                    wait_for_results(page, timeout_ms)
                except PlaywrightTimeoutError as exc:
                    raise RuntimeError(
                        "Timed out waiting for results. The WAF challenge may not "
                        "have completed; try --headed or increase --timeout."
                    ) from exc

                page_results, page_total, next_url = extract_page(page)
                if total is None:
                    total = page_total
                for item in page_results:
                    if item.url in seen:
                        continue
                    seen.add(item.url)
                    item.rank = len(results) + 1
                    results.append(item)
                    if limit is not None and len(results) >= limit:
                        break

                pages_fetched += 1
                if limit is not None and len(results) >= limit:
                    break
                if not next_url or not page_results:
                    break
                url = urljoin("https://search.usa.gov", next_url)
                time.sleep(args.delay)
        finally:
            context.close()
            browser.close()
    return results, total


def print_table(
    results: list[Result], total: int | None, query: str, lang: str, width: int
) -> None:
    if total is not None:
        count = f"{total} reported"
    else:
        count = "no results" if not results else "unknown total"
    print(f"Query: {query} ({lang}) - {count}, {len(results)} returned\n")
    if not results:
        print("No results found.")
        return
    for item in results:
        title = (
            item.title
            if len(item.title) <= width
            else item.title[: width - 1].rstrip() + "..."
        )
        snippet = item.snippet
        if len(snippet) > width * 2:
            snippet = snippet[: width * 2 - 3].rstrip() + "..."
        print(f"{item.rank:>3}. {title}")
        print(f"     {item.url}")
        if snippet:
            print(f"     {snippet}")
        print()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="Search USA.gov (English or Spanish) and print results.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("query", nargs="+", help="search terms")
    parser.add_argument(
        "--lang", choices=sorted(AFFILIATES), default="en", help="search edition"
    )
    parser.add_argument(
        "--affiliate", help="override the search.usa.gov affiliate ID"
    )
    parser.add_argument(
        "--page", type=int, default=1, help="first results page (1-based)"
    )
    parser.add_argument("--limit", type=int, default=20, help="max results; 0 = all")
    parser.add_argument(
        "--max-pages", type=int, default=10, help="safety cap on pages fetched"
    )
    parser.add_argument(
        "--timeout", type=float, default=45.0, help="per-page timeout in seconds"
    )
    parser.add_argument(
        "--retries", type=int, default=2, help="retries when the WAF stalls a request"
    )
    parser.add_argument(
        "--delay", type=float, default=1.0, help="delay between page requests"
    )
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true", help="emit JSON")
    output.add_argument("--csv", action="store_true", help="emit CSV")
    parser.add_argument("--snippet-width", type=int, default=160, help="table width")
    parser.add_argument("--headed", action="store_true", help="show the browser window")
    args = parser.parse_args()

    args.query = " ".join(args.query).strip()
    if not args.query:
        parser.error("query must not be empty")
    if args.page < 1:
        parser.error("--page must be >= 1")
    if args.limit < 0:
        parser.error("--limit must be >= 0")
    if args.max_pages < 1:
        parser.error("--max-pages must be >= 1")
    if args.delay < 0:
        parser.error("--delay must be >= 0")
    if args.retries < 0:
        parser.error("--retries must be >= 0")

    try:
        results, total = run_search(args)
    except Exception as exc:  # CLI boundary
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        payload = {
            "query": args.query,
            "lang": args.lang,
            "affiliate": args.affiliate or AFFILIATES[args.lang],
            "total": total,
            "returned": len(results),
            "results": [asdict(item) for item in results],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    elif args.csv:
        writer = csv.DictWriter(
            sys.stdout,
            fieldnames=["rank", "title", "url", "display_url", "snippet"],
        )
        writer.writeheader()
        for item in results:
            writer.writerow(asdict(item))
    else:
        print_table(results, total, args.query, args.lang, args.snippet_width)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
