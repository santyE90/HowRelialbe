variable "aws_region" {
  description = "AWS region for all regional HowReliable resources."
  type        = string
  default     = "ca-central-1"
}

variable "project_name" {
  description = "Short deterministic resource-name prefix."
  type        = string
  default     = "howreliable"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,20}$", var.project_name))
    error_message = "project_name must be a short lowercase AWS-safe name."
  }
}

variable "environment" {
  description = "Single research-service environment."
  type        = string
  default     = "production"

  validation {
    condition     = var.environment == "production"
    error_message = "Phase 7C supports the production environment only."
  }
}

variable "vpc_cidr" {
  description = "RFC1918 CIDR for the service VPC."
  type        = string
  default     = "10.42.0.0/16"
}

variable "public_subnet_cidrs" {
  description = "Two non-overlapping public-subnet CIDRs."
  type        = list(string)
  default     = ["10.42.0.0/24", "10.42.1.0/24"]

  validation {
    condition     = length(var.public_subnet_cidrs) == 2 && length(distinct(var.public_subnet_cidrs)) == 2
    error_message = "Exactly two distinct public subnet CIDRs are required."
  }
}

variable "artifact_bucket_name" {
  description = "Globally unique private bucket name for the canonical model bundle."
  type        = string

  validation {
    condition     = length(var.artifact_bucket_name) >= 3 && length(var.artifact_bucket_name) <= 63
    error_message = "artifact_bucket_name must be a valid-length S3 bucket name."
  }
}

variable "artifact_prefix" {
  description = "Normalized S3 key prefix consumed by the Phase 6A store."
  type        = string
  default     = "howreliable"

  validation {
    condition     = can(regex("^[A-Za-z0-9][A-Za-z0-9._/-]*[A-Za-z0-9]$", var.artifact_prefix))
    error_message = "artifact_prefix must be a nonempty relative key prefix without edge slashes."
  }
}

variable "bootstrap_image_uri" {
  description = "Digest-pinned image containing Python, used only by Terraform's healthy bootstrap task."
  type        = string

  validation {
    condition     = can(regex("@sha256:[0-9a-f]{64}$", var.bootstrap_image_uri))
    error_message = "bootstrap_image_uri must end in an immutable sha256 digest."
  }
}

variable "certificate_arn" {
  description = "Optional existing ACM certificate ARN for the HTTPS listener."
  type        = string
  default     = null
  nullable    = true
}

variable "redirect_http_to_https" {
  description = "Redirect HTTP to HTTPS when certificate_arn is supplied."
  type        = bool
  default     = true
}

variable "create_github_oidc_provider" {
  description = "Create the account-level GitHub OIDC provider; false reuses existing_github_oidc_provider_arn."
  type        = bool
  default     = true
}

variable "existing_github_oidc_provider_arn" {
  description = "Existing account-level GitHub OIDC provider ARN when creation is disabled."
  type        = string
  default     = null
  nullable    = true
}

variable "github_owner" {
  description = "Exact GitHub repository owner trusted for production deployment."
  type        = string
}

variable "github_repository" {
  description = "Exact GitHub repository name trusted for production deployment."
  type        = string
}
