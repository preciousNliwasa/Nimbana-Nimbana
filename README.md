# USA.gov Search CLI

A small Python client for searching [USA.gov](https://www.usa.gov/) from the
terminal. It supports the English and Spanish search editions, pagination,
JSON/CSV output, and polite rate limiting.

## Why Playwright?

USA.gov's search results are served by `search.usa.gov`, which sits behind an
AWS WAF JavaScript challenge. Plain HTTP clients (`requests`, `curl`, `urllib`)
receive `HTTP 202` with an empty body. This script launches a real Chromium
browser through [Playwright](https://playwright.dev/python/) so the challenge
can complete, then parses the rendered results.

## Requirements

- Python 3.9+
- Playwright for Python
- Chromium (installed by Playwright)

## Setup

```bash
py -m pip install playwright
py -m playwright install chromium
```

## Quick start

```bash
cd path\usa-gov

py usa_gov_search.py "passport"
py usa_gov_search.py "student loans" --limit 5
py usa_gov_search.py "impuestos" --lang es
```

## Usage

```text
py usa_gov_search.py QUERY [options]
```

`QUERY` may be one or more words; quoting is optional.

### Examples

```bash
# English search, default 20 results
py usa_gov_search.py "passport"

# Limit results
py usa_gov_search.py "passport" --limit 5

# Spanish edition
py usa_gov_search.py "pasaporte" --lang es

# Start from page 2
py usa_gov_search.py "taxes" --page 2 --limit 3

# Fetch across pages (20 results per page)
py usa_gov_search.py "passport" --limit 45 --delay 1

# All available pages, capped at 5 pages
py usa_gov_search.py "passport" --limit 0 --max-pages 5

# Machine-readable output
py usa_gov_search.py "student loans" --json
py usa_gov_search.py "student loans" --csv > results.csv

# Show the browser window (useful if the WAF challenge stalls)
py usa_gov_search.py "passport" --headed
```

### Options

| Option | Default | Description |
|---|---|---|
| `--lang {en,es}` | `en` | Search the English or Spanish edition |
| `--affiliate ID` | auto | Override the `search.usa.gov` affiliate ID |
| `--page N` | `1` | First results page (1-based) |
| `--limit N` | `20` | Maximum results; `0` means all pages up to `--max-pages` |
| `--max-pages N` | `10` | Safety cap on pages fetched |
| `--timeout SEC` | `45` | Per-page navigation/result timeout |
| `--retries N` | `2` | Retries when the WAF stalls a request |
| `--delay SEC` | `1.0` | Delay between page requests |
| `--json` | off | Emit JSON |
| `--csv` | off | Emit CSV |
| `--snippet-width N` | `160` | Truncate titles/snippets in table output |
| `--headed` | off | Show the Chromium window |

`--json` and `--csv` are mutually exclusive.

## Output

### Table (default)

```text
Query: passport (en) - 31 reported, 3 returned

  1. Renew an adult passport | USAGov
     https://www.usa.gov/renew-adult-passport
     Find out if you can renew your passport or if you have to get a new one...

  2. Get a passport for a minor under 18 | USAGov
     https://www.usa.gov/child-passport
     child's passport inside or outside the U.S...
```

### JSON

```json
{
  "query": "student loans",
  "lang": "en",
  "affiliate": "usagov_en_internal",
  "total": 10,
  "returned": 2,
  "results": [
    {
      "rank": 1,
      "title": "Resolve student loan payment problems | USAGov",
      "url": "https://www.usa.gov/student-loan-problems",
      "display_url": "www.usa.gov/student-loan-problems",
      "snippet": "Contact your loan servicer if you are struggling to repay..."
    }
  ]
}
```

### CSV

```csv
rank,title,url,display_url,snippet
1,Taxes | USAGov,https://www.usa.gov/taxes,www.usa.gov/taxes,"income tax. Check the status..."
```

## How it works

1. Builds a request to:

   ```text
   https://search.usa.gov/search?affiliate=usagov_en_internal&query=...&page=N
   ```

   The Spanish affiliate is `usagov_es_internal`.

2. Loads the page in Chromium and waits for the WAF challenge to finish.
3. Extracts results from the rendered DOM:

   | Data | Selector |
   |---|---|
   | Result cards | `#results a.no-underline` |
   | Title | `.result-title` |
   | Snippet | `.result-desc p` |
   | Display URL | `.result-url-text` |
   | Total count | `.results-count` |
   | Next page | `.usa-pagination__next-page` |

4. Follows the "Next" link until `--limit` is reached or `--max-pages` is hit.
5. De-duplicates by URL and assigns sequential ranks.

Parsing is centralized in `extract_page()`; if USA.gov changes its markup,
update the selectors there.

## Notes and limitations

- **Rate limits:** `search.usa.gov` rate-limits bursts. Run one instance at a
  time; two concurrent runs caused navigation timeouts during testing. Keep
  `--delay` at 1 second or higher when paging.
- **WAF stalls:** If the challenge does not complete, retry after a minute,
  increase `--timeout`, or use `--headed`.
- **Result order** can vary slightly between runs.
- **Snippets** are returned as the site renders them, including the site's own
  odd duplicated fragments (e.g. `Learn...Learn how to renew...`).
- **Rank** is relative to the returned set, so `--page 2` starts again at 1.
- This is a convenience client for a public search interface, not an official
  USA.gov tool. Respect the site's terms and do not hammer it.

## Troubleshooting

| Error | Fix |
|---|---|
| `Playwright is required` | `py -m pip install playwright` |
| `Executable doesn't exist` | `py -m playwright install chromium` |
| `Navigation timed out` | Wait a minute, retry, or use `--headed` |
| `Timed out waiting for results` | Increase `--timeout`, use `--headed` |
| `returned its search-error page` | Retry later; `search.usa.gov` is having trouble |
| `No results found.` | The query genuinely has no matches |

## Files

| File | Purpose |
|---|---|
| `usa_gov_search.py` | CLI client |
| `README.md` | This document |
