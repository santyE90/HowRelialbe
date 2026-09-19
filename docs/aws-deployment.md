# Phase 6B AWS deployment

## Purpose and compute decision

Phase 6B implements deployment contract `howreliable-deployment-1.0`. It packages the
unchanged `howreliable-api-1.0` FastAPI service for Amazon ECS Fargate and defines a small
manual deployment through ECR, ECS, an Application Load Balancer, and the Phase 6A private
S3 bundle. It does not retrain or modify the model, create monitoring, or implement
Terraform/CI/CD.

The deterministic artifact is `artifacts/cloud/deployment-contract.json`, with SHA-256
`c4e6806743421eb1e0feebf27522aec5caa1e9fc87675daaee18c1e5b30b71e4`.

Fargate was selected because the service needs a long-running container, startup-time S3
retrieval through an IAM task role, ordinary HTTP health checks, and no EC2 host
administration. EKS, Lambda/API Gateway, Elastic Beanstalk, SageMaker endpoints, and managed
EC2 instances add an unsuitable execution model or operational scope.

```text
versioned image -> ECR -> ECS cluster/service -> one Fargate task
                                              -> private S3 bundle (task role)
public client -> ALB -> target group -> container port 8000
```

## Container image

The multi-stage `Dockerfile` pins `python:3.12.11-slim-bookworm`, builds an isolated virtual
environment, installs the package without development extras, and copies only that runtime
environment into the final stage. UID/GID 10001 runs without root or a login shell.
`PYTHONDONTWRITEBYTECODE` avoids runtime cache writes and `PYTHONUNBUFFERED` sends Uvicorn
logs to stdout/stderr.

The image exposes port 8000 and starts the one existing application factory:

```text
uvicorn howreliable.api.app:create_app --factory --host 0.0.0.0 --port 8000
```

Its health check requests `GET /health`; it never performs inference. Application factory
creation first loads and validates the complete model bundle, so a running process with a
healthy route has passed startup validation. The same image supports local or S3 artifact
configuration. `.dockerignore` excludes local data, artifacts, models, credentials,
environments, tests, notebooks, caches, and repository metadata. Consequently, the
production image never contains the frozen inference bundle or AWS credentials.

Local mode requires intentionally mounting the repository artifact/data layout at `/app`
and selecting `HOWRELIABLE_ARTIFACT_BACKEND=local`. The AWS task always selects `s3`; these
are configurations of one image, not separate builds.

```console
docker run --rm --publish 8000:8000 --env HOWRELIABLE_ARTIFACT_BACKEND=local --mount type=bind,source=<repository-root>,target=/app,readonly howreliable-api:1.0.0
```

After startup, request `/health`, `/api/v1/model`, the cohort collection, and a representative
cohort result. The bind mount is a local smoke mechanism only and is absent from Fargate.

## ECR and immutable image tags

The ECR repository is `howreliable-api`. The phase tag is `1.0.0`; a full Git commit SHA is
also acceptable. ECS must reference an explicit version/SHA tag and should enable ECR tag
immutability. It must never deploy `latest`. No CI/CD is created in this phase.

After substituting the region and account variables in a shell:

```console
aws ecr create-repository --repository-name howreliable-api --image-tag-mutability IMMUTABLE --region <region>
aws ecr get-login-password --region <region> | docker login --username AWS --password-stdin <account>.dkr.ecr.<region>.amazonaws.com
docker build --pull --tag howreliable-api:1.0.0 .
docker tag howreliable-api:1.0.0 <account>.dkr.ecr.<region>.amazonaws.com/howreliable-api:1.0.0
docker push <account>.dkr.ecr.<region>.amazonaws.com/howreliable-api:1.0.0
```

Repository creation is a one-time manual prerequisite and was not executed by automated
validation.

## ECS task, roles, and environment

`infrastructure/aws/ecs-task-definition.json` specifies Linux/X86-64 Fargate, `awsvpc`, 512
CPU units (0.5 vCPU), 1,024 MiB memory, one essential container, port 8000, and a 120-second
health start period. Local measurements loaded 8,416 cohorts in approximately 0.6–1.0
seconds, used about 439 MiB steady resident memory, and reached roughly 525 MiB peak resident
memory. One GiB therefore leaves a practical initial margin without large overprovisioning;
production observation belongs to Phase 6C.

The task environment is:

```text
HOWRELIABLE_ENVIRONMENT=aws
HOWRELIABLE_LOG_LEVEL=INFO
HOWRELIABLE_ARTIFACT_BACKEND=s3
HOWRELIABLE_S3_BUCKET=<private-bucket>
HOWRELIABLE_S3_PREFIX=<prefix>
HOWRELIABLE_AWS_REGION=<region>
HOWRELIABLE_MODEL_BUNDLE_ID=howreliable-rf-2022-cutoff-v1
```

No access key, secret key, or session token is supplied. Boto3 obtains short-lived task-role
credentials through its standard ECS credential provider.

The two IAM roles are deliberately distinct:

- The **task execution role** is assumed by the ECS agent to pull the private ECR image and
  perform platform operations. Attach AWS's managed `AmazonECSTaskExecutionRolePolicy`; do
  not give this role application S3 access.
- The **application task role** is used by HowReliable? inside the container. Attach the
  existing `infrastructure/iam/howreliable-s3-reader-policy.json`, after substituting its
  bucket/prefix placeholders. It grants only prefix-scoped `s3:GetObject` and constrained
  `s3:ListBucket`, with no write/delete/administration action.

Both roles may use `infrastructure/aws/ecs-tasks-trust-policy.json` as their trust policy.
The ARNs replace the distinct placeholders in the task definition.

## Service, ALB, networking, and HTTPS

`infrastructure/aws/ecs-service.json` defines desired count 1, Fargate launch type, one
explicit task-definition revision, one target group, and no autoscaling. This is intentionally
a low-cost research deployment with limited availability during task replacement or an
Availability Zone/service failure.

For an initial manual deployment, use two public subnets in an existing/default VPC and
assign the task a public IP for outbound S3/ECR access. This avoids NAT-gateway cost and
private-subnet complexity. No S3 VPC endpoint is required initially. The task security group
accepts TCP 8000 only from the ALB security group. The ALB security group accepts only its
public listener port and allows traffic to the task security group. The target group must use
target type `ip`, protocol HTTP, port 8000, and health path `/health`.

A temporary smoke deployment may expose an HTTP listener on port 80. Production-quality
public access requires a real domain and ACM certificate on an HTTPS/443 ALB listener, with
HTTP redirected to HTTPS. Phase 6B does not invent a domain or certificate.

## Manual deployment procedure

1. Publish and validate the exact Phase 6A bundle in the private S3 prefix.
2. Build and push the explicitly tagged image to immutable ECR as above.
3. Create the execution and task roles with the trust policy; attach the managed execution
   policy and the substituted S3 reader policy to the correct roles.
4. Create or select an ECS cluster and two suitable VPC subnets.
5. Create the ALB/task security groups, ALB, `ip` target group, and HTTP smoke or HTTPS
   listener. Set the target health path to `/health`.
6. Replace every angle-bracket placeholder in the task/service JSON. Do not commit real
   account IDs, bucket names, or resource IDs.
7. Register and capture the exact returned task-definition revision:

   ```console
   aws ecs register-task-definition --cli-input-json file://infrastructure/aws/ecs-task-definition.json --region <region>
   ```

8. Put that revision in the service file. For first deployment run:

   ```console
   aws ecs create-service --cli-input-json file://infrastructure/aws/ecs-service.json --region <region>
   ```

   For a later explicit image/task revision run:

   ```console
   aws ecs update-service --cluster <cluster> --service howreliable-api --task-definition howreliable-api:<revision> --force-new-deployment --region <region>
   ```

9. Wait for service stability and confirm one target is healthy. Inspect stopped-task reasons
   if startup fails; never switch silently to a local bundle.

## Validation and failure behavior

For live acceptance, verify the ECR image tag, RUNNING task and expected roles, healthy ALB
target, `/health`, `/api/v1/model`, cohort total 8,416, one representative frozen
probability, explanation reconstruction, and limitation flags. Record only safe resource
identifiers.

If S3 is unreachable, denied, incomplete, corrupted, or semantically incompatible, factory
creation fails and the prediction-capable process does not become healthy. There is no local
fallback or alternate bundle selection. After successful startup, requests use the validated
in-memory bundle and do not access S3 per request.

Docker was unavailable in the Phase 6B validation environment, so Docker build, local
container smoke, and real-S3 container smoke were not executed. Static Docker/task/service
validation and the host API regression suite were completed. No AWS bucket/account/profile
or authorized infrastructure prerequisites were configured, so live deployment was also not
executed. These statuses must not be interpreted as a running service.

## Limitations and Phase 6C handoff

Phase 6B does not provide autoscaling, multi-task availability, private-subnet/NAT design,
VPC endpoints, domain/TLS certificate, deployment automation, image scanning policy,
rollback automation, secrets management, dashboards, traces, or alerts.

Phase 6C now provides structured stdout/stderr logs, ECS `awslogs` routing to
`/howreliable/api`, 14-day retention, and four AWS-native metric alarm specifications. It
preserves the separate execution-role log-delivery and task-role S3 responsibilities. These
changes are locally/statically validated only; no live CloudWatch delivery or alarms are
claimed. See [monitoring](monitoring.md).
