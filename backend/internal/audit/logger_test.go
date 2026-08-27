package audit

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
)

// fakeIndexer เก็บ entry ที่ถูกส่งมา index (thread-safe — write ถูกเรียกหลาย goroutine ได้)
type fakeIndexer struct {
	mu      sync.Mutex
	entries []map[string]any
}

func (f *fakeIndexer) Index(e map[string]any) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.entries = append(f.entries, e)
}

func (f *fakeIndexer) count() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return len(f.entries)
}

// readLines อ่านทุกบรรทัดของไฟล์ audit วันนี้ (มีไฟล์เดียวในเทสต์)
func readLines(t *testing.T, dir string) []map[string]any {
	t.Helper()
	matches, err := filepath.Glob(filepath.Join(dir, "audit-*.jsonl"))
	if err != nil || len(matches) == 0 {
		t.Fatalf("ไม่พบไฟล์ audit ใน %s (err=%v)", dir, err)
	}
	b, err := os.ReadFile(matches[0])
	if err != nil {
		t.Fatalf("อ่าน %s: %v", matches[0], err)
	}
	var out []map[string]any
	for _, ln := range strings.Split(strings.TrimSpace(string(b)), "\n") {
		if ln == "" {
			continue
		}
		var m map[string]any
		if err := json.Unmarshal([]byte(ln), &m); err != nil {
			t.Fatalf("บรรทัดไม่ใช่ JSON ที่ถูกต้อง (%q): %v", ln, err)
		}
		out = append(out, m)
	}
	return out
}

func TestCommandWritesJSONL(t *testing.T) {
	dir := t.TempDir()
	l, err := New(dir)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	defer l.Close()

	l.Command(7, "Takeoff", true, "ACCEPTED", 0)
	rows := readLines(t, dir)
	if len(rows) != 1 {
		t.Fatalf("ได้ %d บรรทัด ต้องการ 1", len(rows))
	}
	r := rows[0]
	if r["type"] != "command" || r["command"] != "Takeoff" {
		t.Fatalf("field ไม่ตรง: %v", r)
	}
	if r["allowed"] != true {
		t.Fatalf("allowed ต้องเป็น true: %v", r)
	}
	if r["drone_id"].(float64) != 7 {
		t.Fatalf("drone_id ต้องเป็น 7: %v", r)
	}
	if r["ts"] == "" {
		t.Fatal("ไม่มี timestamp — audit trail ต้องบอกเวลาได้เสมอ")
	}
}

// คำสั่งที่ถูก safety ปฏิเสธต้องถูกบันทึกด้วย (SECURITY.md §3: reject แล้วเขียน audit)
func TestRejectedCommandIsRecorded(t *testing.T) {
	dir := t.TempDir()
	l, _ := New(dir)
	defer l.Close()

	l.Command(2, "Goto", false, "alt 250m เกินเพดาน 120m", -1)
	rows := readLines(t, dir)
	if rows[0]["allowed"] != false {
		t.Fatalf("คำสั่งที่ถูกปฏิเสธต้องบันทึก allowed=false: %v", rows[0])
	}
	// code = -1 (ไม่มี ACK จริง) ไม่ควรโผล่เป็น result_code ให้สับสนกับ MAV_RESULT
	if _, ok := rows[0]["result_code"]; ok {
		t.Fatalf("code -1 ไม่ควรถูกบันทึกเป็น result_code: %v", rows[0])
	}
}

func TestAppendOnlyKeepsEveryEntry(t *testing.T) {
	dir := t.TempDir()
	l, _ := New(dir)
	defer l.Close()

	for i := 0; i < 25; i++ {
		l.Command(1, "Hold", true, "holding", 0)
	}
	l.Event("geofence", "set 4 vertices")
	if got := len(readLines(t, dir)); got != 26 {
		t.Fatalf("append-only ต้องเก็บครบทุก entry — ได้ %d ต้องการ 26", got)
	}
}

func TestEventAndRequestShape(t *testing.T) {
	dir := t.TempDir()
	l, _ := New(dir)
	defer l.Close()

	l.Event("swarm", "formation started")
	l.Request("req-abc", 3, "Arm", "OUTCOME_ACCEPTED", true, false)
	rows := readLines(t, dir)

	if rows[0]["type"] != "event" || rows[0]["category"] != "swarm" {
		t.Fatalf("event shape ผิด: %v", rows[0])
	}
	// request_id ต้องอยู่ใน trail (spec §16.1) — ใช้ไล่ย้อนว่าคำสั่งไหนคือ retry
	if rows[1]["request_id"] != "req-abc" {
		t.Fatalf("request_id หายจาก audit trail: %v", rows[1])
	}
	if rows[1]["replay"] != false {
		t.Fatalf("replay flag ผิด: %v", rows[1])
	}
}

func TestIndexerReceivesEntries(t *testing.T) {
	dir := t.TempDir()
	l, _ := New(dir)
	defer l.Close()
	idx := &fakeIndexer{}
	l.SetIndexer(idx)

	l.Command(1, "Land", true, "ACCEPTED", 0)
	l.Event("failsafe", "link lost")
	if idx.count() != 2 {
		t.Fatalf("indexer ต้องได้ 2 entry — ได้ %d", idx.count())
	}
}

// ไม่มี indexer = JSONL ต้องยังเขียนได้ตามปกติ (JSONL คือ source of truth)
func TestWorksWithoutIndexer(t *testing.T) {
	dir := t.TempDir()
	l, _ := New(dir)
	defer l.Close()

	l.Command(1, "Arm", true, "ACCEPTED", 0)
	if len(readLines(t, dir)) != 1 {
		t.Fatal("ไม่มี indexer แล้ว JSONL ไม่ถูกเขียน")
	}
}

func TestCloseIsSafeToCallTwice(t *testing.T) {
	l, _ := New(t.TempDir())
	l.Command(1, "Hold", true, "holding", 0)
	l.Close()
	l.Close() // ต้องไม่ panic — t.Cleanup/defer อาจเรียกซ้ำได้
}

func TestConcurrentWritesDoNotInterleave(t *testing.T) {
	dir := t.TempDir()
	l, _ := New(dir)
	defer l.Close()

	// หลาย goroutine เขียนพร้อมกัน (1 goroutine/โดรน) — ทุกบรรทัดต้องยัง parse ได้
	var wg sync.WaitGroup
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func(id uint32) {
			defer wg.Done()
			for j := 0; j < 20; j++ {
				l.Command(id, "Goto", true, "sent", 0)
			}
		}(uint32(i + 1))
	}
	wg.Wait()

	rows := readLines(t, dir) // readLines จะ fail เองถ้ามีบรรทัดพัง
	if len(rows) != 160 {
		t.Fatalf("ได้ %d บรรทัด ต้องการ 160 — มี entry หาย", len(rows))
	}
}

func TestNewFailsOnUnwritableDir(t *testing.T) {
	// ใช้ไฟล์เป็น "โฟลเดอร์" → MkdirAll ต้องล้มเหลว
	f := filepath.Join(t.TempDir(), "notadir")
	if err := os.WriteFile(f, []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := New(filepath.Join(f, "audit")); err == nil {
		t.Fatal("audit เขียนไม่ได้แต่ New ไม่คืน error — core จะเริ่มบินโดยไม่มี trail")
	}
}
