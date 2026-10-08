# Phase 0 — feasibility proofs and licence check

Everything below was run on 2026-10-08 on an Apple-silicon Mac (Colima, aarch64, 4 CPU / 7.7 GB). Raw evidence is in `proof/`.

## 1. Pinned components and what each proof showed

| Component | Pin | Result |
|---|---|---|
| LocalStack | `localstack/localstack:4.14.0` (community) | **Runs with no account.** `2026.09.1` (the newest tag) **exits with code 55**: *"License activation failed … set LOCALSTACK_AUTH_TOKEN"*. So the zero-account demo is pinned to 4.14.0. |
| Cloud Custodian | `c7n==0.9.53` | `custodian validate` ✔, `run --dryrun` ✔ (matched exactly the 2 idle instances, excluded the protected one), `run` with `mark-for-op` ✔. |
| CloudQuery CLI | `ghcr.io/cloudquery/cloudquery:6.45.0` | Sync ✔ (`Sync completed successfully`, `errors=0`, `resources=13`). |
| CloudQuery AWS source | `cloudquery/aws v22.19.2` | **Downloads anonymously**, syncs `aws_ec2_instances` + `aws_ec2_ebs_volumes` from LocalStack. (Latest on the hub is v35.0.0, 13 majors ahead; not tested.) |
| CloudQuery PostgreSQL destination | `plugins-destination-postgresql-v8.17.0`, **built from source** | **The hub download needs `cloudquery login`** for every version tried (`v7.4.0`, `v8.9.0`, `v8.17.0`: *"Error: unauthorized. Try logging in via `cloudquery login`"*; `cloudquery/file` is gated too). To stay account-free it is compiled from the upstream tag (repo licence **MPL-2.0**, needs Go 1.27.1) in `cq/Dockerfile` and run as a *local* plugin. This is real CloudQuery code writing to PostgreSQL, not a stand-in. |
| PostgreSQL | `postgres:16.4-alpine` | Migrations incl. RLS and append-only triggers apply cleanly. |
| n8n | `n8nio/n8n:1.123.84` | 3 workflows import + activate on start; Webhook raw-body, Code (`crypto`), HTTP, Wait-on-form with a time limit all work. |

Python/Node dependency locks: `api/requirements.lock`, `runner/requirements.lock`, `dashboard/package-lock.json`.

## 2. Custodian: validate, dry-run, one non-destructive action (disposable container, LocalStack 4.14.0)

Policy rendered by the platform's own template code (`app/policy.py`, template `idle-ec2-v1`): resource `aws.ec2`, scope filter `InstanceId in [3 ids]`, `tag:TenantId: payments`, `tag:Protected: absent`, `State.Name: running`, CloudWatch `CPUUtilization` average `< 5` over 7 days; action `mark-for-op` (`op: stop`, `days: 2`, tag `finops_mark`).

```
$ custodian version                      -> 0.9.53
$ custodian validate policy.yml          -> "Configuration valid: policy.yml"   (exit 0)
$ custodian run --dryrun ... policy.yml  -> count:2   matched [payments-batch-dev-1, payments-batch-dev-2]
                                            (the protected payments-dr-standby was in scope but excluded by tag:Protected)
$ custodian run ... policy.yml           -> "Tagging 2 resources for stop on 2026/10/10"
```
Before/after (`proof/phase0-custodian-action.txt`): the two targets gained `finops_mark = "Resource does not meet policy: stop@2026/10/10"`; **all six instances stayed `running`**; the protected and unrelated instances were untouched.

The EC2 `metrics` filter needs CloudWatch data, which LocalStack does not generate, so the seed writes 7 daily `AWS/EC2 CPUUtilization` datapoints per instance with `put_metric_data`. That is a fixture, stated plainly: Custodian's filter really queries it and really excludes busy instances.

Precise combination that works: **EC2 `mark-for-op` on idle instances against LocalStack 4.14.0 + c7n 0.9.53**. (A second template, EBS `tag`, is also exercised in the live flow for the Analytics case.)

## 3. CloudQuery → PostgreSQL (`proof/phase0-cloudquery-sync.txt`)

```
Sync summary duration=1s errors=0 resources=13 result="Sync completed successfully" warnings=0
End sync destinations=["postgresql (local@/usr/local/bin/cq-destination-postgresql)"] source="aws (cloudquery/aws@v22.19.2)"
```
`inventory_raw.aws_ec2_instances` holds 6 rows and `inventory_raw.aws_ec2_ebs_volumes` 7 (6 root volumes carry no `TenantId` tag and are counted as `unscoped`, never stored). Tags, state and attachments round-trip intact. The CLI's telemetry is disabled (`--telemetry-level none`).

## 4. Licence of the reference repository (independent of the technical assessment)

- No `LICENSE`, `COPYING` or `NOTICE` file in the supplied folder; no licence/copyright string anywhere in the tree; `dashboard/package.json` has no `license` field.
- GitHub's licence endpoint for `AdiiiC/Agentic-FinOps-Optimizer` returned **404** and the repository root listing has no licence file.
- **Conclusion: reuse rights are not established.** Public visibility is not permission; under default copyright you may not copy, modify or redistribute its code. Not usable: any of its source files, its React pages/styles, its tests, its README prose.
- **What was done:** nothing in the upstream tree was edited or copied. All code under `sentinel/` was written fresh from the PRD and blueprint with a different structure (e.g. no Streamlit/Terraform/LLM-recommender, SQL-first schema, new UI).
- **Honest caveat on "clean room":** I read the upstream source to verify the audit, so this is *independent re-implementation*, not a strict clean-room (a strict one needs implementers who never saw the code). If rights matter for publication, either ask the owner to add a licence or have the sentinel code reviewed by someone who has not read upstream.
- Third-party licences to keep straight: CloudQuery monorepo/plugins MPL-2.0; Cloud Custodian Apache-2.0; n8n is source-available (Sustainable Use License, not OSI open source); LocalStack 4.14.0 community is the last tag I could confirm runs without an account.

## 5. Not verified in Phase 0
Windows/WSL2 and amd64 (the Go plugin build and all images were exercised on arm64 only); Slack interactivity (needs a public HTTPS callback; the local n8n form is used); CloudQuery AWS plugin versions newer than v22.19.2.
