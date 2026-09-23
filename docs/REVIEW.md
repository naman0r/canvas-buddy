# Safety, setup, and experience review

Reviewed September 12, 2026; priorities updated September 23 with the 0.3.0 release. This is a code review and test pass, not an independent security certification.

## Release decision

Personal testing and a fictional-data demo are ready for feedback. Broader account onboarding needs work first. Canvas documents personal tokens as a testing mechanism and requires OAuth for apps used by multiple people; institution admins issue hosted Canvas developer keys. This corrects our earlier assessment that the token-based app was ready for friends to connect their accounts. [Canvas OAuth documentation](https://developerdocs.instructure.com/services/canvas/oauth2/file.oauth#manual-token-generation).

The published API policy also addresses approved integrations and disclosure of AI data use. Ask Northeastern's Canvas administrator about an approved integration, minimum read scopes, and permitted model processing. Do not assume open source, local storage, or GET-only requests exempt the app. No one has been contacted on your behalf. [Canvas API policy](https://www.instructure.com/policies/canvas-api-policy).

## What changed

| Area | Finding | Implemented refinement |
| --- | --- | --- |
| Untrusted output | Textual opened arbitrary Markdown links, including model-generated ones | Disable automatic opening; only exact cached HTTPS Canvas source URLs without queries/fragments can open |
| Local inference | A remote Ollama URL or inherited proxy could send course text elsewhere | Require a loopback endpoint and ignore proxy environment for Ollama requests |
| Secrets | Error formatting was inconsistent | Shared token redaction for chat, sync, search, setup, and CLI operation failures |
| Setup | Expired tokens and inaccessible resources gave terse HTTP errors | Recovery instructions, persistent field labels, provider/login help, and a clear empty-course result |
| Trust | A partial sync could look complete | Persistent cache timestamp/coverage summary, incomplete-section and unreadable-file counts, explicit AI-answer labels |
| Navigation | Users had to discover slash commands and scan every cached document | Upcoming/Grades/Coverage buttons; browser filtering, Open in Canvas, and Escape to close |
| First try | No way to explore without secrets and model setup | Disposable fictional-data demo; no network, saved settings, or model subprocesses |
| Predictability | Ollama silently chose a large default chat model | Require the installed model name instead |

These controls follow the practical direction of OWASP's guidance: treat retrieved documents and generated links as untrusted, constrain actions outside the model, and keep secret access separate. Prompt wording alone does not establish a security boundary. [OWASP prompt-injection guidance](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html).

Persistent progress, visible partial failures, useful error recovery, and a low-friction demo also follow the human-facing principles in the [Command Line Interface Guidelines](https://clig.dev/). The app has five direct runtime dependencies; this pass added none.

## Next priorities

1. **Before broad account onboarding: OAuth integration design.** Obtain institution approval and credentials; determine an approved native-client flow or token-exchange service. Never ship a shared client secret in an open-source binary. A service would change the local-only architecture and needs an explicit design decision. Implement authorization, cancellation, refresh, revocation, and account-binding tests once this is resolved.
2. **Credential storage:** use macOS Keychain with an explicit portable fallback; current files are owner-only but not independently encrypted. Add a clear disconnect/delete-data flow that explains remote token revocation separately.
3. **Document processing:** isolate PDF/Office extraction in a cancellable process with time/memory budgets. Current 25 MB downloads and Office expansion checks do not bound all parser CPU/memory use. Also harden download-host resolution against private-network destinations; signed redirects currently enforce HTTPS and omit the PAT, but are not a full network sandbox. [OWASP file-processing guidance](https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html).
4. **Answer quality:** a retrieval evaluation set now lives in `tests/test_retrieval.py` (fictional courses, expected source in the top three for each question). Extend it with contradictory announcements, stale grades, and follow-up questions, and add a model-side check for correct citations and appropriate uncertainty before adding more retrieval infrastructure. Cached metadata can verify link origin, not the truth of a model claim.
5. **New-user observation:** watch three people try the demo, including one who rarely uses Terminal. Ask them to find a deadline, locate an attendance policy, distinguish sample data from live data, and recover from a failed connection. Record friction manually; add no telemetry.

## Validation

46 automated tests pass, including malicious-link blocking, remote Ollama rejection, proxy isolation, offline demo behavior, 80×24 layout, browser filtering, partial-sync visibility, token redaction, and expired-token recovery. Ruff passes. `pip-audit` reported no known vulnerabilities in the locked runtime dependency set on the review date; this does not rule out undisclosed vulnerabilities. Existing tests cover pagination credential boundaries, read-only adapters, account separation, cache transactions, and cancellation.

No real Canvas content, grades, token, or professor names were put into the demo or sharing drafts. Broader institution/provider compatibility still needs testing; this pass did not exercise another student's account.
