package connector

import (
	"testing"

	"github.com/akashic-project/akashic/scanner/pkg/models"
)

func TestInferS3Public_BlockedByPAB(t *testing.T) {
	meta := &models.SourceSecurityMetadata{
		PublicAccessBlock: &models.PublicAccessBlock{
			BlockPublicAcls: true, IgnorePublicAcls: true,
			BlockPublicPolicy: true, RestrictPublicBuckets: true,
		},
	}
	if inferS3Public(meta) {
		t.Error("expected non-public when all PAB flags true")
	}
}

func TestInferS3Public_AllUsersGrant(t *testing.T) {
	meta := &models.SourceSecurityMetadata{
		BucketAcl: map[string]interface{}{
			"grants": []map[string]interface{}{
				{"grantee_uri": "http://acs.amazonaws.com/groups/global/AllUsers", "permission": "READ"},
			},
		},
	}
	if !inferS3Public(meta) {
		t.Error("expected public when AllUsers has a grant and no blocking PAB")
	}
}

func TestInferS3Public_PolicyAllowAll(t *testing.T) {
	meta := &models.SourceSecurityMetadata{
		BucketPolicy: map[string]interface{}{
			"Statement": []interface{}{
				map[string]interface{}{"Effect": "Allow", "Principal": "*"},
			},
		},
	}
	if !inferS3Public(meta) {
		t.Error("expected public when policy has Allow Principal:*")
	}
}
