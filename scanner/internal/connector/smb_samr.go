package connector

import (
	"fmt"
	"io"
	"net"
	"os"

	"github.com/hirochachacha/go-smb2"
)

// samrTransport wraps a SMB-pipe handle and the underlying SMB session/IPC$
// share so closing the transport tears all of them down.
type samrTransport struct {
	pipe    io.ReadWriteCloser
	ipc     *smb2.Share
	session *smb2.Session
	conn    net.Conn
}

func (t *samrTransport) Read(p []byte) (int, error)  { return t.pipe.Read(p) }
func (t *samrTransport) Write(p []byte) (int, error) { return t.pipe.Write(p) }

// Close releases everything in reverse setup order. Errors from each layer
// are best-effort — the caller learns about transport health from the SAMR
// session's own error reporting.
func (t *samrTransport) Close() error {
	if t.pipe != nil {
		_ = t.pipe.Close()
	}
	if t.ipc != nil {
		_ = t.ipc.Umount()
	}
	if t.session != nil {
		_ = t.session.Logoff()
	}
	if t.conn != nil {
		_ = t.conn.Close()
	}
	return nil
}

// OpenSamrPipe dials the SMB host, mounts IPC$, and opens the
// `\PIPE\samr` named pipe. The returned io.ReadWriteCloser owns the
// session and connection — Close() releases everything.
//
// Auth uses NTLM (matches the existing SMBConnector). port may be 0
// (defaults to 445).
func OpenSamrPipe(host string, port int, username, password string) (io.ReadWriteCloser, error) {
	if port == 0 {
		port = 445
	}
	addr := net.JoinHostPort(host, fmt.Sprintf("%d", port))
	conn, err := net.Dial("tcp", addr)
	if err != nil {
		return nil, fmt.Errorf("smb dial %s: %w", addr, err)
	}

	d := &smb2.Dialer{
		Initiator: &smb2.NTLMInitiator{User: username, Password: password},
	}
	session, err := d.Dial(conn)
	if err != nil {
		conn.Close()
		return nil, fmt.Errorf("smb session: %w", err)
	}

	ipc, err := session.Mount(fmt.Sprintf(`\\%s\IPC$`, host))
	if err != nil {
		_ = session.Logoff()
		conn.Close()
		return nil, fmt.Errorf("smb IPC$ mount: %w", err)
	}

	pipe, err := ipc.OpenFile("samr", os.O_RDWR, 0)
	if err != nil {
		_ = ipc.Umount()
		_ = session.Logoff()
		conn.Close()
		return nil, fmt.Errorf("smb open samr pipe: %w", err)
	}

	return &samrTransport{
		pipe:    pipe,
		ipc:     ipc,
		session: session,
		conn:    conn,
	}, nil
}
