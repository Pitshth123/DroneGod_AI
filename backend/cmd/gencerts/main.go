// gencerts — สร้าง dev certificates สำหรับ mTLS (cockpit <-> core) + MAVLink signing key
// Go ล้วน ไม่ต้องมี openssl. เขียนไป certs/ (git-ignored)
//   go run ./cmd/gencerts -out ../certs
package main

import (
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/hex"
	"encoding/pem"
	"flag"
	"log"
	"math/big"
	"net"
	"os"
	"path/filepath"
	"time"
)

func main() {
	out := flag.String("out", "../certs", "output dir")
	pass := flag.String("passphrase", "", "MAVLink signing passphrase (ว่าง=สุ่มให้) — พิมพ์ค่าเดียวกันนี้ใน Mission Planner")
	flag.Parse()
	if err := os.MkdirAll(*out, 0o755); err != nil {
		log.Fatal(err)
	}

	// ── CA ──
	caKey, _ := rsa.GenerateKey(rand.Reader, 2048)
	caTmpl := &x509.Certificate{
		SerialNumber:          big.NewInt(1),
		Subject:               pkix.Name{CommonName: "SwarmGod-Dev-CA"},
		NotBefore:             time.Now().Add(-time.Hour),
		NotAfter:              time.Now().AddDate(10, 0, 0),
		IsCA:                  true,
		KeyUsage:              x509.KeyUsageCertSign | x509.KeyUsageDigitalSignature,
		BasicConstraintsValid: true,
	}
	caDER, _ := x509.CreateCertificate(rand.Reader, caTmpl, caTmpl, &caKey.PublicKey, caKey)
	caCert, _ := x509.ParseCertificate(caDER)
	writePEM(filepath.Join(*out, "ca.crt"), "CERTIFICATE", caDER)
	writeKey(filepath.Join(*out, "ca.key"), caKey)

	// ── server cert (core) — SAN 127.0.0.1 + localhost ──
	makeLeaf(*out, "server", caCert, caKey, x509.ExtKeyUsageServerAuth)
	// ── client cert (cockpit) ──
	makeLeaf(*out, "client", caCert, caKey, x509.ExtKeyUsageClientAuth)

	// ── MAVLink signing key = SHA256(passphrase) ──
	// FC/Mission Planner ได้ key จาก SHA256(passphrase) เหมือนกัน → ตรงกันด้วย passphrase
	phrase := *pass
	if phrase == "" {
		b := make([]byte, 12)
		rand.Read(b)
		phrase = "swarmgod-" + hex.EncodeToString(b) // สุ่ม passphrase ที่พิมพ์ได้
	}
	key := sha256.Sum256([]byte(phrase))
	os.WriteFile(filepath.Join(*out, "mavlink_key"), key[:], 0o600)
	os.WriteFile(filepath.Join(*out, "mavlink_passphrase.txt"), []byte(phrase+"\n"), 0o600)

	log.Printf("✓ certs written to %s (ca, server, client, mavlink_key)", *out)
	log.Println("─────────────────────────────────────────────────────────")
	log.Printf("  MAVLink signing PASSPHRASE : %s", phrase)
	log.Printf("  (พิมพ์ passphrase นี้ใน Mission Planner ให้ตรงกัน)")
	log.Printf("  key hex (เผื่อ MP รับ raw)  : %x", key)
	log.Println("─────────────────────────────────────────────────────────")
}

func makeLeaf(dir, name string, caCert *x509.Certificate, caKey *rsa.PrivateKey, eku x509.ExtKeyUsage) {
	key, _ := rsa.GenerateKey(rand.Reader, 2048)
	tmpl := &x509.Certificate{
		SerialNumber: big.NewInt(time.Now().UnixNano()),
		Subject:      pkix.Name{CommonName: "swarmgod-" + name},
		NotBefore:    time.Now().Add(-time.Hour),
		NotAfter:     time.Now().AddDate(2, 0, 0),
		KeyUsage:     x509.KeyUsageDigitalSignature | x509.KeyUsageKeyEncipherment,
		ExtKeyUsage:  []x509.ExtKeyUsage{eku},
		DNSNames:     []string{"localhost", "swarmgod-core"},
		IPAddresses:  []net.IP{net.IPv4(127, 0, 0, 1), net.IPv6loopback},
	}
	der, _ := x509.CreateCertificate(rand.Reader, tmpl, caCert, &key.PublicKey, caKey)
	writePEM(filepath.Join(dir, name+".crt"), "CERTIFICATE", der)
	writeKey(filepath.Join(dir, name+".key"), key)
}

func writePEM(path, typ string, der []byte) {
	f, _ := os.Create(path)
	defer f.Close()
	pem.Encode(f, &pem.Block{Type: typ, Bytes: der})
}

func writeKey(path string, key *rsa.PrivateKey) {
	f, _ := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0o600)
	defer f.Close()
	pem.Encode(f, &pem.Block{Type: "RSA PRIVATE KEY", Bytes: x509.MarshalPKCS1PrivateKey(key)})
}
