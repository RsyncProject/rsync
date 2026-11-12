# Hosted network checks

The Interoperability workflow follows normal CI runs for pull requests, pushes, schedules and manual runs. Fork pull requests are included. The controller and worker orchestration always come from the protected default branch; the candidate revision comes from the upstream CI run.

The current network check runs four Linux canaries as two isolated pairs. It checks quarantine, permitted connections, cross-cohort denial, a forbidden port, revocation and device removal. The connected transfer stage needs endpoint services and transport orchestration for Linux, FreeBSD, NetBSD, OpenBSD, macOS, Solaris and Cygwin, covering 42 directed pairs, both client roles and SSH and daemon transports.

## Trust boundaries

Only the isolation controller and recovery job have OIDC permission. Workers generate temporary RSA recipients and receive encrypted, single-use quarantine keys. RSA-OAEP uses 3072-bit keys and SHA-256. Private keys and enrolment keys are removed after connection; plaintext keys never enter artifacts or job outputs. Enrolment keys expire after 900 seconds and create ephemeral nodes.

The controller validates each node against the provider before admission. Address-specific grants use conditional policy updates. One workflow holds admission at a time; other cohorts wait in quarantine within a fixed deadline. Unexpected provider errors fail immediately. Separate GitHub-run isolation remains an unqualified case.

Admission leases carry GitHub build-provenance attestations. Recovery verifies the repository, workflow, source ref and signing revision before using a downloaded lease. Reassigned addresses require review rather than deleting another cohort's access. Failed or cancelled runs trigger a separate recovery workflow. Missing artifacts or failed verification stop recovery and need maintainer attention.

Candidate code is never checked out or executed by these credential-bearing jobs. The current workflow runs only trusted canaries. Native candidate tests run separately through CI. Before connected transfers execute candidate binaries, their fixtures must retain this separation and bind all endpoint identities and results to the candidate revision.

## Deployment

Install the trusted workflows and helpers on master in a small bootstrap PR before relying on them from the rework branch. GitHub only activates [workflow_run listeners](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_run) from the default branch. The bootstrap consists of interoperability.yml, interoperability-cleanup.yml, the interop helpers and their two test scripts.

Write access to these files on master carries the controller's test-tailnet authority. Require maintainer review of changes to them before enabling the broader federation rules.

The controller federated identity keeps its existing client ID and audience. Configure these fields:

Field | Value
--- | ---
Issuer | https://token.actions.githubusercontent.com
Subject | repo:RsyncProject/rsync:ref:refs/heads/master
repository_id | 266877886
repository_owner_id | 166180282
event_name | workflow_run
workflow_ref | RsyncProject/rsync/.github/workflows/interoperability*.yml@refs/heads/master
runner_environment | github-hosted

Remove the old actor_id, head_ref and job_workflow_ref restrictions. Keep write access to auth_keys, policy_file, devices:core and devices:posture_attributes. The posture scope is required by policy-file write access. Restrict tagged enrolment to tag:quarantine. No user, DNS, route or credential-administration scopes are needed.

With no active runs, rename tag:rsync-quarantine to tag:quarantine in tagOwners and the controller credential's tag restrictions. Preserve the explicit empty acls baseline and any unrelated policy content. Revoke the obsolete worker federated identity after the brokered workflow is qualified; worker jobs have no OIDC permission.

The workflow needs contents:read and actions:read. The source and result jobs also use statuses:write. The controller needs id-token:write and attestations:write; recovery uses id-token:write and attestations:read. All actions are pinned. The new dependency is GitHub's actions/attest v4.2.2. Artifact storage records are disabled, so artifact-metadata write access is unnecessary.

Publish the full CI rework first so its unprivileged diagnostics exercise the new helpers before the bootstrap is reviewed. After the bootstrap merges, rerun CI on the rework branch to activate the listener. Qualify Interoperability through revocation and cleanup before merging the CI router. Once ci.yml is on the default branch and existing PRs have fresh runs, require the Interoperability commit status and the Required check from GitHub Actions in branch protection. The latter appears as CI / Required in the Actions interface. Enabling those requirements before the router is deployed would block PRs using the legacy workflows. The commit status is posted to the upstream candidate SHA. Before posting, the publisher checks for a newer CI run or attempt and rejects obsolete results.

## Recovery

The controller performs cleanup on ordinary failure. The separate recovery workflow handles failed and cancelled Interoperability runs using the attested lease. An unavailable or invalid lease, reassigned addresses or provider errors leave a failed recovery job. Review the exact recorded node IDs, key IDs and grants before changing the test tailnet. Never replace the entire policy to clear one run.

For rollback, disable new Interoperability runs first. Keep the controller identity available while active runs finish or are reconciled from their leases. Remove its authority after the recorded grants, nodes and keys have been cleared, then adjust the required status setting before removing the workflows.
