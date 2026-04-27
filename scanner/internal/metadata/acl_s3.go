package metadata

import (
	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/s3"

	"github.com/akashic-project/akashic/scanner/pkg/models"
)

// FromS3GetObjectAcl converts AWS SDK output to our wrapped ACL shape.
func FromS3GetObjectAcl(out *s3.GetObjectAclOutput) *models.ACL {
	if out == nil {
		return nil
	}
	acl := &models.ACL{Type: "s3"}
	if out.Owner != nil {
		acl.S3Owner = &models.S3Owner{
			ID:          aws.ToString(out.Owner.ID),
			DisplayName: aws.ToString(out.Owner.DisplayName),
		}
	}
	for _, g := range out.Grants {
		grant := models.S3Grant{
			Permission: string(g.Permission),
		}
		if g.Grantee != nil {
			grant.GranteeType = string(g.Grantee.Type)
			grant.GranteeID = aws.ToString(g.Grantee.ID)
			grant.GranteeName = aws.ToString(g.Grantee.DisplayName)
		}
		acl.S3Grants = append(acl.S3Grants, grant)
	}
	return acl
}
