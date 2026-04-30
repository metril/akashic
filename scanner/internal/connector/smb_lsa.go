package connector

import (
	"fmt"
	"io"
	"net"
	"os"

	"github.com/hirochachacha/go-smb2"
)

// lsaTransport is the LSA pipe equivalent of samrTransport. Same shape:
// own the pipe + IPC$ share + session + TCP conn so a single Close()
// tears the whole stack down in reverse setup order.
type lsaTransport struct {
	pipe    io.ReadWriteCloser
	ipc     *smb2.Share
	session *smb2.Session
	conn    net.Conn
}

func (t *lsaTransport) Read(p []byte) (int, error)  { return t.pipe.Read(p) }
func (t *lsaTransport) Write(p []byte) (int, error) { return t.pipe.Write(p) }

func (t *lsaTransport) Close() error {
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

// OpenLsaPipe dials the SMB host, mounts IPC$, and opens the
// `\PIPE\lsarpc` named pipe used by the LSA RPC interface for SID
// translation (LsarLookupSids2).
//
// Mirrors OpenSamrPipe — same auth (NTLM), same trust boundary
// assumption (caller is on a trusted host providing valid creds), same
// ownership semantics (the returned ReadWriteCloser owns everything
// it touches and tears it all down on Close()).
func OpenLsaPipe(host string, port int, username, password string) (io.ReadWriteCloser, error) {
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

	pipe, err := ipc.OpenFile("lsarpc", os.O_RDWR, 0)
	if err != nil {
		_ = ipc.Umount()
		_ = session.Logoff()
		conn.Close()
		return nil, fmt.Errorf("smb open lsarpc pipe: %w", err)
	}

	return &lsaTransport{
		pipe:    pipe,
		ipc:     ipc,
		session: session,
		conn:    conn,
	}, nil
}
