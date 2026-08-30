package main

import (
	"os"
	"strings"
	"testing"
)

func TestBenchAckContainsNoFlightCommandRPCs(t *testing.T) {
	b, err := os.ReadFile("main.go")
	if err != nil {
		t.Fatal(err)
	}
	src := string(b)
	for _, forbidden := range []string{
		"c.Arm(", "c.Disarm(", "c.Kill(", "c.Takeoff(", "c.Land(",
		"c.ReturnToLaunch(", "c.Hold(", "c.Goto(", "c.ChangeAlt(", "c.RcMove(",
	} {
		if strings.Contains(src, forbidden) {
			t.Fatalf("benchack must remain mode-ACK-only; found %s", forbidden)
		}
	}
	if !strings.Contains(src, "if t.GetArmed()") || !strings.Contains(src, confirmation) {
		t.Fatal("benchack must retain armed refusal and explicit bench confirmation")
	}
}
