terraform {
  required_version = ">= 1.6"
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 5.80" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
  }
  # Remote state: create the bucket once, then uncomment (see infra/README.md).
  # backend "s3" {
  #   bucket = "caretwin-tfstate-<account-id>"
  #   key    = "caretwin/terraform.tfstate"
  #   region = "us-east-1"
  # }
}

provider "aws" {
  region = var.region
  default_tags { tags = { Project = "caretwin", ManagedBy = "terraform", Environment = var.environment } }
}
