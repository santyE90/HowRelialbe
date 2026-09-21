# Phase 7C Terraform

Phase 7C implements infrastructure contract `howreliable-terraform-1.0` in
`infrastructure/terraform/`. It makes the Phase 6A storage, Phase 6B runtime, Phase 6C
monitoring, and Phase 7B deployment prerequisites reviewable and reproducible. It does not
deploy application versions, publish the model bundle, train a model, or manage GitHub
repository settings.

Implementation and offline validation are complete. Terraform CLI validation, a live AWS
plan, and apply were not executed because Terraform is not installed locally and no live AWS
operation was authorized.

## Ownership boundary

Terraform owns the infrastructure lifecycle:

- one VPC, two public subnets, internet routing, and security groups;
- private S3 artifact bucket and private ECR repository;
- ECS cluster, healthy bootstrap task revision, and service foundation;
- ALB, target group, HTTP listener, and optional HTTPS listener;
- task, execution, and GitHub deployment roles;
- optional GitHub Actions OIDC provider;
- CloudWatch log group and four established alarms.

Phase 7B CD remains the sole application-deployment mechanism. It builds and pushes one
immutable application image, registers the application task revision, updates the existing
service, waits, smokes, and rolls back. Terraform neither runs Docker nor pushes ECR images.
Phase 6A bundle publication remains a separate controlled operation; Terraform creates the
bucket but uploads no model or data object.

## Contract and tool versions

The ignored deterministic artifact is
`artifacts/terraform/terraform-contract.json`. It binds Terraform to the frozen S3,
deployment, monitoring, CD, registry, bundle, compute, network, and ownership contracts.
Given the same explicit UTC timestamp, regeneration is byte-identical.

- Terraform: `>= 1.14.0, < 1.17.0`
- AWS provider: `~> 6.0`
- provider source: `hashicorp/aws`
- state backend: local
- environment: `production`

There is no GitHub provider. GitHub's `production` Environment, reviewer/branch protection,
and repository variables remain manual repository administration.

## Resource graph

```text
VPC + internet gateway
  -> two public subnets + public route table
  -> ALB security group -> ALB -> target group -> HTTP/optional HTTPS listeners
  -> task security group -> ECS cluster/service -> bootstrap task definition

private S3 bucket -> prefix-scoped task-role policy -> application task role
private ECR repository -> exact deploy-role policy
CloudWatch log group -> bootstrap/application task awslogs configuration
ECS/ALB identifiers -> four CloudWatch metric alarms
GitHub OIDC provider (created or reused) -> exact production deploy role
```

The roles are deliberately distinct: the application task role reads the bundle, the ECS
execution role pulls images and writes logs, and the GitHub role deploys application
revisions.

## Networking and security groups

The VPC defaults to `10.42.0.0/16`. Two configurable, non-overlapping `/24` public subnets
are placed in the first two available AZs returned by AWS, avoiding account-specific AZ
letters. An internet gateway and one public route table provide egress.

The ALB is public. Its security group accepts HTTP 80 and, when a certificate is configured,
HTTPS 443. Its outbound rule reaches only task TCP 8000. The task security group accepts TCP
8000 only by security-group reference from the ALB. There is no `0.0.0.0/0` ingress to the
task port. Task outbound is initially broad for S3, ECR, and AWS API access.

Fargate tasks run in the public subnets with public IP assignment. A public IP supplies
outbound connectivity, but the task is not directly publicly reachable because its security
group has no public ingress. This deliberately avoids NAT Gateway fixed cost for the small
research service. A later hardened design could use private subnets and VPC endpoints/NAT.

## ALB and HTTPS

The target group uses target type `ip`, HTTP port 8000, and `/health` with a strict `200`
matcher. The HTTP listener forwards to it by default. Supplying an existing regional ACM
certificate ARN creates an HTTPS listener with a modern TLS policy. HTTP redirects to HTTPS
by default when that certificate exists; the redirect is configurable. Terraform creates no
domain, DNS record, or certificate. Without a certificate, the output URL is explicitly
temporary HTTP and Phase 7B receives `HOWRELIABLE_ALLOW_HTTP_SMOKE=true`.

## ECR and the bootstrap task

The private ECR repository uses immutable tags, scan-on-push, and SSE-S3 encryption. Its
lifecycle expires untagged images after 14 days and retains the 50 most recent `sha-` images,
leaving substantial rollback/debug history without unlimited growth.

An ECS service cannot be created without a task definition, while Phase 7B cannot deploy
until its target infrastructure exists. Terraform therefore registers one narrow bootstrap
revision using required `bootstrap_image_uri`. The URI must be digest-pinned and its image
must contain Python. A built-in command serves a minimal `/health` response on port 8000; it
does not load the model or impersonate the production API. An official digest-pinned Python
slim image is a suitable operator-selected input.

The service uses Fargate, `awsvpc`, desired count 1, 512 CPU units, 1,024 MiB memory, both
public subnets, task security group, public IP assignment, ALB target group, and a 180-second
health grace period. There is no autoscaling or EC2 capacity provider.

The ECS deployment circuit breaker with AWS rollback is an infrastructure-level safety net.
Phase 7B remains responsible for observing failure, reporting it, and explicitly restoring
the previously captured application task revision. Reapplying Terraform will not undo a CD
deployment because the service lifecycle ignores only `task_definition`; desired count,
network, load balancer, and other service settings remain Terraform-managed.

## S3 artifact protection

The caller supplies a globally unique production bucket name and configurable canonical
prefix. Terraform enables all four public-access blocks, bucket-owner-enforced ownership,
SSE-S3 (`AES256`), and versioning. There is no public bucket policy and `force_destroy` is
false. `prevent_destroy` protects the production bucket, so ordinary `terraform destroy`
cannot remove it.

Intentional bucket retirement requires an external backup/retention decision, explicit code
review removing `prevent_destroy`, and separate handling of versioned objects before a
targeted destruction. Terraform never uploads or deletes the canonical bundle.

The task role permits prefix-scoped `s3:GetObject` plus conditioned `s3:ListBucket`. It has
no write/delete/wildcard S3 action. Bundle publication needs a separately authorized Phase 6A
operator identity that this configuration intentionally does not create.

## IAM and GitHub OIDC

The ECS execution role uses the AWS-managed task-execution policy for private ECR pulls and
`awslogs`. It receives no application S3 access.

The GitHub deployment role trusts only:

- issuer `https://token.actions.githubusercontent.com`;
- audience `sts.amazonaws.com`;
- subject `repo:<owner>@<owner-id>/<repository>@<repository-id>:environment:production`.

The numeric owner and repository IDs are GitHub's immutable identifiers. Keeping both names
and IDs in the subject prevents a renamed or transferred namespace from silently retaining
deployment access.

By default Terraform creates the account-level GitHub OIDC provider. If one already exists,
set `create_github_oidc_provider=false` and supply its exact ARN. This avoids duplicate
account-wide provider conflicts.

The deploy policy permits ECR authorization and repository-scoped upload, ECS describe and
task registration, update of the exact service, and `iam:PassRole` for only the application
task and execution roles. `PassRole` is conditioned on `ecs-tasks.amazonaws.com`. It grants
no S3 write/delete and no `ecs:*`, `ecr:*`, `iam:*`, AdministratorAccess, or PowerUserAccess.

## Monitoring

Terraform creates `/howreliable/api` with 14-day retention and exactly the Phase 6C alarms:

| Alarm | Contract |
|---|---|
| `howreliable-ecs-cpu-high` | average CPU >=80%, 2 of 3 five-minute periods |
| `howreliable-ecs-memory-high` | average memory >=80%, 2 of 3 five-minute periods |
| `howreliable-alb-unhealthy-target` | maximum unhealthy targets >0, 1 of 2 one-minute periods |
| `howreliable-alb-target-5xx` | sum >=5 in one five-minute period |

No alarm actions are configured because no SNS topic, email address, or incident destination
has been selected. There is no ML drift/quality alarm.

## Variables and outputs

Copy `terraform.tfvars.example` to ignored `terraform.tfvars` and set at least the globally
unique bucket, digest-pinned bootstrap image, GitHub owner and immutable owner ID, and GitHub
repository and immutable repository ID. Region, project/environment names, CIDRs, prefix,
certificate behavior, and OIDC creation/reuse are explicit variables.

The `github_environment_variables` output maps directly to Phase 7B:

| Terraform value | GitHub production variable |
|---|---|
| deploy-role ARN | `HOWRELIABLE_DEPLOY_ROLE_ARN` |
| region | `HOWRELIABLE_AWS_REGION` |
| ECR repository name | `HOWRELIABLE_ECR_REPOSITORY` |
| cluster name | `HOWRELIABLE_ECS_CLUSTER` |
| service name | `HOWRELIABLE_ECS_SERVICE` |
| HTTP/HTTPS ALB URL | `HOWRELIABLE_API_BASE_URL` |
| bucket name | `HOWRELIABLE_S3_BUCKET` |
| canonical prefix | `HOWRELIABLE_S3_PREFIX` |
| task-role ARN | `HOWRELIABLE_TASK_ROLE_ARN` |
| execution-role ARN | `HOWRELIABLE_EXECUTION_ROLE_ARN` |
| temporary HTTP flag | `HOWRELIABLE_ALLOW_HTTP_SMOKE` |

Additional outputs include ECR URL, task family, ALB DNS name, and CloudWatch log group. No
output is a secret.

## State and commands

Phase 7C intentionally uses local state: no backend block bootstraps resources it depends on.
Local state contains infrastructure identifiers and metadata and must be protected even
though the application has no secrets. `.terraform/`, `terraform.tfstate*`, crash logs, and
environment-specific variable files are git-ignored. The provider lock file is committed and
currently resolves the allowed AWS provider range to 6.65.0. A locked/encrypted remote backend
with state locking is a future operational improvement.

From `infrastructure/terraform/`:

```console
terraform init
terraform fmt -check -recursive
terraform validate
terraform plan -out=howreliable.tfplan
terraform apply howreliable.tfplan
```

Review the plan before apply. Do not commit the plan or state. None of these live commands was
run during Phase 7C. In particular, no billable resource was created.

## Cost and operational limitations

Billable categories include the ALB, one Fargate task, public IPv4 addressing, ECR storage
and scanning, S3 storage/requests, CloudWatch logs/metrics, and applicable data transfer.
Avoiding NAT Gateway reduces fixed networking cost. Exact pricing is region- and usage-
dependent and must be reviewed at deployment time.

Desired count 1 is not highly available. Tasks have public-IP egress. Temporary HTTP is not
appropriate for public production use. Alarm notifications, DNS, ACM certificate issuance,
remote state, WAF, autoscaling, VPC endpoints, backup policy, and live acceptance remain
future operations.

The validated production image is approximately 11.56 GB because the current runtime pulls
PyTorch and CUDA-related packages. This increases ECR storage, image pull time, and deployment
startup time. Dependency/image optimization is intentionally outside Phase 7C.

## First live deployment sequence

1. Install a compatible Terraform CLI and copy/configure `terraform.tfvars`.
2. Run `terraform init`, formatting, validation, and a saved plan.
3. Review the complete plan, then explicitly apply it.
4. Publish the canonical bundle to the Terraform-created bucket/prefix with Phase 6A tooling.
5. Configure GitHub's protected `production` Environment from the Terraform outputs.
6. Push the repository and ensure CI passes for the intended commit.
7. Manually dispatch Phase 7B with that full SHA already merged to `main`.
8. CD builds and pushes the immutable image, registers a task revision, and updates ECS.
9. CD waits for stability and performs its remote health/model/cohort smoke.
10. Inspect ECS/ALB health plus `/howreliable/api` logs and the four alarms.

Live plan/apply, model publication, application deployment, S3-backed startup, remote smoke,
and CloudWatch acceptance remain unexecuted. The next milestone is Phase 8A; no Phase 8 work
is implemented here.
