module github.com/akashic-project/akashic/scanner

go 1.22

require (
	github.com/aws/aws-sdk-go-v2 v1.26.0
	github.com/aws/aws-sdk-go-v2/config v1.27.0
	github.com/aws/aws-sdk-go-v2/credentials v1.17.0
	github.com/aws/aws-sdk-go-v2/service/s3 v1.53.0
	github.com/google/uuid v1.6.0
	github.com/hirochachacha/go-smb2 v1.1.0
	github.com/jcmturner/gokrb5/v8 v8.4.4
	github.com/pkg/sftp v1.13.6
	github.com/pkg/xattr v0.4.12
	github.com/zeebo/blake3 v0.2.4
	golang.org/x/crypto v0.17.0
)

require (
	github.com/aws/aws-sdk-go-v2/aws/protocol/eventstream v1.6.1 // indirect
	github.com/aws/aws-sdk-go-v2/feature/ec2/imds v1.15.0 // indirect
	github.com/aws/aws-sdk-go-v2/internal/configsources v1.3.4 // indirect
	github.com/aws/aws-sdk-go-v2/internal/endpoints/v2 v2.6.4 // indirect
	github.com/aws/aws-sdk-go-v2/internal/ini v1.8.0 // indirect
	github.com/aws/aws-sdk-go-v2/internal/v4a v1.3.4 // indirect
	github.com/aws/aws-sdk-go-v2/service/internal/accept-encoding v1.11.1 // indirect
	github.com/aws/aws-sdk-go-v2/service/internal/checksum v1.3.6 // indirect
	github.com/aws/aws-sdk-go-v2/service/internal/presigned-url v1.11.6 // indirect
	github.com/aws/aws-sdk-go-v2/service/internal/s3shared v1.17.4 // indirect
	github.com/aws/aws-sdk-go-v2/service/sso v1.19.0 // indirect
	github.com/aws/aws-sdk-go-v2/service/ssooidc v1.22.0 // indirect
	github.com/aws/aws-sdk-go-v2/service/sts v1.27.0 // indirect
	github.com/aws/smithy-go v1.20.1 // indirect
	github.com/geoffgarside/ber v1.1.0 // indirect
	github.com/hashicorp/go-uuid v1.0.3 // indirect
	github.com/jcmturner/aescts/v2 v2.0.0 // indirect
	github.com/jcmturner/dnsutils/v2 v2.0.0 // indirect
	github.com/jcmturner/gofork v1.7.6 // indirect
	github.com/jcmturner/goidentity/v6 v6.0.1 // indirect
	github.com/jcmturner/rpc/v2 v2.0.3 // indirect
	github.com/klauspost/cpuid/v2 v2.0.12 // indirect
	github.com/kr/fs v0.1.0 // indirect
	golang.org/x/net v0.10.0 // indirect
	golang.org/x/sys v0.15.0 // indirect
)

// Vendored patch: github.com/hirochachacha/go-smb2 v1.1.0 does not expose
// SMB2 QUERY_INFO with InfoType=SMB2_0_INFO_SECURITY. The local copy at
// ./internal/vendor/go-smb2 adds GetSecurityDescriptorBytes() on *Share.
// Based on upstream PR #65 (github.com/hirochachacha/go-smb2/pull/65).
// Drop this replace once that PR is merged and a tagged release is cut.
replace github.com/hirochachacha/go-smb2 => ./internal/vendor/go-smb2
