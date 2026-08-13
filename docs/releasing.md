# Releasing pydry

pydry uses Conventional Commit pull-request titles and Release Please to prepare releases. A release produces one version shared by the Python package and the GitHub Action.

## Repository setup

Configure these Actions values before enabling the release workflow:

- Repository or organization variable `RELEASE_APP_CLIENT_ID`: the client ID of the installed release GitHub App.
- Repository or organization secret `RELEASE_APP_PRIVATE_KEY`: the App's private key.

Install the App on this repository with these repository permissions:

- Contents: read and write, for release branches, tags, and GitHub releases.
- Pull requests: read and write, for release pull requests.
- Issues: read and write, for release labels.

The release workflow deliberately uses a short-lived GitHub App installation token. Tags created by the built-in `GITHUB_TOKEN` do not start another workflow, which would prevent the separate PyPI publishing workflow from running.

In the repository's merge settings, enable squash merging and use the pull-request title as the default squash commit message. Add `Validate PR title` and the normal CI jobs as required checks for `main`.

## Release flow

1. Open pull requests against `main` with Conventional Commit titles such as `feat: add JSON output`, `fix(cli): preserve exit status`, or `feat!: revise the configuration schema`.
2. Merge pull requests by squash merge so the validated title becomes a commit on `main`.
3. Release Please creates or updates a release pull request. Its changelog and version follow the commits accumulated since the previous release.
4. Merge the release pull request when the release is ready.
5. Release Please creates the `v<major>.<minor>.<patch>` tag and GitHub release, then moves the floating `v<major>` and `v<major>.<minor>` GitHub Action tags to the same commit.
6. The three-component version tag starts the existing PyPI trusted-publishing workflow.

## Marketplace

To list the action, accept the GitHub Marketplace Developer Agreement, open a versioned release in GitHub's release UI, select **Publish this Action to the GitHub Marketplace**, choose its categories, and update the release. See [Publishing actions in GitHub Marketplace](https://docs.github.com/en/actions/how-tos/create-and-publish-actions/publish-in-github-marketplace) for the current GitHub procedure.

Release Please remains the source of version tags and GitHub releases.

## Version sources

Release Please updates `[project].version` in `pyproject.toml`, creates `CHANGELOG.md`, and keeps `pydry.__version__` synchronized through the release annotation in `src/pydry/__init__.py`. The manifest in `.github/.release-please-manifest.json` records the most recently released version; it should not be manually advanced to an unreleased source version.
