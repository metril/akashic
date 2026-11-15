package metadata

import (
	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/s3"

	"github.com/akashic-project/akashic/scanner/internal/apiclient"
	"github.com/akashic-project/akashic/scanner/internal/wire"
)

// FromS3GetObjectAcl converts AWS SDK output to our wrapped ACL shape.
func FromS3GetObjectAcl(out *s3.GetObjectAclOutput) *apiclient.ACLInput {
	if out == nil {
		return nil
	}
	acl := apiclient.S3ACLInput{}
	if out.Owner != nil {
		acl.Owner = &apiclient.S3OwnerInput{
			Id:          aws.ToString(out.Owner.ID),
			DisplayName: wire.Str(aws.ToString(out.Owner.DisplayName)),
		}
	}
	grants := []apiclient.S3GrantInput{}
	for _, g := range out.Grants {
		grant := apiclient.S3GrantInput{
			Permission: apiclient.S3GrantInputPermission(g.Permission),
		}
		if g.Grantee != nil {
			grant.GranteeType = apiclient.S3GrantInputGranteeType(g.Grantee.Type)
			grant.GranteeId = wire.Str(aws.ToString(g.Grantee.ID))
			grant.GranteeName = wire.Str(aws.ToString(g.Grantee.DisplayName))
		}
		grants = append(grants, grant)
	}
	acl.Grants = &grants
	return wire.S3ACL(acl)
}
