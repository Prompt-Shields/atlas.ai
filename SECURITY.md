# Security Policy

## Reporting a vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

Report suspected vulnerabilities through GitHub's
[private vulnerability reporting][gh-pvr] on this repository:
<https://github.com/Prompt-Shields/atlas.ai/security/advisories/new>
(Security → Report a vulnerability). Only you and the maintainers see the report.
You may also write to the team mailbox, **security@promptshields.com**.

If the form is not available to you, open an issue that says only that you have a
security report and asks for a private channel. Put no details in it.

Reports about **Promptly Guide** (the Mac and Windows app, whose repository is
private) come here too: start the title with "Guide:".

Please include:

- A description of the issue and the impact you believe it has
- Steps to reproduce, or a proof-of-concept
- The affected version, commit SHA, or deployment
- Any suggested remediation

We aim to acknowledge reports within **3 business days** and to provide a
remediation plan within **10 business days**. We will keep you updated as we work
on a fix, and we are happy to credit you in the release notes unless you prefer
otherwise.

## Supported versions

Only the latest release on the default branch receives security updates. Older
tags are provided as-is.

## Scope

In scope:

- Code in this repository
- Default configurations shipped in this repository

Out of scope:

- Findings that require a compromised host or a malicious browser extension
  already running with elevated privileges
- Denial of service through resource exhaustion on a self-hosted deployment you
  control
- Vulnerabilities in third-party dependencies — please report those upstream,
  though we appreciate a heads-up so we can bump the pin

## How we look for problems ourselves

- `.github/workflows/codeql.yml` runs CodeQL on the Python, TypeScript and workflow
  code on every pull request, every push to `main` and weekly.
- `.github/dependabot.yml` opens weekly update pull requests for the workflow
  actions, the backend's and worker's Python packages and the frontend's npm packages.
- `.pre-commit-config.yaml` runs gitleaks and private-key detection before a commit.

## Handling secrets

This repository must never contain live credentials. All configuration is
supplied through environment variables or untracked local config files; see the
`*.example` / `*.template` files for the expected shape.

If you believe a credential has been committed, email
**security@promptshields.com** immediately rather than opening a pull request
that removes it — a public commit that deletes a secret advertises the secret.

[gh-pvr]: https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability
