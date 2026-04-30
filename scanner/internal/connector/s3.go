package connector

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"path/filepath"
	"strings"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	awsconfig "github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/aws/aws-sdk-go-v2/service/s3"

	"github.com/akashic-project/akashic/scanner/internal/metadata"
	"github.com/akashic-project/akashic/scanner/pkg/models"
)

type S3Connector struct {
	endpoint          string
	bucket            string
	region            string
	accessKey         string
	secretKey         string
	client            *s3.Client
	captureObjectACLs bool
}

func (c *S3Connector) SetCaptureObjectACLs(v bool) {
	c.captureObjectACLs = v
}

func NewS3Connector(endpoint, bucket, region, accessKey, secretKey string) *S3Connector {
	return &S3Connector{
		endpoint:  endpoint,
		bucket:    bucket,
		region:    region,
		accessKey: accessKey,
		secretKey: secretKey,
	}
}

func (c *S3Connector) Connect(ctx context.Context) error {
	cfg, err := awsconfig.LoadDefaultConfig(ctx,
		awsconfig.WithRegion(c.region),
		awsconfig.WithCredentialsProvider(credentials.NewStaticCredentialsProvider(c.accessKey, c.secretKey, "")),
	)
	if err != nil {
		return fmt.Errorf("s3 config: %w", err)
	}

	c.client = s3.NewFromConfig(cfg, func(o *s3.Options) {
		if c.endpoint != "" {
			o.BaseEndpoint = aws.String(c.endpoint)
			o.UsePathStyle = true
		}
	})

	return nil
}

func (c *S3Connector) Walk(ctx context.Context, prefix string, excludePatterns []string, computeHash bool, _ bool, fn func(*models.EntryRecord) error) error {
	excludeSet := make(map[string]bool, len(excludePatterns))
	for _, p := range excludePatterns {
		excludeSet[strings.ToLower(p)] = true
	}

	paginator := s3.NewListObjectsV2Paginator(c.client, &s3.ListObjectsV2Input{
		Bucket: aws.String(c.bucket),
		Prefix: aws.String(prefix),
	})

	for paginator.HasMorePages() {
		page, err := paginator.NextPage(ctx)
		if err != nil {
			return fmt.Errorf("s3 list: %w", err)
		}

		for _, obj := range page.Contents {
			key := aws.ToString(obj.Key)

			skip := false
			for _, part := range strings.Split(key, "/") {
				if excludeSet[strings.ToLower(part)] {
					skip = true
					break
				}
			}
			if skip {
				continue
			}

			isDir := strings.HasSuffix(key, "/")
			entry := &models.EntryRecord{
				Path: key,
				Name: filepath.Base(key),
			}
			if isDir {
				entry.Kind = "directory"
			} else {
				entry.Kind = "file"
				size := aws.ToInt64(obj.Size)
				entry.SizeBytes = &size
				ext := filepath.Ext(entry.Name)
				if ext != "" {
					entry.Extension = strings.TrimPrefix(ext, ".")
				}
			}

			if obj.LastModified != nil {
				t := *obj.LastModified
				entry.ModifiedAt = &t
			}

			if obj.ETag != nil {
				entry.ContentHash = strings.Trim(aws.ToString(obj.ETag), "\"")
			}

			if computeHash && entry.Kind == "file" {
				if hash, err := c.hashObject(ctx, key); err == nil {
					entry.ContentHash = hash
				}
			}

			if c.captureObjectACLs && entry.Kind == "file" {
				if aclOut, aerr := c.client.GetObjectAcl(ctx, &s3.GetObjectAclInput{
					Bucket: aws.String(c.bucket),
					Key:    aws.String(key),
				}); aerr == nil {
					entry.Acl = metadata.FromS3GetObjectAcl(aclOut)
				}
			}

			if err := fn(entry); err != nil {
				return err
			}
		}
	}

	return nil
}

func (c *S3Connector) hashObject(ctx context.Context, key string) (string, error) {
	output, err := c.client.GetObject(ctx, &s3.GetObjectInput{
		Bucket: aws.String(c.bucket),
		Key:    aws.String(key),
	})
	if err != nil {
		return "", err
	}
	defer output.Body.Close()
	return metadata.HashReader(output.Body)
}

func (c *S3Connector) ReadFile(ctx context.Context, path string) (io.ReadCloser, error) {
	output, err := c.client.GetObject(ctx, &s3.GetObjectInput{
		Bucket: aws.String(c.bucket),
		Key:    aws.String(path),
	})
	if err != nil {
		return nil, err
	}
	return output.Body, nil
}

// Delete removes an object from the bucket. Note: on versioned buckets
// this writes a delete marker rather than purging history — that's the
// safer default. Callers wanting to actually purge versions need to
// handle versioning explicitly (out of scope here).
func (c *S3Connector) Delete(ctx context.Context, path string) error {
	if c.client == nil {
		return fmt.Errorf("not connected")
	}
	_, err := c.client.DeleteObject(ctx, &s3.DeleteObjectInput{
		Bucket: aws.String(c.bucket),
		Key:    aws.String(path),
	})
	return err
}

func (c *S3Connector) Close() error {
	return nil
}

func (c *S3Connector) Type() string {
	return "s3"
}

// CollectBucketSecurity fetches GetBucketAcl + GetBucketPolicy + GetPublicAccessBlock
// and packs them into a SourceSecurityMetadata for the next ingest batch.
func (c *S3Connector) CollectBucketSecurity(ctx context.Context) (*models.SourceSecurityMetadata, error) {
	if c.client == nil {
		return nil, fmt.Errorf("not connected")
	}
	meta := &models.SourceSecurityMetadata{
		CapturedAt: time.Now().UTC().Format(time.RFC3339),
	}

	if aclOut, err := c.client.GetBucketAcl(ctx, &s3.GetBucketAclInput{
		Bucket: aws.String(c.bucket),
	}); err == nil {
		meta.BucketAcl = bucketAclToMap(aclOut)
	}

	if polOut, err := c.client.GetBucketPolicy(ctx, &s3.GetBucketPolicyInput{
		Bucket: aws.String(c.bucket),
	}); err == nil && polOut.Policy != nil {
		meta.BucketPolicyPresent = true
		var doc map[string]interface{}
		if jerr := json.Unmarshal([]byte(*polOut.Policy), &doc); jerr == nil {
			meta.BucketPolicy = doc
		}
	}

	if pabOut, err := c.client.GetPublicAccessBlock(ctx, &s3.GetPublicAccessBlockInput{
		Bucket: aws.String(c.bucket),
	}); err == nil && pabOut.PublicAccessBlockConfiguration != nil {
		cfg := pabOut.PublicAccessBlockConfiguration
		meta.PublicAccessBlock = &models.PublicAccessBlock{
			BlockPublicAcls:       aws.ToBool(cfg.BlockPublicAcls),
			IgnorePublicAcls:      aws.ToBool(cfg.IgnorePublicAcls),
			BlockPublicPolicy:     aws.ToBool(cfg.BlockPublicPolicy),
			RestrictPublicBuckets: aws.ToBool(cfg.RestrictPublicBuckets),
		}
	}

	meta.IsPublicInferred = inferS3Public(meta)
	return meta, nil
}

func bucketAclToMap(out *s3.GetBucketAclOutput) map[string]interface{} {
	m := map[string]interface{}{}
	if out.Owner != nil {
		m["owner"] = map[string]interface{}{
			"id":           aws.ToString(out.Owner.ID),
			"display_name": aws.ToString(out.Owner.DisplayName),
		}
	}
	grants := make([]map[string]interface{}, 0, len(out.Grants))
	for _, g := range out.Grants {
		grant := map[string]interface{}{
			"permission": string(g.Permission),
		}
		if g.Grantee != nil {
			grant["grantee_type"] = string(g.Grantee.Type)
			grant["grantee_id"] = aws.ToString(g.Grantee.ID)
			grant["grantee_name"] = aws.ToString(g.Grantee.DisplayName)
			grant["grantee_uri"] = aws.ToString(g.Grantee.URI)
		}
		grants = append(grants, grant)
	}
	m["grants"] = grants
	return m
}

func inferS3Public(meta *models.SourceSecurityMetadata) bool {
	if pab := meta.PublicAccessBlock; pab != nil &&
		pab.BlockPublicAcls && pab.IgnorePublicAcls &&
		pab.BlockPublicPolicy && pab.RestrictPublicBuckets {
		return false
	}
	if acl, ok := meta.BucketAcl["grants"].([]map[string]interface{}); ok {
		for _, g := range acl {
			if uri, _ := g["grantee_uri"].(string); strings.HasSuffix(uri, "/AllUsers") {
				return true
			}
		}
	}
	if doc := meta.BucketPolicy; doc != nil {
		if stmts, ok := doc["Statement"].([]interface{}); ok {
			for _, s := range stmts {
				stmt, _ := s.(map[string]interface{})
				if stmt["Effect"] == "Allow" && stmt["Principal"] == "*" {
					return true
				}
			}
		}
	}
	return false
}
