<p align="right">
  <a href="SECURITY.zh_CN.md">简体中文</a> · <strong>English</strong>
</p>

# Security Policy

## Supported versions

Security fixes are applied to the latest release and the `main` branch.

## Reporting a vulnerability

Please do **not** open a public issue for security problems. Use GitHub's
"Report a vulnerability" (private security advisory) on this repository, or
contact the maintainers directly through the contact information on the
repository profile.

You will get an initial response within a few days, and a fix or mitigation
plan as soon as the issue is understood.

## Scope notes

- The web app is designed to run on **localhost for a single user**. It has no
  authentication layer. Do not expose it directly to untrusted networks.
- Uploaded firmware is parsed with defensive checks (size limits, structure
  validation), but firmware images are untrusted input — run the simulator on
  data you are willing to inspect.
- The plays catalog download feature fetches from the official
  `ai-passport.folotoy.cn` service only, and validates size, SHA-256 and image
  structure before storing.
