output "aws_region" {
  value = var.aws_region
}

output "ecr_repository_name" {
  value = aws_ecr_repository.api.name
}

output "ecr_repository_url" {
  value = aws_ecr_repository.api.repository_url
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.api.name
}

output "ecs_service_name" {
  value = aws_ecs_service.api.name
}

output "ecs_task_family" {
  value = local.task_family
}

output "task_role_arn" {
  value = aws_iam_role.task.arn
}

output "execution_role_arn" {
  value = aws_iam_role.execution.arn
}

output "github_deploy_role_arn" {
  value = aws_iam_role.github_deploy.arn
}

output "s3_bucket_name" {
  value = aws_s3_bucket.artifacts.id
}

output "s3_prefix" {
  value = var.artifact_prefix
}

output "alb_dns_name" {
  value = aws_lb.api.dns_name
}

output "api_base_url" {
  value = var.certificate_arn == null ? "http://${aws_lb.api.dns_name}" : "https://${aws_lb.api.dns_name}"
}

output "allow_http_smoke" {
  value = var.certificate_arn == null ? "true" : "false"
}

output "cloudwatch_log_group" {
  value = aws_cloudwatch_log_group.api.name
}

output "github_environment_variables" {
  description = "Direct Phase 7B production-environment variable handoff; contains no secrets."
  value = {
    HOWRELIABLE_DEPLOY_ROLE_ARN    = aws_iam_role.github_deploy.arn
    HOWRELIABLE_AWS_REGION         = var.aws_region
    HOWRELIABLE_ECR_REPOSITORY     = aws_ecr_repository.api.name
    HOWRELIABLE_ECS_CLUSTER        = aws_ecs_cluster.api.name
    HOWRELIABLE_ECS_SERVICE        = aws_ecs_service.api.name
    HOWRELIABLE_API_BASE_URL       = var.certificate_arn == null ? "http://${aws_lb.api.dns_name}" : "https://${aws_lb.api.dns_name}"
    HOWRELIABLE_S3_BUCKET          = aws_s3_bucket.artifacts.id
    HOWRELIABLE_S3_PREFIX          = var.artifact_prefix
    HOWRELIABLE_TASK_ROLE_ARN      = aws_iam_role.task.arn
    HOWRELIABLE_EXECUTION_ROLE_ARN = aws_iam_role.execution.arn
    HOWRELIABLE_ALLOW_HTTP_SMOKE   = var.certificate_arn == null ? "true" : "false"
  }
}
