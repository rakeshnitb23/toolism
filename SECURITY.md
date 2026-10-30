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
- **`--mcp-url URL` connects to that HTTP endpoint** to initialize an MCP session and list
  tools. It does not execute tools or configure OAuth/authentication headers. Use an endpoint
  you intend to contact; remote definitions are handled as data, like tool definition files.
- **`anthropic:<model>` tokenization** sends the tool definitions to Anthropic's `count_tokens`
  API using your `ANTHROPIC_API_KEY`. The `heuristic` and `tiktoken:*` tokenizers count locally;
  tiktoken downloads its vocabulary file once on first use (set `TIKTOKEN_CACHE_DIR` to control
  where it is cached). For no network access at all, use a file/stdin input with `-t heuristic`.
- **The GitHub Action** passes its inputs to the CLI through environment variables, never by
  interpolating them into a shell script, and needs no write permissions.
