package nfsprobe

import (
	"context"
	"time"
)

// Exports issues the MOUNT3 EXPORT RPC and returns the server's
// advertised export list. Auto-discovers the mountd port via
// portmap when ``mountdPort == 0``, identical to the dispatch
// inside Probe(). Used by the ``list-shares`` subcommand.
func Exports(
	ctx context.Context,
	host string,
	mountdPort uint32,
	timeout time.Duration,
) ([]MountExportEntry, error) {
	if timeout <= 0 {
		timeout = 5 * time.Second
	}
	port := mountdPort
	if port == 0 {
		p, err := portmapGetPort(ctx, host, progMount3, versMount3, protoTCP, timeout)
		if err != nil {
			return nil, err
		}
		port = p
	}
	return mount3Export(ctx, host, port, timeout)
}
