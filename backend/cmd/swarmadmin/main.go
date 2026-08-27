// swarmadmin — เครื่องมือ CLI จัดการ user/registry ใน SQLite store
//
// ไม่มี default password (spec §15) — ต้องสร้าง user เองก่อนใช้ auth
//
// ใช้งาน:
//
//	go run ./cmd/swarmadmin user add <username> [role]     # อ่านรหัสจาก stdin หรือ env SWARMGOD_NEW_PASSWORD
//	go run ./cmd/swarmadmin user list
//	go run ./cmd/swarmadmin vehicle list
//
// เลือกไฟล์ DB ด้วย env SWARMGOD_DB (default logs/swarmgod.db)
package main

import (
	"bufio"
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/swarmgod/backend/internal/config"
	"github.com/swarmgod/backend/internal/store"
)

func main() {
	if len(os.Args) < 2 {
		usage()
	}
	cfg := config.Load()
	st, err := store.Open(cfg.DBPath)
	if err != nil {
		die("open store %s: %v", cfg.DBPath, err)
	}
	defer st.Close()

	switch os.Args[1] {
	case "user":
		cmdUser(st, os.Args[2:])
	case "vehicle":
		cmdVehicle(st, os.Args[2:])
	case "session":
		cmdSession(st, os.Args[2:])
	default:
		usage()
	}
}

// cmdSession ออก bearer token ให้ operator (login ที่ CLI: ตรวจรหัสก่อนออก token)
func cmdSession(st *store.Store, args []string) {
	if len(args) < 2 || args[0] != "new" {
		die("usage: session new <username> [ttl-hours]")
	}
	username := args[1]
	hours := 12
	if len(args) >= 3 {
		if h, err := strconv.Atoi(args[2]); err == nil && h > 0 {
			hours = h
		}
	}
	pw := readPassword()
	if pw == "" {
		die("empty password rejected")
	}
	u, err := st.Authenticate(username, pw)
	if err != nil {
		die("login failed: %v", err)
	}
	sess, err := st.CreateSession(u.ID, time.Duration(hours)*time.Hour)
	if err != nil {
		die("create session: %v", err)
	}
	fmt.Fprintf(os.Stderr, "✓ session for %q (ttl=%dh). ใส่ token นี้ใน env SWARMGOD_TOKEN ของ cockpit:\n", u.Username, hours)
	fmt.Println(sess.Bearer()) // token ออก stdout อย่างเดียว (pipe/capture ได้)
}

func cmdUser(st *store.Store, args []string) {
	if len(args) == 0 {
		usage()
	}
	switch args[0] {
	case "add":
		if len(args) < 2 {
			die("usage: user add <username> [role]")
		}
		username := args[1]
		role := "operator"
		if len(args) >= 3 {
			role = args[2]
		}
		pw := readPassword()
		if pw == "" {
			die("empty password rejected (spec §15)")
		}
		u, err := st.CreateUser(username, pw, role)
		if err != nil {
			die("create user: %v", err)
		}
		fmt.Printf("✓ created user %q (id=%d role=%s)\n", u.Username, u.ID, u.Role)
	case "list":
		n, err := st.CountUsers()
		if err != nil {
			die("count users: %v", err)
		}
		fmt.Printf("%d user(s) in %s\n", n, "store")
	default:
		die("unknown user subcommand %q", args[0])
	}
}

func cmdVehicle(st *store.Store, args []string) {
	if len(args) == 0 || args[0] != "list" {
		die("usage: vehicle list")
	}
	vs, err := st.ListVehicles()
	if err != nil {
		die("list vehicles: %v", err)
	}
	if len(vs) == 0 {
		fmt.Println("(registry ว่าง)")
		return
	}
	for _, v := range vs {
		fmt.Printf("%s  sysid=%d  %-12s  first=%s\n",
			v.UUID, v.SystemID, v.DisplayName, v.FirstSeen.Format("2006-01-02 15:04"))
	}
}

// readPassword อ่านรหัสจาก env SWARMGOD_NEW_PASSWORD ก่อน (สำหรับ automation)
// ถ้าไม่มี → อ่านบรรทัดแรกจาก stdin
func readPassword() string {
	if pw := os.Getenv("SWARMGOD_NEW_PASSWORD"); pw != "" {
		return pw
	}
	fmt.Fprint(os.Stderr, "password (stdin): ")
	sc := bufio.NewScanner(os.Stdin)
	if sc.Scan() {
		return strings.TrimRight(sc.Text(), "\r\n")
	}
	return ""
}

func usage() {
	fmt.Fprintln(os.Stderr, `swarmadmin — จัดการ SwarmGod store
  user add <username> [role]   สร้าง user (รหัสจาก stdin หรือ env SWARMGOD_NEW_PASSWORD)
  user list                    นับจำนวน user
  session new <user> [ttl-h]   login → พิมพ์ bearer token (ออก stdout)
  vehicle list                 แสดง vehicle registry
env: SWARMGOD_DB=<path>  (default logs/swarmgod.db)`)
	os.Exit(2)
}

func die(format string, a ...any) {
	fmt.Fprintf(os.Stderr, "swarmadmin: "+format+"\n", a...)
	os.Exit(1)
}
