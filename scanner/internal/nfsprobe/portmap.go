package nfsprobe

import (
	"context"
	"fmt"
	"time"
)

// Portmapper / rpcbind program (RFC 1833). Listens on TCP/UDP 111 and
// answers "where's program X version Y running?" We use it to find
// mountd's port — mountd typically registers on a randomly-assigned
// port at startup.

const (
	progPortmap        = 100000
	versPortmap        = 2
	procPortmapGetPort = 3
	portmapPort        = 111

	protoTCP = 6
	protoUDP = 17
)

// portmapGetPort calls PMAPPROC_GETPORT for the given program, version,
// and protocol against `host:111`. Returns the port number, or 0 if the
// program isn't registered (success-but-zero is the ABI for "not
// found"; we treat that as a recoverable signal in the cascade).
func portmapGetPort(
	ctx context.Context,
	host string,
	program, version, protocol uint32,
	timeout time.Duration,
) (uint32, error) {
	// Args: program, version, protocol, port (port is ignored on input).
	w := newXDRWriter()
	w.writeUint32(program)
	w.writeUint32(version)
	w.writeUint32(protocol)
	w.writeUint32(0)

	addr := fmt.Sprintf("%s:%d", host, portmapPort)
	body, err := rpcCallTCP(
		ctx, addr,
		progPortmap, versPortmap, procPortmapGetPort,
		noAuth, w.bytes(), timeout,
	)
	if err != nil {
		return 0, err
	}
	r := newXDRReader(body)
	port, err := r.readUint32()
	if err != nil {
		return 0, fmt.Errorf("portmap reply: %w", err)
	}
	return port, nil
}
