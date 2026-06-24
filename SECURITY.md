# Security Policy

## Your data stays on your machine

ApplyPilot is a **local-first** tool. It is not a hosted service. Your resume,
profile, API keys, and generated documents are stored on your own computer
(under `~/.applypilot/` and your working directory) and are never sent to any
ApplyPilot server — there is no ApplyPilot server. The only outbound network
calls ApplyPilot makes are:

- to the **job boards** it searches,
- to your chosen **LLM provider** (e.g. Google Gemini) using **your own API key**, and
- optionally to a **CAPTCHA-solving service** if you configure one.

Keep your `~/.applypilot/.env` file private — it contains your API keys.

## Supported Versions

Security fixes are applied to the latest released version. Please make sure you
are on the most recent version before reporting an issue:

```bash
pip install --upgrade applypilot
```

## Reporting a Vulnerability

Please **do not** open a public issue for security vulnerabilities.

Instead, report it privately through GitHub's
[Security Advisories](https://github.com/Pickle-Pixel/ApplyPilot/security/advisories/new)
for this repository. Include:

- a description of the vulnerability and its impact,
- steps to reproduce, and
- any suggested remediation if you have one.

We aim to acknowledge reports within a few days and will keep you updated as we
work on a fix. Responsible disclosure is appreciated — please give us a
reasonable chance to release a fix before any public disclosure.
