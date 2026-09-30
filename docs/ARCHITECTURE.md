# Personal Council architecture

## Distribution and identity

The GitHub repository contains the Python server, browser UI and public example
profiles. Each installation starts its own loopback HTTP server. There is no
shared inference backend, account database, API key or hosted UI dependency.

```text
User's browser -> 127.0.0.1 -> Python Council controller
                               |              |
                           Codex CLI      Claude Code CLI
                               |              |
                         User's OpenAI   User's Anthropic
                            account         account
```

User preferences and research profiles live outside the checkout. The default
location is selected from the operating system's current user directory.
PERSONAL_COUNCIL_HOME is an explicit override for development or separate
research environments. CLI authentication remains owned by the respective CLI.
The app never copies authentication files into its settings directory.

## Request lifecycle

1. The UI obtains a random, process-local session token from /api/session.
2. Every POST includes X-Council-Token. Host and Origin are validated.
3. The controller validates the question, mode and profile, and prevents
   concurrent Council sessions in one server process.
4. Both providers' subscription login states are checked before inference.
5. Independent answers run concurrently, followed by the selected stages.
6. Polling /api/runs/{id} retrieves completed stages, status and logs.

Questions and model output are passed via UTF-8 stdin, never shell expressions.
Each invocation uses an isolated temporary working directory. Responses are
displayed as text using textContent so model-supplied HTML is not executed.

In Quick mode the controller invokes 3 model calls. Council uses 7, and Deep
Debate uses 9. Codex synthesizes the final response in every mode. Every call
receives the selected context. Each model revises against the other model's
criticism; a critique failure stops the session instead of being silently used
as evidence. Existing answers remain available when a later stage fails.

## Runtime limitations

The local server is designed for a single trusted OS user, not internet hosting
or multi-tenant deployment. Other software running under the same OS account
is outside its security boundary. CLI user/admin configuration remains subject
to the CLI's own policies. Subscription login and use limits may change with
provider versions and contracts.

Sessions are kept only in server memory, with at most 30 retained records.
There is no history database, automatic account provisioning, export workflow
or web-search grounding. The app cannot carry ChatGPT conversation memory into
Codex automatically; any relevant research context must be supplied explicitly
through the question or profile.

## Release preparation

Only source files, examples and tests belong in a release archive. User settings,
private profiles, CLI credentials and model responses are excluded. The CI suite
uses mocked CLI responses and temporary user data, so no subscriptions or
secrets are needed in GitHub Actions. Actual CLI compatibility must also be
checked with a locally authenticated account before claiming an end-to-end
provider run.
