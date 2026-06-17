## Summary

<!-- What does this PR change, and why? -->

## Testing

<!-- How did you verify this? scry never spends real subscription credit in tests. -->
- [ ] `sh tests/smoke.sh` passes
- [ ] `python3 -m unittest discover -s tests -p 'test_*.py'` passes (if applicable)

## Checklist
- [ ] Tests use stubbed CLIs only — no real model calls, no subscription spend
- [ ] Updated docs (README / CHANGELOG) if behavior changed
- [ ] No secrets, API keys, or private prompts committed
