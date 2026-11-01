# Persistent API count cache

Set a nonempty `TOOLISM_CACHE_DIR` to reuse Anthropic counts between runs:

```bash
TOOLISM_CACHE_DIR=.cache/toolism toolism measure tools.json -t anthropic:claude-sonnet-5
```

Unset or empty means persistence is disabled. The normal Anthropic package/key checks still run.
Explicitly injected clients and `ANTHROPIC_CUSTOM_HEADERS` configurations keep their existing
memory-only behavior. Clients with an unidentified/ambiguous endpoint or API-version identity
also remain uncached. This does not add Gemini support.

A complete, unexpired cache of an unchanged workload makes zero count API requests, including
the empty-request baseline, probe and aggregate tool-set requests. Totals are never reconstructed
by summing marginal counts. Changing a tool, wire key order, model, SDK, toolism version or
counting recipe causes the corresponding misses. Missing, invalid, expired or evicted entries
are counted normally; provider errors retain their existing behavior.

The directory contains one `toolism-token-counts-v1.json` snapshot with hashed identities,
integer counts, generation metadata and an integrity checksum, not tool/schema/credential plaintext.
The checksum detects accidental corruption, including changes that remain valid JSON. It is capped at
4,096 entries and 1 MiB, with oldest-write eviction and a fixed 24-hour generation lifetime.
Hits and rewrites do not renew that lifetime. Counts can be stale within it; this lifetime is a
cache policy, not a provider stability guarantee. To clear it, stop active runs, remove that
one file and create a fresh tokenizer/process. Existing instances or writers can retain or
repopulate cached entries.

Writes use a temporary sibling and atomic replacement. Concurrent writers can lose each other's
new cache hits and repeat requests; they do not merge updates or coordinate cold requests.
The size bound covers the published snapshot, not simultaneous temporary files or abandoned
crash leftovers. Cache filesystem failures are misses, and replacement is not a guarantee of
power-loss durability. Use a trusted cache directory: digests identify requests, but do not
authenticate stored numeric values: someone controlling the cache can recalculate its checksum.

For GitHub Actions, use a new save key for refreshed snapshots because cache archives are
immutable. For example, in a workflow that already installs its locked toolism dependencies:

```yaml
env:
  TOOLISM_CACHE_DIR: .cache/toolism-counts
  TOOLISM_CACHE_COMPAT: v1-anthropic-claude-sonnet-5-toolism-0.2.0
steps:
  - uses: actions/checkout@v7
  - uses: actions/cache@v6
    with:
      path: ${{ env.TOOLISM_CACHE_DIR }}
      key: counts-${{ runner.os }}-${{ env.TOOLISM_CACHE_COMPAT }}-${{ hashFiles('uv.lock') }}-${{ github.run_id }}-${{ github.run_attempt }}
      restore-keys: |
        counts-${{ runner.os }}-${{ env.TOOLISM_CACHE_COMPAT }}-${{ hashFiles('uv.lock') }}-
```

Internal identities still validate restored records. Cross-PR reuse needs a trusted
base/default-branch cache producer; merge-ref caches are restricted to that PR's reruns.
Fork PRs may read base caches, so never put secrets in cache paths/keys/files or expose provider
credentials to untrusted jobs. This cache is distinct from provider prompt caching.
