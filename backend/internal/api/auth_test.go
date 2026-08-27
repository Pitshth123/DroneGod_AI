package api

import (
	"context"
	"errors"
	"testing"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
)

type fakeVal struct{ good string }

func (f fakeVal) ValidateBearer(b string) (int64, error) {
	if b == f.good {
		return 42, nil
	}
	return 0, errors.New("invalid")
}

func ctxWithToken(tok string) context.Context {
	md := metadata.Pairs("authorization", "Bearer "+tok)
	return metadata.NewIncomingContext(context.Background(), md)
}

// SEC-AUTH-001: นโยบายตาม profile
func TestAuthorize(t *testing.T) {
	val := fakeVal{good: "sess.tok"}

	cases := []struct {
		name    string
		strict  bool
		ctx     context.Context
		wantErr codes.Code // OK = อนุญาต
		wantUID bool
	}{
		{"prod-no-token-reject", true, context.Background(), codes.Unauthenticated, false},
		{"prod-valid-allow", true, ctxWithToken("sess.tok"), codes.OK, true},
		{"prod-invalid-reject", true, ctxWithToken("wrong"), codes.Unauthenticated, false},
		{"dev-no-token-allow", false, context.Background(), codes.OK, false},
		{"dev-valid-allow", false, ctxWithToken("sess.tok"), codes.OK, true},
		{"dev-invalid-reject", false, ctxWithToken("wrong"), codes.Unauthenticated, false},
	}

	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			profile := "sitl"
			if c.strict {
				profile = "production"
			}
			a := newAuthInterceptor(val, profile)
			newCtx, err := a.authorize(c.ctx, "/svc/Method")

			if c.wantErr == codes.OK {
				if err != nil {
					t.Fatalf("expected allow, got err %v", err)
				}
				uid, ok := newCtx.Value(userIDKey{}).(int64)
				if c.wantUID && (!ok || uid != 42) {
					t.Fatalf("expected userID 42 in ctx, got %v ok=%v", uid, ok)
				}
				if !c.wantUID && ok {
					t.Fatalf("did not expect userID in ctx, got %v", uid)
				}
			} else {
				if status.Code(err) != c.wantErr {
					t.Fatalf("expected code %v, got %v (err=%v)", c.wantErr, status.Code(err), err)
				}
			}
		})
	}
}

// production profile ต้องบังคับ token ผ่าน strict flag ที่ถูกตั้งจาก profile string
func TestStrictFromProfile(t *testing.T) {
	if !newAuthInterceptor(fakeVal{}, "production").strict {
		t.Fatal("production must be strict")
	}
	if !newAuthInterceptor(fakeVal{}, "PRODUCTION").strict {
		t.Fatal("profile match must be case-insensitive")
	}
	if newAuthInterceptor(fakeVal{}, "sitl").strict {
		t.Fatal("sitl must not be strict")
	}
}
