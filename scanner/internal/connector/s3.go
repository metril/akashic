package connector

import (
	"context"
	"fmt"
	"io"

	"github.com/akashic-project/akashic/scanner/pkg/models"
)

// S3Connector lists and reads objects from an S3-compatible bucket.
//
// TODO: Implement using github.com/aws/aws-sdk-go-v2 when integration
// testing with a real S3 endpoint (or LocalStack) is available.
type S3Connector struct {
	endpoint  string
	bucket    string
	region    string
	accessKey string
	secretKey string
}

// NewS3Connector creates a new S3Connector.
func NewS3Connector(endpoint, bucket, region, accessKey, secretKey string) *S3Connector {
	return &S3Connector{
		endpoint:  endpoint,
		bucket:    bucket,
		region:    region,
		accessKey: accessKey,
		secretKey: secretKey,
	}
}

// Connect initialises the S3 client.
// TODO: Use aws-sdk-go-v2 config.LoadDefaultConfig with custom credentials and endpoint.
func (c *S3Connector) Connect(_ context.Context) error {
	return fmt.Errorf("not implemented: S3Connector.Connect (requires github.com/aws/aws-sdk-go-v2)")
}

// Walk lists all objects under the given prefix and calls fn for each.
// TODO: Use s3.NewListObjectsV2Paginator to iterate pages and build FileEntry records.
func (c *S3Connector) Walk(_ context.Context, root string, excludePatterns []string, computeHash bool, fn func(*models.FileEntry) error) error {
	return fmt.Errorf("not implemented: S3Connector.Walk")
}

// ReadFile fetches an S3 object body for reading.
// TODO: Use s3.GetObject and return Body as io.ReadCloser.
func (c *S3Connector) ReadFile(_ context.Context, path string) (io.ReadCloser, error) {
	return nil, fmt.Errorf("not implemented: S3Connector.ReadFile")
}

// Close releases any S3 client resources.
func (c *S3Connector) Close() error {
	return nil
}

// Type returns the connector type.
func (c *S3Connector) Type() string {
	return "s3"
}
