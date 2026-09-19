# Phase 6C monitoring

Phase 6C implements operational observability contract `howreliable-monitoring-1.0` for
the existing `howreliable-deployment-1.0` ECS Fargate design. It does not change the model,
prediction contract, routes, or scientific interpretation. Monitoring implementation and
local/static validation are complete; live CloudWatch validation was not executed because
there is no live AWS deployment.

## Architecture and structured logs

The FastAPI process writes application logs only to stdout/stderr. In ECS,
`HOWRELIABLE_LOG_FORMAT=json` produces one compact JSON object per line and the task
definition's `awslogs` driver sends those streams to CloudWatch Logs. Application code does
not call a CloudWatch API. Local development defaults to readable `text`; `json` is also
available locally. Any other format fails clearly during settings validation/startup.

Every JSON record has `timestamp` (UTC with millisecond precision and `Z`), `level`,
`logger`, `event`, and `message`. A bounded allowlist supplies event-specific fields such as
API/deployment versions, backend and bundle identifiers, load/request duration, method,
route template, status, request ID, model/registry identifiers, cohort count, lifecycle
stage, and error category. Exception records include only the exception type, not exception
text or a serialized traceback. JSON serialization rejects NaN and Infinity.

The stable event taxonomy is:

- `service_starting`
- `artifact_backend_selected`
- `bundle_load_started`
- `bundle_load_succeeded`
- `bundle_load_failed`
- `service_ready`
- `request_completed`
- `request_failed`

Startup identifies the safe deployment/API/backend/bundle context, measures bundle loading,
and reports the registry, frozen model, and 8,416 supported cohorts when ready. A load error
emits `bundle_load_failed` with backend, bundle, lifecycle stage, and exception category,
then preserves fail-closed startup behavior.

## Request correlation and data minimization

The API accepts `X-Request-ID` only when it is 1–64 characters, starts alphanumerically,
and otherwise contains only ASCII letters, digits, `.`, `_`, `:`, or `-`. It generates a
lowercase hexadecimal UUID when the header is absent or invalid. The selected ID is returned
in `X-Request-ID` and included in completion/failure logs.

Request logs contain method, matched route template (not the cohort path value), status,
monotonic duration in milliseconds, and request ID. Successful `GET /health` records are
DEBUG to avoid routine ALB health-check noise; failures remain visible. Unexpected errors
are correlated and logged server-side while the client receives the existing sanitized
`INTERNAL_ERROR` response without exception details.

Logs do not contain request bodies, arbitrary headers, cookies, authorization values, AWS
credentials/tokens, cohort IDs, feature vectors, prediction probabilities/classes,
contributors, explanations, manifests, model bytes, or future ground truth. The formatter
ignores arbitrary extra fields rather than emitting unbounded dictionaries.

## CloudWatch Logs and IAM

The deterministic log-group specification is
`infrastructure/aws/monitoring/cloudwatch-log-group.json`:

- log group: `/howreliable/api`
- stream prefix: `ecs`
- retention: 14 days
- task region: explicit `<AWS_REGION>` placeholder

The ECS task execution role needs `logs:CreateLogStream` and `logs:PutLogEvents` (normally
through the standard ECS task execution policy) for platform log delivery. The application
task role remains limited to private-S3 bundle reads and receives no CloudWatch Logs
permission. Before registering the task definition, create the group and retention policy:

```console
aws logs create-log-group --log-group-name /howreliable/api --region <region>
aws logs put-retention-policy --log-group-name /howreliable/api --retention-in-days 14 --region <region>
```

## Native metrics and alarms

`infrastructure/aws/monitoring/cloudwatch-alarms.json` defines exactly four initial alarms.
Replace only its cluster, load-balancer, and target-group placeholders before turning each
entry under `alarms` into an individual `aws cloudwatch put-metric-alarm` input.

| Alarm | Metric and statistic | Trigger | Missing data |
|---|---|---|---|
| `howreliable-ecs-cpu-high` | ECS `CPUUtilization`, average | at least 2 of 3 five-minute periods at or above 80% | not breaching |
| `howreliable-ecs-memory-high` | ECS `MemoryUtilization`, average | at least 2 of 3 five-minute periods at or above 80% | not breaching |
| `howreliable-alb-unhealthy-target` | ALB `UnHealthyHostCount`, maximum | above zero for 1 of 2 one-minute periods | breaching |
| `howreliable-alb-target-5xx` | ALB `HTTPCode_Target_5XX_Count`, sum | at least 5 target-generated 5xx responses in five minutes | not breaching |

The CPU threshold requires sustained saturation instead of reacting to one transient sample.
The memory threshold is provisional: local observations were about 439 MiB steady and 525
MiB peak in a 1,024 MiB task, not cloud production measurements. Both thresholds must be
reviewed after live workload history exists. Desired count is one, so any unhealthy target
can make the service unavailable. The 5xx alarm uses a count because percentages are unstable
and misleading at the expected low traffic volume; it may need adjustment with real traffic.
Alarm actions/notification destinations are intentionally deployment-operator choices.

Standard ECS service metrics do not directly provide a desired-versus-running task-count
comparison. Phase 6C therefore does not invent a custom metric, Container Insights,
EventBridge, or Lambda. Operators must inspect the ECS service deployment/events and desired
versus running counts; a later operational phase may add a purpose-built signal. No alarm
monitors model accuracy, probabilities, class mix, feature/model drift, complaint activity,
or explanation distributions because there is no live ground-truth pipeline.

## Contract, validation, and troubleshooting

The ignored deterministic artifact `artifacts/cloud/monitoring-contract.json` links this
contract to deployment version/checksum, API version, ECS Fargate, JSON delivery, log group,
retention, events, request IDs, the four alarms, and explicit task-count/ML-monitoring
exclusions. Regeneration is byte-identical for the same explicit UTC timestamp and inputs.

Local/static validation covers formatter output, lifecycle and request events, request-ID
safety, error sanitization, prediction/secret exclusion, task-definition log configuration,
alarm dimensions, retention, contract regeneration, and the unchanged API. It does not prove
that CloudWatch accepted logs or alarms. Optional live acceptance should confirm startup and
request events in `/howreliable/api`, healthy target and ECS CPU/memory metrics, alarm
creation, and a correlated request ID—without recording credentials or payloads.

For missing logs, verify `HOWRELIABLE_LOG_FORMAT=json`, task execution-role permissions,
the exact group/region, retention, and stopped-task events. For a task that never becomes
healthy, inspect `bundle_load_failed`, S3 task-role access, bundle checksums, and ALB target
health. For elevated 5xx, correlate `request_failed` by request ID while keeping client
responses sanitized. For resource alarms, compare ECS service metrics with task restarts and
review thresholds only after enough live data exists.

Known limitations are the absent live AWS validation, one-task availability, provisional
thresholds, no notification destinations/dashboard/task-count signal, and no distributed
tracing, APM, Prometheus, or ML-quality monitoring.

## Phase 7A implementation

Phase 7A CI now runs these checks without requiring AWS credentials or a live service:

```console
python -m pytest
python -m ruff check .
python -m mypy
python -c "import howreliable; print(howreliable.__version__)"
python -m pip check
```

CI also exercises deterministic monitoring-contract regeneration, monitoring static
validators, and Docker configuration. The exact API/frozen-probability/explanation/limitation
tests run in the full-artifact gate because ignored model/data products are unavailable in a
clean checkout. Tests never call live AWS; deployment and CloudWatch acceptance remain
separately authorized manual work. See [continuous integration](ci.md).

Phase 7B preserves this monitoring configuration in every rendered task revision. CD does
not mutate log groups or alarms. Live deployment acceptance should confirm startup and smoke
events in `/howreliable/api`; this remains unexecuted. See [continuous deployment](cd.md).

Phase 7C Terraform now declares the exact `/howreliable/api` group with 14-day retention and
all four Phase 6C alarms using live ECS/ALB dimensions. It deliberately configures no alarm
actions because no notification destination has been chosen. Terraform was not applied, so
CloudWatch delivery, metrics, and alarms remain unexecuted. See [Terraform](terraform.md).
