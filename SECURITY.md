# Security

Canvas Buddy is an independent, early-stage project. Current account connection is for personal testing; broader onboarding needs OAuth. See the [review and launch prerequisites](docs/REVIEW.md).

## Boundaries

- Canvas API operations are GET-only; the access token itself may have broader privileges. Keep it private and revoke it in Canvas if exposed.
- Tokens live in an owner-only local file, separately from ordinary settings. They are not encrypted by this app. Local course data includes sensitive grades, feedback, and conversations.
- Model subprocesses do not inherit Canvas environment variables. Providers are configured without action tools; these settings are defense in depth, not an OS-level guarantee against a compromised CLI.
- Codex/OpenCode send selected context to their model service. Ollama requests are restricted to loopback endpoints and ignore HTTP proxy environment variables in the source preview.
- Retrieved content can contain prompt injection. Output may be wrong. The source preview opens only exact cached HTTPS Canvas links without query strings/fragments; this verifies the destination, not an answer's accuracy.
- Files have download limits, but extraction is not yet isolated with a hard CPU/memory budget. Signed download redirects are not a full network sandbox. See the review for remaining work.

## Reporting

Do not post access tokens, raw cache databases, private class content, or exploit details in a public issue. For a suspected vulnerability, use the maintainer's private contact on the [GitHub profile](https://github.com/naman0r), or open an issue containing only a request for a private reporting channel. Include the app version, platform, and a reproduction with fictional data when privately reporting.

No independent security audit has been performed. Automated dependency checks and regression tests reduce known risks; they do not establish that the app is vulnerability-free.
