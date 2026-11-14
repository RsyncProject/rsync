# Network checks

CI / Interoperability contains Linux, FreeBSD, NetBSD, OpenBSD, macOS, Solaris and Cygwin endpoints. Linux, macOS and Cygwin execute on hosted runners; the BSD and Solaris binaries execute inside their guests. Guest forwarding exposes fixture ports only. Administrative SSH stays local to the VM host.

The 168 cases cover every directed platform pair, push and pull initiation and SSH and daemon transport. Separate trees prevent concurrent cases from sharing destinations. Initial copies finish before verification and source updates; checksum updates finish before destination verification. Results include data, modes, integer timestamps, symlink targets and hard-link groups.

The parent CI run and attempt identify each cohort. The tested revision comes from GitHub's resolved reusable-workflow commit and is checked against the native checkout. Endpoint records also contain the operating system, effective UID/GID and executable SHA-256. Required depends on all seven endpoint jobs, complete case results and confirmed network cleanup.

Artefact names end in `attempt-N`. Context and lease artefacts use the controller attempt; other interoperability artefacts use the parent CI attempt. The suffix prevents a rerun from consuming records from an earlier attempt.

## Authentication

The Network controller uses trusted default-branch code through a workflow_run listener. It is deployed independently of the PR endpoint workflow and ignores CI runs which have not selected that workflow. Its existing OIDC identity has write access to auth_keys, policy_file, devices:core and devices:posture_attributes in the dedicated test tailnet. The posture scope is a dependency of policy-file writes. Enrolment is restricted to tag:quarantine.

Endpoint jobs have read-only GitHub permissions and no OIDC permission. Each generates a temporary RSA recipient and receives one encrypted, single-use enrolment key. RSA-OAEP uses 3072-bit keys and SHA-256. Key files and recipient private keys are removed after enrolment. SSH private keys are removed when fixture services stop. Plaintext credentials are excluded from artifacts and job outputs.

Endpoints authenticate through the supported Tailscale CLI. Client package checksums and the macOS source revision are pinned in interop-client.py. Route acceptance and DNS changes are disabled and checked through the client's active preferences. SSH host keys are pinned to endpoint identities.

Admission permits only the cohort's registered IPv4 addresses on TCP ports 18731 and 18733. Conditional policy updates preserve other cohorts. Local service checks precede and follow denial probes. Key expiry limits unused enrolment credentials; active nodes require explicit removal.

## Configuration

The production federated identity uses these claims:

Field | Value
--- | ---
Issuer | https://token.actions.githubusercontent.com
Subject | repo:RsyncProject/rsync:ref:refs/heads/master
repository_id | 266877886
repository_owner_id | 166180282
event_name | workflow_run
workflow_ref | RsyncProject/rsync/.github/workflows/interoperability*.yml@refs/heads/master
runner_environment | github-hosted

Endpoint jobs have no federated identity. Controller discovery uses the parent CI run and attempt and verifies the controller's default-branch revision. No per-PR identity, branch or repository variable is required. The controller checks CI and endpoint liveness before obtaining authority, requesting keys and publishing enrolment.

## Recovery

Normal cleanup revokes the recorded grant, removes owned devices and revokes issued keys. An individual failure does not prevent attempts to remove the remaining resources. Device deletion checks the recorded node identity; reassigned devices remain untouched.

Keys created before enrolment delivery can be revoked from the local journal without an admission lease. An interrupted request with an unknown key ID or enrolment delivered without a lease requires reconciliation. A failed transfer remains failed even when resource cleanup succeeds.

Admission leases are attested before access is granted. Recovery verifies the original lease bytes, repository, controller workflow, signing commit, source ref and run attempt before modifying the tailnet. Failed or cancelled controller runs trigger recovery from trusted code.

Missing leases and provider failures require reconciliation. Partial enrolment before a lease exists remains quarantined; hostnames alone are insufficient grounds for device deletion. Cleanup failure keeps the CI result unsuccessful.
