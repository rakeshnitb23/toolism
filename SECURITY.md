# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately through
[GitHub security advisories](https://github.com/rakeshnitb23/toolism/security/advisories/new),
not in public issues. You can expect an acknowledgement within a few days and a fix or a
mitigation plan within 30 days for confirmed issues.

## Supported versions

Only the latest release receives security fixes.

## Trust model

- **Tool definition files are data.** toolism parses them as JSON and never executes or evaluates
  anything in them. Malformed or hostile input should produce an error, never code execution. If
  you find otherwise, that is a vulnerability.
- **`--mcp COMMAND` runs that command**, exactly like typing it in your shell. The server process
  inherits your environment, overlaid with anything you pass. Only point `--mcp` at servers you
  would run anyway.
- **`--mcp-config FILE` starts every stdio server listed in that file**, each the way `--mcp`
  would, with that entry's `command`, `args` and `env` added to your environment. Only point it
  at a config whose servers you would run anyway. toolism never prints `env` values: a failing
  server is reported by its name, without its command line, and any `env` or argument value of
  3 or more characters is masked if the server's error repeats it. With `TOOLISM_DEBUG=1` the
  servers' own stderr is passed through unfiltered.
- **`anthropic:<model>` tokenization** sends the tool definitions to Anthropic's `count_tokens`
  API using your `ANTHROPIC_API_KEY`. The `heuristic` and `tiktoken:*` tokenizers count locally;
  tiktoken downloads its vocabulary file once on first use (set `TIKTOKEN_CACHE_DIR` to control
  where it is cached). For no network access at all, use `-t heuristic`.
- **The GitHub Action** passes its inputs to the CLI through environment variables, never by
  interpolating them into a shell script, and needs no write permissions.
