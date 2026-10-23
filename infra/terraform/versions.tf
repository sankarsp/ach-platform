terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Remote state (recommended for any shared environment). Create the bucket and lock
  # table once, out of band, then uncomment and run `terraform init -migrate-state`.
  #
  # backend "s3" {
  #   bucket         = "acme-terraform-state-us-east-1"
  #   key            = "ach-platform/production/terraform.tfstate"
  #   region         = "us-east-1"
  #   encrypt        = true
  #   kms_key_id     = "alias/terraform-state"
  #   dynamodb_table = "terraform-locks" # partition key: LockID (S)
  # }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = local.tags
  }
}
