# Security policy

## Public-repository rule

This repository contains a reusable labeling Skill and fictional examples. Do
not commit real comments, customer workbooks, run outputs, access tokens,
cookies, private keys, or exported chat history. Keep those files in the local
ignored directories documented in `AGENTS.md`.

Run the release gate before changing repository visibility:

```bash
python3 scripts/public_release_check.py --history
```

The check is a high-signal guard, not a substitute for a credential manager or
GitHub's secret-scanning features. If a credential was ever committed, revoke
it even when the file has since been deleted.

## Reporting a problem

Do not open a public issue containing a secret or private data. Revoke the
credential first, then contact the repository owner through a private channel
with the affected commit and a short description of the impact.
