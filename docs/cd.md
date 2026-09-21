# Phase 7B continuous deployment

Phase 7B implements controlled application deployment contract `howreliable-cd-1.0` in
`.github/workflows/deploy.yml`. It automates deployment to existing ECS Fargate resources; it
does not provision AWS, publish the scientific bundle, train a model, or implement Terraform.
Implementation and offline validation are complete. No live GitHub Actions CD run, ECR push,
ECS deployment, or post-deployment smoke has been executed.

## CI/CD separation and trigger

The Phase 7A workflow remains read-only and AWS-independent. Deployment is a separate,
manual-only `workflow_dispatch` workflow. Its required `commit_sha` input must be a full
lowercase 40-character SHA, resolve exactly to the checked-out commit, and be an ancestor of
`origin/main`. Pull requests and arbitrary external/feature-branch code cannot deploy.

Before AWS authentication, the `validate` job reruns Ruff, strict mypy, `pip check`, plain
pytest, the Phase 7A contract validators, and Phase 7B workflow/IAM validators. This is the
deployment-critical CI gate. It preserves the documented clean-runner behavior: ignored
canonical-bundle tests are explicit `full_artifacts` skips, not silently removed. No research
experiment or model training runs during CD.

The `deploy` job depends on that gate and targets the GitHub `production` Environment. The
environment should be configured with a required reviewer, a `main` deployment-branch rule,
and protected deployment variables. These repository settings are recommended but are not
claimed to exist or configured by this code.

## OIDC and least-privilege IAM

Only the deployment job has `contents: read` plus `id-token: write`. It uses the official
`aws-actions/configure-aws-credentials@v6.3.0` action to exchange GitHub's OIDC token for a
short-lived AWS role session. There are no long-lived access-key secrets.

`infrastructure/iam/github-deploy-trust-policy.json` restricts assumption to the exact
immutable repository and `production` environment subject. Substitute the AWS account ID,
GitHub owner and numeric owner ID, and repository and numeric repository ID without widening
the subject to arbitrary repositories.

`infrastructure/iam/github-deploy-policy.json` permits ECR authentication and upload only to
the configured repository, task-definition describe/register operations, service describe/
update for the configured service, and `iam:PassRole` only for the exact application task
and execution roles. `PassRole` is necessary so ECS may use those roles in the registered
revision; its condition limits passing to `ecs-tasks.amazonaws.com`. The policy grants no S3
write/delete, CloudWatch administration, wildcard ECS/ECR/IAM administration, or
infrastructure-creation permission.

Non-secret GitHub production variables are:

- `HOWRELIABLE_DEPLOY_ROLE_ARN`
- `HOWRELIABLE_AWS_REGION`
- `HOWRELIABLE_ECR_REPOSITORY`
- `HOWRELIABLE_ECS_CLUSTER`
- `HOWRELIABLE_ECS_SERVICE`
- `HOWRELIABLE_API_BASE_URL`
- `HOWRELIABLE_S3_BUCKET`
- `HOWRELIABLE_S3_PREFIX`
- `HOWRELIABLE_TASK_ROLE_ARN`
- `HOWRELIABLE_EXECUTION_ROLE_ARN`
- optional `HOWRELIABLE_ALLOW_HTTP_SMOKE=true` only for a documented temporary HTTP smoke

## Immutable image and ECS revision

The authenticated job logs into the existing ECR registry with the official ECR action. It
uses tag `sha-<full-git-sha>`. Before building, it queries the repository and refuses to
overwrite an existing tag; ECR tag immutability should also be enabled. It builds once,
checks non-root user/port/healthcheck/command, imports the packaged application, then pushes
that same local image. The resolved ECR digest is queried and recorded in safe logs and the
workflow summary. `latest` is never used.

The workflow renders the tracked task-definition template through typed Python support code.
It injects only the SHA-tagged image, region, S3 bucket/prefix, task-role ARN, and execution-
role ARN. Validation preserves Fargate/awsvpc, 512 CPU, 1,024 MiB, container `howreliable-api`,
port 8000, S3 backend, explicit `howreliable-rf-2022-cutoff-v1`, distinct roles, and the
`/howreliable/api` awslogs contract. It then registers a new revision and updates only the
configured existing ECS service. It does not create a cluster, service, ALB, target group,
network, role, ECR repository, S3 bucket, log group, or alarm.

Application deployment never publishes, replaces, or mutates the S3 model bundle. Model-
artifact publication remains the separate Phase 6A operation.

## Stability, smoke, and rollback

Production concurrency group `howreliable-production` permits one deployment at a time and
does not cancel an in-progress rollout. The deployment job is bounded to 60 minutes; both
forward and rollback ECS stability waits are bounded to 20 minutes.

Before update, the workflow records the service's previous task-definition ARN. After update
it waits for ECS service stability and runs external requests against the configured base URL:

- `GET /health`: exact healthy service payload
- `GET /api/v1/model`: `howreliable-api-1.0`, registry
  `howreliable-model-registry-1.0`, and bundle `howreliable-rf-2022-cutoff-v1`
- `GET /api/v1/cohorts?limit=1&offset=0`: total exactly 8,416

HTTPS is required unless the explicit temporary-HTTP variable is set. No representative
prediction is hard-coded because representative result artifacts are intentionally ignored
and unavailable on a clean runner. The metadata and cohort-count smoke is therefore the
scientifically honest live boundary.

Any failure after the service update—forward stability or smoke—triggers an update back to
the captured previous task definition and a bounded rollback stability wait. Rollback
restores only the previous application task definition; it cannot restore an S3 bundle or
AWS infrastructure, and there is no database state. The script exits with the original
failure status even when rollback succeeds, so rollback never turns a failed deployment
green.

## First live deployment prerequisites

Before manual execution, verify all of the following exist and are configured:

1. AWS account and GitHub Actions access.
2. Validated canonical bundle already published to the private S3 prefix.
3. Existing immutable-tag ECR repository.
4. Existing ECS cluster and service.
5. Existing ALB and target group.
6. Existing task and execution roles.
7. Existing VPC subnets and security groups referenced by the service.
8. Existing `/howreliable/api` CloudWatch log group and alarms.
9. GitHub OIDC provider and the narrowly trusted deployment role.
10. GitHub `production` Environment and recommended protection rules.
11. All listed repository/environment variables.
12. API base URL and an explicit understanding of HTTPS versus temporary HTTP status.
13. Successful CI/deployment-critical validation for the selected commit.

For first live validation, manually dispatch the full merged SHA, approve the protected
environment, observe the safe milestones, verify the recorded image digest/task revision,
and independently confirm ECS target health and CloudWatch startup/request events. A real
deployment record may be captured after success; none is fabricated during local validation.

## Known limitations and Phase 7C handoff

No live OIDC exchange, ECR collision/push, AWS CLI operation, ECS stability wait, rollback,
or external smoke has been exercised. Environment protections are documentation, not
repository-managed settings. The locally built production image was about 11.56 GB, so the
existing Linux PyTorch wheel and CUDA-related transitive packages make builds expensive;
dependency/image optimization is deferred and Phase 7B does not change established package/
model architecture. The local read-only canonical-bundle container smoke passed with all
8,416 cohorts, one representative frozen prediction, and explanation reconstruction error of
approximately 5.6e-17. It does not validate the production S3 backend or AWS network path.

Phase 7C now codifies the exact assumed resources: ECR repository, private S3 bucket/prefix,
ECS cluster/service/task-definition family, task role, execution role, ALB, target group,
subnets/security groups, CloudWatch log group/alarms, and GitHub OIDC provider/deployment
role. Its outputs map directly to every Phase 7B production variable. Terraform owns the
foundation while this workflow remains the sole application-revision owner; see
[Terraform](terraform.md). Neither layer has run against live AWS.
