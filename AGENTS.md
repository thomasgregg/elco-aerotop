# ELCO Aerotop — agent working rules

Custom Home Assistant integration for ELCO/Ariston Remocon.net gateways
(Galevo and BSB controller families). The project is open source.

## Working rules

- **NEVER put user data into versioned files.** The repository is public:
  no credentials, passwords, tokens, cookies, gateway IDs, serial numbers,
  e-mail addresses, real names, addresses or real zone names in source
  code, docs, tests or comments. Anonymize to placeholders such as
  `<gateway-id>` when documenting observed behavior.
  `captures/` and `tools/` are gitignored — they may hold local evidence,
  but nothing versioned may quote identifying values from them.
- **Check `captures/` before making real API calls.** The folder contains
  previously recorded API responses (HAR exports, JSON payloads) that are
  often sufficient for analysis. Only hit the live gateway when the
  required data is not already captured, and never perform writes without
  explicit user authorization.
- **NEVER run `git commit` or other git write operations unless the user
  explicitly asks.** Leave changes in the working tree.
- **Do not modify files via shell commands** (heredocs, sed, awk, patch,
  redirects) — use the editor/file-edit tools so changes land in the IDE.

## Verification

Run from the repo root:

```bash
uv run pytest        # test suite (stubs Home Assistant locally)
uv run ruff check .  # lint + import sorting
uv run ty check      # type checking (scope: custom_components, tools)
```

The dev environment is uv-managed; `homeassistant` and
`pytest-homeassistant` install only on Python >= 3.14.2.
