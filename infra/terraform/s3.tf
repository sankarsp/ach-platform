# ---------------------------------------------------------------- Nacha file archive (WORM)
# Object Lock in COMPLIANCE mode: no one, including the root user, can delete or shorten retention of an
# object version until its retention date passes. Nacha Rules require retaining entry records for 2 years;
# the 7-year default covers BSA/AML and typical bank record-retention policies. Choose deliberately: in
# COMPLIANCE mode the retention cannot be undone and the bucket cannot be emptied early.

resource "aws_s3_bucket" "nacha_archive" {
  bucket              = "${local.name}-nacha-archive-${local.account_id}"
  object_lock_enabled = true

  tags = { Name = "${local.name}-nacha-archive", Retention = "${var.nacha_archive_retention_days}d" }
}

resource "aws_s3_bucket_versioning" "nacha_archive" {
  bucket = aws_s3_bucket.nacha_archive.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_object_lock_configuration" "nacha_archive" {
  bucket = aws_s3_bucket.nacha_archive.id

  rule {
    default_retention {
      mode = "COMPLIANCE"
      days = var.nacha_archive_retention_days
    }
  }

  depends_on = [aws_s3_bucket_versioning.nacha_archive]
}

resource "aws_s3_bucket_server_side_encryption_configuration" "nacha_archive" {
  bucket = aws_s3_bucket.nacha_archive.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.main.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "nacha_archive" {
  bucket                  = aws_s3_bucket.nacha_archive.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "nacha_archive" {
  bucket = aws_s3_bucket.nacha_archive.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "nacha_archive" {
  bucket = aws_s3_bucket.nacha_archive.id

  rule {
    id     = "archive-to-glacier"
    status = "Enabled"

    filter {}

    transition {
      days          = var.nacha_archive_glacier_after_days
      storage_class = "GLACIER"
    }

    noncurrent_version_transition {
      noncurrent_days = var.nacha_archive_glacier_after_days
      storage_class   = "GLACIER"
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  depends_on = [aws_s3_bucket_versioning.nacha_archive]
}

data "aws_iam_policy_document" "nacha_archive" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.nacha_archive.arn,
      "${aws_s3_bucket.nacha_archive.arn}/*",
    ]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }

  statement {
    sid       = "DenyUnencryptedOrWrongKeyUploads"
    effect    = "Deny"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.nacha_archive.arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "StringNotEqualsIfExists"
      variable = "s3:x-amz-server-side-encryption-aws-kms-key-id"
      values   = [aws_kms_key.main.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "nacha_archive" {
  bucket = aws_s3_bucket.nacha_archive.id
  policy = data.aws_iam_policy_document.nacha_archive.json

  depends_on = [aws_s3_bucket_public_access_block.nacha_archive]
}

# ---------------------------------------------------------------- ALB access logs
# ALB access logging only supports SSE-S3 (not SSE-KMS), so it gets its own bucket.
data "aws_elb_service_account" "this" {}

resource "aws_s3_bucket" "alb_logs" {
  bucket        = "${local.name}-alb-logs-${local.account_id}"
  force_destroy = false
  tags          = { Name = "${local.name}-alb-logs" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "alb_logs" {
  bucket                  = aws_s3_bucket.alb_logs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id

  rule {
    id     = "expire"
    status = "Enabled"

    filter {}

    transition {
      days          = 90
      storage_class = "GLACIER"
    }

    expiration {
      days = var.log_retention_days
    }
  }
}

data "aws_iam_policy_document" "alb_logs" {
  statement {
    sid       = "AllowElbLogDelivery"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.alb_logs.arn}/alb/AWSLogs/${local.account_id}/*"]
    principals {
      type        = "AWS"
      identifiers = [data.aws_elb_service_account.this.arn]
    }
  }

  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.alb_logs.arn,
      "${aws_s3_bucket.alb_logs.arn}/*",
    ]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id
  policy = data.aws_iam_policy_document.alb_logs.json

  depends_on = [aws_s3_bucket_public_access_block.alb_logs]
}
