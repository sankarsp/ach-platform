data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  name       = "${var.project}-${var.environment}"
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition
  azs        = slice(data.aws_availability_zones.available.names, 0, 2)

  tags = merge({
    Project            = var.project
    Environment        = var.environment
    Owner              = var.owner
    CostCenter         = var.cost_center
    DataClassification = "confidential"
    Compliance         = "nacha,glba,pci-adjacent"
    ManagedBy          = "terraform"
  }, var.extra_tags)

  backend_image  = "${aws_ecr_repository.this["backend"].repository_url}:${var.backend_image_tag}"
  frontend_image = "${aws_ecr_repository.this["frontend"].repository_url}:${var.frontend_image_tag}"
}
