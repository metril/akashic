"""Scanner ↔ api protocol version.

Bumped whenever the lease / heartbeat / handshake contracts change in a
non-additive way. The api accepts agents whose `protocol_version` falls
in `[ACCEPTED_MIN, ACCEPTED_MAX]` at handshake time; out-of-range agents
are rejected with `426 Upgrade Required` so the operator notices fast.

Bumping rules:
- Additive change (new optional field, new endpoint): no bump.
- Breaking change (removed field, semantic shift, new required header):
  bump PROTOCOL_VERSION + raise ACCEPTED_MIN to drop support for older
  agents. Note the change in the release notes.
"""

PROTOCOL_VERSION: int = 1

# Inclusive range. Widen by lowering ACCEPTED_MIN to keep older agents
# working through a transition; raise ACCEPTED_MIN once those agents
# are decommissioned.
ACCEPTED_MIN: int = 1
ACCEPTED_MAX: int = 1
