// Package audit — audit log แบบ append-only ที่ core (SECURITY.md §7, spec §16)
//
// ทุก command/event เขียนเป็น JSONL ต่อบรรทัดที่ logs/audit/audit-YYYY-MM-DD.jsonl
// (หมุนไฟล์ตามวัน, ไม่ลบย้อนหลัง — source of truth) แล้วส่งสำเนาแบบ non-blocking
// ให้ Indexer (store.AsyncIndexer) สำหรับ query เร็ว ๆ — DB ห้ามอยู่ใน
// flight-command critical path (spec §5.9, §3.4) ดังนั้นการเขียนไฟล์นี้ก็ต้องไม่บล็อกคำสั่งเช่นกัน
package audit

import (
	"encoding/json"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"sync"
	"time"
)

// Indexer รับสำเนา entry แบบ non-blocking (store.AsyncIndexer satisfy)
type Indexer interface {
	Index(entry map[string]any)
}

// Logger เขียน audit JSONL แบบ append-only + ส่งสำเนาให้ Indexer (ถ้ามี)
type Logger struct {
	mu  sync.Mutex
	dir string
	idx Indexer

	day string
	f   *os.File
}

// New สร้าง Logger เขียนไฟล์ที่ dir (สร้าง dir ให้ถ้ายังไม่มี)
//
// คืน error ถ้าสร้างโฟลเดอร์ไม่ได้ — audit เขียนไม่ได้ = ไม่ควรปล่อยให้ core
// เริ่มบินเงียบ ๆ โดยไม่มี trail (SECURITY.md §7: ทุกคำสั่งต้องถูกบันทึก)
func New(dir string) (*Logger, error) {
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return nil, fmt.Errorf("audit: mkdir %s: %w", dir, err)
	}
	return &Logger{dir: dir}, nil
}

// SetIndexer ผูก index layer (store.AsyncIndexer) — เรียกได้ทีหลัง New
// nil = ไม่ index (JSONL ยังเขียนปกติ — นั่นคือ source of truth)
func (l *Logger) SetIndexer(idx Indexer) {
	l.mu.Lock()
	l.idx = idx
	l.mu.Unlock()
}

// Close ปิดไฟล์ที่เปิดอยู่ (best-effort — ใช้กับ defer/t.Cleanup ได้ตรง ๆ)
func (l *Logger) Close() {
	l.mu.Lock()
	defer l.mu.Unlock()
	if l.f != nil {
		if err := l.f.Close(); err != nil {
			log.Printf("[audit] close: %v", err)
		}
		l.f = nil
	}
}

// Command บันทึกผลคำสั่งต่อโดรน 1 ลำ
// allowed=false = ถูก safety/precondition ปฏิเสธ (ไม่ส่งออกไปโดรน)
// code = MAV_RESULT ถ้ามี ACK จริง, -1 = ไม่มี/ไม่เกี่ยวข้อง
func (l *Logger) Command(droneID uint32, cmd string, allowed bool, reason string, code int32) {
	entry := map[string]any{
		"ts":       nowISO(),
		"type":     "command",
		"drone_id": droneID,
		"command":  cmd,
		"allowed":  allowed,
		"reason":   reason,
	}
	if code >= 0 {
		entry["result_code"] = code
	}
	l.write(entry)
}

// Event บันทึกเหตุการณ์ระดับระบบที่ไม่ผูกกับคำสั่งใดคำสั่งหนึ่ง (geofence/failsafe/identity/swarm ฯลฯ)
func (l *Logger) Event(category, message string) {
	l.write(map[string]any{
		"ts":       nowISO(),
		"type":     "event",
		"category": category,
		"message":  message,
	})
}

// Request บันทึก audit ระดับ request (idempotency, spec §16.1)
// replay=true = request_id เคยเห็นแล้ว ตอบผลเดิมกลับไปโดยไม่ยิงคำสั่งซ้ำ
func (l *Logger) Request(requestID string, droneID uint32, command, outcome string, ok bool, replay bool) {
	l.write(map[string]any{
		"ts":         nowISO(),
		"type":       "request",
		"request_id": requestID,
		"drone_id":   droneID,
		"command":    command,
		"reason":     outcome,
		"allowed":    ok,
		"replay":     replay,
	})
}

// Correlation records the end-to-end V3-S08 identity attached by CommandGateway
// together with the Core request/result fields needed to join one frontend
// attempt deterministically to request-level idempotency audit. These fields are
// observability only: they never participate in authorization, safety, ownership,
// ordering, or dedup decisions.
func (l *Logger) Correlation(operationID, commandID, attemptID, rpcMethod,
	requestID, command, outcome string, ok bool, handlerError string) {
	if l == nil || (operationID == "" && commandID == "" && attemptID == "") {
		return
	}
	entry := map[string]any{
		"ts":           nowISO(),
		"type":         "command_correlation",
		"operation_id": operationID,
		"command_id":   commandID,
		"attempt_id":   attemptID,
		"rpc_method":   rpcMethod,
		"request_id":   requestID,
		"command":      command,
		"outcome":      outcome,
		"allowed":      ok,
	}
	if handlerError != "" {
		entry["handler_error"] = handlerError
	}
	l.write(entry)
}

func (l *Logger) write(entry map[string]any) {
	l.mu.Lock()
	if err := l.ensureFile(); err != nil {
		l.mu.Unlock()
		log.Printf("[audit] write skipped: %v", err)
		return
	}
	b, err := json.Marshal(entry)
	if err != nil {
		l.mu.Unlock()
		log.Printf("[audit] marshal failed: %v", err)
		return
	}
	if _, err := l.f.Write(append(b, '\n')); err != nil {
		log.Printf("[audit] file write failed: %v", err)
	}
	idx := l.idx // อ่านใต้ lock — SetIndexer เขียนค่านี้จาก goroutine อื่นได้
	l.mu.Unlock()

	// เรียก Index นอก lock: มันต้องไม่บล็อกอยู่แล้ว (AsyncIndexer หยอด channel)
	// แต่ก็ไม่ควรถือ lock ของ audit ค้างไว้ระหว่างเรียกโค้ดแพ็กเกจอื่น
	if idx != nil {
		idx.Index(entry)
	}
}

// ensureFile เปิดไฟล์ของวันนี้ (หมุนไฟล์อัตโนมัติเมื่อข้ามวัน) — เรียกโดยถือ l.mu อยู่แล้ว
func (l *Logger) ensureFile() error {
	day := time.Now().UTC().Format("2006-01-02")
	if l.f != nil && l.day == day {
		return nil
	}
	if l.f != nil {
		l.f.Close()
	}
	path := filepath.Join(l.dir, fmt.Sprintf("audit-%s.jsonl", day))
	f, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		return err
	}
	l.f = f
	l.day = day
	return nil
}

func nowISO() string { return time.Now().UTC().Format(time.RFC3339Nano) }
