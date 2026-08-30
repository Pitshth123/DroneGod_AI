package mission

import (
	"fmt"
	"sort"
)

// canonicalParticipants returns the participant ids as a unique, ascending slice.
// Duplicate ids in one plan are collapsed deterministically so a run can never
// address the same drone twice (e.g. [1,2,1] -> [1,2]; [1,1] -> [1]).
func canonicalParticipants(ids []uint32) []uint32 {
	seen := make(map[uint32]struct{}, len(ids))
	out := make([]uint32, 0, len(ids))
	for _, id := range ids {
		if _, dup := seen[id]; dup {
			continue
		}
		seen[id] = struct{}{}
		out = append(out, id)
	}
	sort.Slice(out, func(i, j int) bool { return out[i] < out[j] })
	return out
}

// orderedParticipants preserves the first occurrence of the operator-supplied
// mission order. SWARM_LEADER succession uses this frozen order rather than
// numeric ID order; other mission modes retain their canonical sorted sets.
func orderedParticipants(ids []uint32) []uint32 {
	seen := make(map[uint32]struct{}, len(ids))
	out := make([]uint32, 0, len(ids))
	for _, id := range ids {
		if id == 0 {
			continue
		}
		if _, duplicate := seen[id]; duplicate {
			continue
		}
		seen[id] = struct{}{}
		out = append(out, id)
	}
	return out
}

// sortedScopes returns the wait-map keys in ascending order so iteration and
// snapshots are deterministic (0 = grouped scope, otherwise drone ids).
func sortedScopes(waits map[uint32]*WaitState) []uint32 {
	scopes := make([]uint32, 0, len(waits))
	for k := range waits {
		scopes = append(scopes, k)
	}
	sort.Slice(scopes, func(i, j int) bool { return scopes[i] < scopes[j] })
	return scopes
}

func waitReason(scope uint32, index, seconds int) string {
	if scope == 0 {
		return fmt.Sprintf("WAIT %ds at WP %d", seconds, index+1)
	}
	return fmt.Sprintf("WAIT %ds at WP %d (D%d)", seconds, index+1, scope)
}

func indexReason(prefix string, index int) string {
	return fmt.Sprintf("%s %d", prefix, index+1)
}

// sepIndexReason labels a SEPARATE per-drone progression boundary so the durable
// history records which drone advanced (D<id>) to which 1-based waypoint.
func sepIndexReason(scope uint32, index int) string {
	return fmt.Sprintf("WP %d (D%d)", index+1, scope)
}
