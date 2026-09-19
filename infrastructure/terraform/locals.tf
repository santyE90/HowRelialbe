locals {
  name_prefix = "${var.project_name}-${var.environment}"

  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
  }

  container_name = "howreliable-api"
  task_family    = "howreliable-api"
  log_group_name = "/howreliable/api"

  github_oidc_provider_arn = var.create_github_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : var.existing_github_oidc_provider_arn

  bootstrap_server = <<-PYTHON
    from http.server import BaseHTTPRequestHandler, HTTPServer
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'{"status":"ok","service":"howreliable-bootstrap"}'
            status = 200 if self.path == "/health" else 404
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, format, *args):
            return
    HTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
  PYTHON
}

check "github_oidc_provider_configuration" {
  assert {
    condition = var.create_github_oidc_provider || (
      var.existing_github_oidc_provider_arn != null &&
      can(regex("^arn:[^:]+:iam::[0-9]{12}:oidc-provider/token\\.actions\\.githubusercontent\\.com$", var.existing_github_oidc_provider_arn))
    )
    error_message = "Supply the existing GitHub OIDC provider ARN when creation is disabled."
  }
}
