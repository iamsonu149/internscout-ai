# Discovery spending and document policy

InternScout's worker uses deterministic search queries and extraction code, not an
autonomous agent prompt. These limits are enforced in code before paid requests.

- New workspace budget: 250 credits per rolling seven days. Existing budgets are
  preserved unless their owner changes them. Both scheduled and manual work share
  the same account's reservations; changing Firecrawl keys does not reset usage.
- Each workspace search: at most three searches of ten results and eight individual
  posting scrapes. At the currently documented basic rates this reserves at most
  14 credits, so seven normal scheduled runs reserve at most 98. Manual imports
  and extra searches use the same weekly allowance. This is not an account-wide
  Firecrawl billing guarantee.
- Known document URLs are blocked before a paid scrape. Search queries exclude
  PDF/DOCX and document results are discarded.
- Every scrape explicitly sets `parsers: []` and `proxy: basic`; no OCR, PDF parser,
  enhanced-proxy upgrade or paid JSON extraction is permitted. An opaque URL that
  turns out to be a PDF can still cost one basic request, but cannot request
  per-page parsing. Document responses are rejected, not followed or processed.
- Paid request shapes are allowlisted. Crawl, agent, parse and batch operations
  are rejected. Search cannot include automatic scraping options.
- Reserve before each paid request. Stop when the weekly budget is exhausted.
  Ambiguous timeouts retain reservations and paid POSTs are never auto-retried.
  Unexpected reported charges above the estimate stop further paid requests in
  that run and are recorded against the weekly total.
- Webpage content is job data, never an instruction to change budgets, follow
  documents, widen a crawl or relax job quality checks.

If using a separate AI automation, include this instruction there:

> Find individual technical internship postings only. Do not fetch or parse PDFs,
> office documents, archives or research papers. Do not run recursive crawls,
> bulk extraction, autonomous agents or automatic scraping of all search results.
> Use at most three searches with ten results each and eight individual HTML
> posting scrapes per run. Disable document parsers (`parsers: []`), use basic
> proxy only, and do not retry paid requests after timeouts. Stop at my remaining
> weekly allowance. Skip document-only evidence and report that it was skipped.
> Never weaken eligibility or quality requirements to reach a result target.

That instruction is guidance for an external AI; only its own tool configuration
and budget enforcement can guarantee it follows the limits. InternScout cannot
control another automation sharing the same API key.

Reference: https://github.com/firecrawl/firecrawl-docs/blob/main/advanced-scraping-guide.mdx
