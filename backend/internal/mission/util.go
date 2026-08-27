package mission

import (
	"fmt"
	"sort"
)

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
