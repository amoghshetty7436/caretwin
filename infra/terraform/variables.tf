variable "region" {
  type    = string
  default = "us-east-1"
}
variable "environment" {
  type    = string
  default = "dev"
}
variable "name" {
  type    = string
  default = "caretwin"
}
variable "api_image_tag" {
  type    = string
  default = "latest"
}
variable "web_image_tag" {
  type    = string
  default = "latest"
}
variable "neo4j_image" {
  type    = string
  default = "neo4j:5.26-community"
}
variable "api_desired_count" {
  type    = number
  default = 2
}
variable "api_cpu" {
  type    = number
  default = 512
}
variable "api_memory" {
  type    = number
  default = 1024
}
variable "db_instance_class" {
  description = "db.t4g.micro is free-tier eligible for 12 months on new AWS accounts"
  type        = string
  default     = "db.t4g.micro"
}
variable "llm_provider" {
  type    = string
  default = "offline"
}
variable "llm_base_url" {
  type    = string
  default = "https://api.groq.com/openai/v1"
}
variable "llm_model" {
  type    = string
  default = "llama-3.3-70b-versatile"
}
variable "github_repository" {
  description = "owner/repo allowed to assume the deploy role through GitHub OIDC"
  type        = string
  default     = "amoghshetty7436/caretwin"
}
variable "create_github_oidc_provider" {
  description = "false if the account already has the token.actions.githubusercontent.com provider"
  type        = bool
  default     = true
}
