# Publishing preflight

Taskledger is MIT-licensed but remains in pre-release preparation. This
checklist separates release blockers from improvements that can follow an
initial public release.

## Release blockers

- [x] Add an open-source license. Taskledger uses the MIT License.
- [ ] Decide the supported Codex surfaces and operating systems, then run and
  record a clean installation smoke test on each one.
- [ ] Complete or explicitly defer the failure-injection and 71-requirement
  conformance gaps identified in the technical documentation.
- [ ] Verify that a fresh user can follow the README from clone to an approved
  first assignment without relying on files outside this repository.
- [ ] Confirm that `.taskledger/`, worker credentials, test repositories, and
  private benchmark data are excluded from the published source and package.
- [ ] Add a concise GitHub repository description and relevant topics; the
  public repository currently has neither.
- [ ] Confirm that the release destination and issue/security settings match the
  support promises in the README.

## Community and support files

- [ ] Add `CONTRIBUTING.md` with development setup, test expectations, pull
  request scope, and the contribution/licensing terms.
- [ ] Add `SECURITY.md` with a private vulnerability-reporting channel and a
  supported-version policy.
- [ ] Add a `CODE_OF_CONDUCT.md` before actively soliciting community
  contributions.
- [ ] Add issue and pull-request templates if outside contributions are wanted.
- [ ] State where users should ask setup questions and report bugs.

## Packaging and automation

- [ ] Add CI for supported Python versions and operating systems.
- [ ] Complete the packaging metadata. The license and project README are now
  declared; project URLs, author/contact details, and classifiers remain.
- [ ] Build a wheel and source distribution, inspect their contents, and install
  each into a clean environment.
- [ ] Decide whether to publish to PyPI, a Codex plugin marketplace, both, or
  neither; document only the installation paths that actually exist.
- [ ] Add a changelog and a repeatable tagged-release process.
- [ ] Add an automated documentation-link and example-command check.

## Compatibility evidence

- [ ] Record the Codex version, model roles, operating system, Python version,
  and Git version used for each full workflow test.
- [ ] Keep CLI compatibility separate from orchestration compatibility. The CLI
  can be invoked by any local agent, but only the Codex orchestration package is
  currently implemented.
- [ ] If Claude Code support is claimed later, add native `.claude/skills` and
  `.claude/agents` assets plus an end-to-end conformance run; do not infer support
  solely because Claude can execute the CLI.
- [ ] Publish token-efficiency results only with comparable task scope, model
  roles, reasoning effort, elapsed work, and evaluation cost.

## Release-day check

- [ ] Run `python3 -m unittest discover -s tests -v` from a clean checkout.
- [ ] Run `./scripts/install-local.sh` in a clean local environment and start a
  new Codex task to confirm skill discovery.
- [ ] Verify every relative link and copy-paste command in `README.md`.
- [ ] Confirm the CLI, Python package, plugin manifest, and skill metadata all
  report the same version.
- [ ] Tag the exact tested commit and attach concise release notes describing
  limitations and migration concerns.

## Useful references

- [GitHub: About READMEs](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-readmes)
- [GitHub: About community profiles for public repositories](https://docs.github.com/en/communities/setting-up-your-project-for-healthy-contributions/about-community-profiles-for-public-repositories)
- [GitHub: Adding a license to a repository](https://docs.github.com/en/communities/setting-up-your-project-for-healthy-contributions/adding-a-license-to-a-repository)
- [GitHub: Setting guidelines for repository contributors](https://docs.github.com/en/communities/setting-up-your-project-for-healthy-contributions/setting-guidelines-for-repository-contributors)
