# HACS / Home Assistant Marketplace submission checklist

**Not a submission, approval, or published release.** This is a preparation checklist to discuss with the original author.

## Permission and ownership

- [ ] Obtain review/feedback from original author @buggedcom on upstream PR #11.
- [ ] Agree whether upstream or this fork is the intended official catalog target; do not submit two repositories providing the same `hass_ledvance` domain.
- [ ] Confirm the submission account and ongoing support/contact policy with the original maintainer.
- [x] Preserve the MIT license and original attribution.
- [x] List both authors as contributors without claiming transfer of upstream ownership.

## Engineering and catalog requirements

- [x] Include HACS metadata (`hacs.json`), manifest, documentation and issue tracker.
- [x] Add GitHub Actions tests, Ruff, HACS action and Hassfest; allow manual dispatch.
- [ ] Verify every workflow is **green on the intended release commit**, including network-dependent tests.
- [ ] Perform manual tests with real Ledvance/Sylvania hardware (LAN commands, login/logout, reauthentication, sensors, mixed lights, room assignments, adding devices).
- [ ] Confirm minimum supported Home Assistant version in `hacs.json` still works.
- [ ] Publish an appropriate GitHub release with matching version and changelog **only after approval/decision**. A tag alone is not a release.
- [ ] Draft HACS catalog pull request with evidence and scope, and submit **only after approval**.

## Important

The upstream integration is already using the `hass_ledvance` domain. The two forks cannot coexist. HACS repository validation and catalog acceptance are distinct decisions. A successful HACS action is not an acceptance guarantee.
