// auth.go — gRPC authentication interceptor (spec §8 authenticate, §15 security)
//
// นโยบายตาม profile (spec §17):
//   - production: ทุก RPC ต้องมี bearer token ที่ valid (fail closed §5.8)
//   - sitl/hil (dev): ถ้ามี token → ตรวจ (invalid = reject); ถ้าไม่มี → อนุญาต (dev mode)
//     เพื่อไม่ให้ cockpit เดิมที่ยังไม่ส่ง token พังระหว่างพัฒนา
package api

import (
	"context"
	"strings"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
)

// SessionValidator ตรวจ bearer token → userID (store.Store satisfy)
type SessionValidator interface {
	ValidateBearer(bearer string) (int64, error)
}

type authInterceptor struct {
	val    SessionValidator
	strict bool // true = production (บังคับ token ทุก RPC)
}

func newAuthInterceptor(val SessionValidator, profile string) *authInterceptor {
	return &authInterceptor{val: val, strict: strings.EqualFold(profile, "production")}
}

// bearerFromCtx ดึง token จาก metadata "authorization: Bearer <token>"
func bearerFromCtx(ctx context.Context) (string, bool) {
	md, ok := metadata.FromIncomingContext(ctx)
	if !ok {
		return "", false
	}
	vals := md.Get("authorization")
	if len(vals) == 0 {
		return "", false
	}
	tok := strings.TrimSpace(vals[0])
	if len(tok) > 7 && strings.EqualFold(tok[:7], "bearer ") {
		tok = strings.TrimSpace(tok[7:])
	}
	if tok == "" {
		return "", false
	}
	return tok, true
}

type userIDKey struct{}

// authorize ใช้ตรรกะร่วมกันของ unary/stream: คืน ctx ใหม่ (แนบ userID) หรือ error
func (a *authInterceptor) authorize(ctx context.Context, method string) (context.Context, error) {
	tok, present := bearerFromCtx(ctx)
	if !present {
		if a.strict {
			return nil, status.Errorf(codes.Unauthenticated,
				"missing bearer token (production profile requires auth) — %s", method)
		}
		return ctx, nil // dev mode: อนุญาต
	}
	uid, err := a.val.ValidateBearer(tok)
	if err != nil {
		// token ผิด/หมดอายุ = reject เสมอ ไม่ว่า profile ใด (มี token แต่ invalid = เจตนาไม่ดี)
		return nil, status.Error(codes.Unauthenticated, "invalid or expired session token")
	}
	return context.WithValue(ctx, userIDKey{}, uid), nil
}

func (a *authInterceptor) unary() grpc.UnaryServerInterceptor {
	return func(ctx context.Context, req any, info *grpc.UnaryServerInfo,
		handler grpc.UnaryHandler) (any, error) {
		newCtx, err := a.authorize(ctx, info.FullMethod)
		if err != nil {
			return nil, err
		}
		return handler(newCtx, req)
	}
}

func (a *authInterceptor) stream() grpc.StreamServerInterceptor {
	return func(srv any, ss grpc.ServerStream, info *grpc.StreamServerInfo,
		handler grpc.StreamHandler) error {
		newCtx, err := a.authorize(ss.Context(), info.FullMethod)
		if err != nil {
			return err
		}
		return handler(srv, &wrappedStream{ServerStream: ss, ctx: newCtx})
	}
}

// wrappedStream override Context() เพื่อส่ง ctx ที่แนบ userID ต่อไปยัง handler
type wrappedStream struct {
	grpc.ServerStream
	ctx context.Context
}

func (w *wrappedStream) Context() context.Context { return w.ctx }
