# --- PostgreSQL (RDS) -------------------------------------------------------------
resource "random_password" "db" {
  length  = 32
  special = false
}
resource "random_password" "neo4j" {
  length  = 32
  special = false
}

resource "aws_db_subnet_group" "main" {
  name       = "${var.name}-db"
  subnet_ids = aws_subnet.data[*].id
}

resource "aws_db_instance" "postgres" {
  identifier              = "${var.name}-${var.environment}"
  engine                  = "postgres"
  engine_version          = "16"
  instance_class          = var.db_instance_class
  allocated_storage       = 20
  storage_type            = "gp3"
  db_name                 = "caretwin"
  username                = "caretwin"
  password                = random_password.db.result
  db_subnet_group_name    = aws_db_subnet_group.main.name
  vpc_security_group_ids  = [aws_security_group.data.id]
  backup_retention_period = 1
  skip_final_snapshot     = true
  publicly_accessible     = false
  storage_encrypted       = true
}

resource "aws_secretsmanager_secret" "app" { name = "${var.name}/${var.environment}/app" }

resource "aws_secretsmanager_secret_version" "app" {
  secret_id = aws_secretsmanager_secret.app.id
  secret_string = jsonencode({
    CARETWIN_DATABASE_URL   = "postgresql+psycopg://caretwin:${random_password.db.result}@${aws_db_instance.postgres.address}:5432/caretwin"
    CARETWIN_NEO4J_PASSWORD = random_password.neo4j.result
    NEO4J_AUTH              = "neo4j/${random_password.neo4j.result}"
    CARETWIN_LLM_API_KEY    = ""
  })
  lifecycle { ignore_changes = [secret_string] } # LLM key is filled in by hand
}

# --- Neo4j on ECS with an EFS volume -------------------------------------------------
resource "aws_efs_file_system" "neo4j" {
  encrypted = true
  tags      = { Name = "${var.name}-neo4j" }
}

resource "aws_efs_mount_target" "neo4j" {
  count           = 2
  file_system_id  = aws_efs_file_system.neo4j.id
  subnet_id       = aws_subnet.public[count.index].id
  security_groups = [aws_security_group.data.id]
}

resource "aws_service_discovery_private_dns_namespace" "main" {
  name = "${var.name}.local"
  vpc  = aws_vpc.main.id
}

resource "aws_service_discovery_service" "neo4j" {
  name = "neo4j"
  dns_config {
    namespace_id = aws_service_discovery_private_dns_namespace.main.id
    dns_records {
      ttl  = 10
      type = "A"
    }
  }
  health_check_custom_config { failure_threshold = 1 }
}
