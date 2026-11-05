output "url" { value = "http://${aws_lb.main.dns_name}" }
output "api_ecr_repository" { value = aws_ecr_repository.api.repository_url }
output "web_ecr_repository" { value = aws_ecr_repository.web.repository_url }
output "ecs_cluster" { value = aws_ecs_cluster.main.name }
output "github_deploy_role_arn" { value = aws_iam_role.github_deploy.arn }
output "app_secret_arn" { value = aws_secretsmanager_secret.app.arn }
