package connector

import (
	"context"
	"fmt"
	"io"

	"github.com/akashic-project/akashic/scanner/pkg/models"
)

// SMBConnector connects to a Windows/Samba share via SMB2/3.
//
// TODO: Implement using github.com/hirochachacha/go-smb2 when integration
// testing with a real SMB server is available.
type SMBConnector struct {
	host     string
	port     int
	username string
	password string
	share    string
}

// NewSMBConnector creates a new SMBConnector.
func NewSMBConnector(host string, port int, username, password, share string) *SMBConnector {
	return &SMBConnector{
		host:     host,
		port:     port,
		username: username,
		password: password,
		share:    share,
	}
}

// Connect establishes an SMB session.
// TODO: Implement with go-smb2: dial TCP, negotiate SMB2, authenticate, mount share.
func (c *SMBConnector) Connect(_ context.Context) error {
	return fmt.Errorf("not implemented: SMBConnector.Connect (requires github.com/hirochachacha/go-smb2)")
}

// Walk traverses the SMB share starting at root.
// TODO: Use smb2.Share.ReadDir recursively to enumerate files.
func (c *SMBConnector) Walk(_ context.Context, root string, excludePatterns []string, computeHash bool, fn func(*models.FileEntry) error) error {
	return fmt.Errorf("not implemented: SMBConnector.Walk")
}

// ReadFile opens a file on the SMB share for reading.
// TODO: Use smb2.Share.Open to get a ReadCloser.
func (c *SMBConnector) ReadFile(_ context.Context, path string) (io.ReadCloser, error) {
	return nil, fmt.Errorf("not implemented: SMBConnector.ReadFile")
}

// Close disconnects from the SMB server.
func (c *SMBConnector) Close() error {
	return nil
}

// Type returns the connector type.
func (c *SMBConnector) Type() string {
	return "smb"
}
