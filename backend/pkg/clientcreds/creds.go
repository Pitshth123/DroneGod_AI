// Package clientcreds — dial core gRPC ด้วย mTLS ถ้ามี cert, ไม่งั้น insecure
// ใช้ร่วมกันโดย test clients (flyctl/swarmctl/telemctl)
package clientcreds

import (
	"crypto/tls"
	"crypto/x509"
	"os"
	"path/filepath"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/credentials/insecure"
)

// certDirs ที่จะลองหา (relative to cwd — go run รันจาก backend/)
var certDirs = []string{"../certs", "certs", "./certs"}

func Dial(addr string) (*grpc.ClientConn, error) {
	if creds, ok := loadClient(); ok {
		return grpc.NewClient(addr, grpc.WithTransportCredentials(creds))
	}
	return grpc.NewClient(addr, grpc.WithTransportCredentials(insecure.NewCredentials()))
}

func loadClient() (credentials.TransportCredentials, bool) {
	for _, d := range certDirs {
		ca := filepath.Join(d, "ca.crt")
		crt := filepath.Join(d, "client.crt")
		key := filepath.Join(d, "client.key")
		cert, err := tls.LoadX509KeyPair(crt, key)
		if err != nil {
			continue
		}
		caPEM, err := os.ReadFile(ca)
		if err != nil {
			continue
		}
		pool := x509.NewCertPool()
		if !pool.AppendCertsFromPEM(caPEM) {
			continue
		}
		return credentials.NewTLS(&tls.Config{
			Certificates: []tls.Certificate{cert},
			RootCAs:      pool,
			ServerName:   "localhost", // ตรงกับ SAN ใน server cert
			MinVersion:   tls.VersionTLS12,
		}), true
	}
	return nil, false
}
