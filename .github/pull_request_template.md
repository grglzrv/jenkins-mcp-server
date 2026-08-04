## Summary

Describe the change and its operational impact.

## Validation

- [ ] `make verify-version`
- [ ] Deployable changes use `make version VERSION=X.Y.Z`, with a version newer
      than the base branch.
- [ ] `make lint`
- [ ] `make test`
- [ ] `make helm-lint`
- [ ] Relevant integration or deployment validation completed

## Security and compatibility

- [ ] No credentials, tokens, certificates, or private endpoints were committed
- [ ] Backward compatibility and upgrade impact were reviewed
- [ ] Documentation, deploy manifests, examples, and README files were updated when required
- [ ] `CHANGELOG.md` has professional notes under every required category
