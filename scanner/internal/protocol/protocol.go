// Package protocol holds the wire-protocol version that the scanner
// agent presents to the api during /handshake. Must match the value in
// api/akashic/protocol.py:PROTOCOL_VERSION.
//
// Bump on breaking lease/heartbeat/handshake contract changes; the api
// rejects out-of-range agents with 426 Upgrade Required.
package protocol

const Version = 1
