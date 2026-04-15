package connector

import (
	"context"
	"fmt"
	"io"
	"path/filepath"
	"strings"

	"github.com/aws/aws-sdk-go-v2/aws"
	awsconfig "github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/aws/aws-sdk-go-v2/service/s3"

	"github.com/akashic-project/akashic/scanner/internal/metadata"
	"github.com/akashic-project/akashic/scanner/pkg/models"
)

type S3Connector struct {
	endpoint  string
	bucket    string
	region    string
	accessKey string
	secretKey string
	client    *s3.Client
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

func (c *S3Connector) Walk(ctx context.Context, prefix string, excludePatterns []string, computeHash bool, fn func(*models.FileEntry) error) error {
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

			// Check exclude patterns against each path component
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
			entry := &models.FileEntry{
				Path:      key,
				Filename:  filepath.Base(key),
				SizeBytes: aws.ToInt64(obj.Size),
				IsDir:     isDir,
			}

			if !isDir {
				ext := filepath.Ext(entry.Filename)
				if ext != "" {
					entry.Extension = strings.TrimPrefix(ext, ".")
				}
			}

			if obj.LastModified != nil {
				t := *obj.LastModified
				entry.ModifiedAt = &t
			}

			// S3 ETag as default hash for non-multipart uploads
			if obj.ETag != nil {
				entry.ContentHash = strings.Trim(aws.ToString(obj.ETag), "\"")
			}

			// For proper BLAKE3 hashing, download and hash
			if computeHash && !isDir {
				if hash, err := c.hashObject(ctx, key); err == nil {
					entry.ContentHash = hash
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

func (c *S3Connector) Close() error {
	return nil
}

func (c *S3Connector) Type() string {
	return "s3"
}
