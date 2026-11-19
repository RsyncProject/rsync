# Cross-platform transfers

We run Linux, FreeBSD, NetBSD, OpenBSD, macOS, Solaris and Cygwin endpoints. Linux, macOS and Cygwin run directly on hosted runners; BSD and Solaris run inside guests. We expose only the fixture ports through Tailscale and keep administrative SSH local to each guest host.

We run push and pull transfers over SSH and daemon connections for every ordered platform pair, giving 168 data cases. We use separate trees so concurrent cases never share a destination. The first phase verifies the initial transfer. The second verifies checksum updates and deletion. We compare data, modes, integer timestamps, symlink targets and hard-link groups.

We test compatible POSIX ACLs and xattrs separately. We return fake-super backups unchanged with `-XX` and decode them only on their originating platform. We require explicit errors for unsupported metadata and empty NTFS xattrs.

We bind every record to the parent CI run, attempt and tested revision. Endpoint records include the operating system, effective UID, effective GID and executable SHA-256. We accept the final result only after all seven endpoints complete every assigned case and we confirm network cleanup.

Artefact names end in `attempt-N`. Context and lease artefacts use the controller attempt; other interoperability artefacts use the parent CI attempt. The suffix prevents a rerun from consuming records from an earlier attempt. Controller discovery lists only runs created since the current CI attempt started and reuses each candidate's artefact listing for its first context download. A matching timestamp does not replace validation of the controller context.

Recipient and enrolment waits allow 30 minutes without a newly observed endpoint job start. Each endpoint can renew that startup window once, using GitHub job identity and start metadata from the same CI attempt. Polling, artefact changes and transient request failures do not renew it. Transfer barriers retain their fixed deadlines. The controller and endpoint jobs retain their 75-minute limits.

We publish each phase result before waiting at the barrier, which keeps a failed endpoint available until its peers finish the current phase. Each verification step requires its barrier to have succeeded. It checks CI liveness again without downloading the same completion artefacts. We block later phases but still run local cleanup. A missing, failed or incomplete endpoint result fails the final check.

Blocked endpoint jobs fail after uploading their blocked record and attempting local cleanup. Qualification after a shared startup failure requires a full CI attempt. A failed-job rerun can leave successful endpoints from an earlier attempt, whose records remain ineligible.

GitHub API errors report explicit rate-limit signals without logging response bodies. An ordinary HTTP 403 remains a refusal and is not retried.

## Authentication

The Network controller runs trusted default-branch code through a `workflow_run` listener and ignores CI runs that did not select the endpoint workflow. We configure the dedicated test tailnet outside this repository. The controller identity needs write access to `auth_keys`, `policy_file`, `devices:core` and `devices:posture_attributes`. We restrict enrolment to `tag:quarantine`.

The controller checks CI and endpoint liveness before obtaining authority, requesting keys and publishing enrolment.

The broker waits for every enrolment recipient before obtaining Tailscale authority. A startup timeout before that point does not create tailnet resources.

We give endpoint jobs read-only GitHub permissions and no OIDC permission. Each endpoint generates a temporary RSA recipient and receives one encrypted, single-use enrolment key. RSA-OAEP uses 3072-bit keys and SHA-256. We remove the enrolment files immediately and remove SSH private keys when fixture services stop. We never include plaintext credentials in artefacts or job output.

We pin the Tailscale CLI in `interop-client.py` and pin SSH host keys to endpoint identities. We disable route acceptance and DNS changes and verify the active preferences.

We admit only the cohort's registered IPv4 addresses on TCP ports 18731 and 18733. Policy updates preserve other cohorts. We check local services before and after the denial probes. Enrolment keys expire; we remove active nodes explicitly.

## Configuration

We configure the controller identity with these claims:

Field | Value
--- | ---
Issuer | https://token.actions.githubusercontent.com
Subject | repo:RsyncProject/rsync:ref:refs/heads/master
repository_id | 266877886
repository_owner_id | 166180282
event_name | workflow_run
workflow_ref | RsyncProject/rsync/.github/workflows/interoperability*.yml@refs/heads/master
runner_environment | github-hosted

We give endpoint jobs no federated identity. We find the controller from the parent CI run and attempt. We then verify its default-branch revision. We do not need a per-PR identity, branch or repository variable.

## Recovery

We always try to revoke the cohort grant, remove its devices and revoke its enrolment keys. We remove a device only while its current node ID matches the lease. If a run fails before any key request, we record that no resources need cleanup without marking transfers as passed.

Keys created before enrolment delivery can be revoked from the local journal without an admission lease. An interrupted request with an unknown key ID or enrolment delivered without a lease requires reconciliation.

We attest the admission lease before granting access. A failed or cancelled controller triggers the cleanup workflow. We first check whether cleanup already succeeded. Otherwise we verify the lease bytes, repository, controller workflow, signing commit, source reference and run attempt before changing the tailnet.

If automated cleanup fails, we use the attested `interoperability-lease-attempt-N` artefact from the controller run to identify the cohort. We remove only the recorded grant, keys and devices while each device's node ID still matches. Without an attested lease, we compare the `interoperability-ready-PLATFORM-attempt-N` artefacts with quarantined node IDs before making any change. A hostname alone is not enough to delete a device.
