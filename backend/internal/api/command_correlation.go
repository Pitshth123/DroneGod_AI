package api

import (
	"context"
	"strings"

	"google.golang.org/grpc"
	"google.golang.org/grpc/metadata"

	pb "github.com/swarmgod/backend/gen/swarmgod/v1"
)

// V3-S08 wire correlation headers.  They are intentionally metadata instead of
// command proto fields: existing request_id remains Core's idempotency key, while
// these IDs only make one operator intent traceable from frontend Gateway to the
// Core audit boundary.
const (
	correlationOperationHeader = "x-swarmgod-operation-id"
	correlationCommandHeader   = "x-swarmgod-command-id"
	correlationAttemptHeader   = "x-swarmgod-attempt-id"
	maxCorrelationValueLen     = 512
)

type commandCorrelation struct {
	operationID string
	commandID   string
	attemptID   string
}

func correlationMetadataValue(md metadata.MD, key string) string {
	vals := md.Get(key)
	if len(vals) == 0 {
		return ""
	}
	v := strings.TrimSpace(vals[0])
	if len(v) > maxCorrelationValueLen {
		v = v[:maxCorrelationValueLen]
	}
	return v
}

// commandCorrelationFromContext returns a complete correlation tuple only.
// Partial/malformed metadata never influences command execution and is ignored
// for correlation audit rather than creating an ambiguous trace.
func commandCorrelationFromContext(ctx context.Context) (commandCorrelation, bool) {
	md, ok := metadata.FromIncomingContext(ctx)
	if !ok {
		return commandCorrelation{}, false
	}
	c := commandCorrelation{
		operationID: correlationMetadataValue(md, correlationOperationHeader),
		commandID:   correlationMetadataValue(md, correlationCommandHeader),
		attemptID:   correlationMetadataValue(md, correlationAttemptHeader),
	}
	if c.operationID == "" || c.commandID == "" || c.attemptID == "" {
		return commandCorrelation{}, false
	}
	return c, true
}

type requestIDGetter interface {
	GetRequestId() string
}

func correlationRequestID(req any) string {
	if g, ok := req.(requestIDGetter); ok && g != nil {
		return strings.TrimSpace(g.GetRequestId())
	}
	return ""
}

// correlationAuditUnary is observe-only. Auth remains earlier in the interceptor
// chain; this wrapper calls the handler exactly once, then records the correlation
// tuple together with request_id and final CommandResult fields. It never
// authorizes, rejects, deduplicates, preempts, or mutates the request/result.
func (s *Server) correlationAuditUnary() grpc.UnaryServerInterceptor {
	return func(ctx context.Context, req any, info *grpc.UnaryServerInfo,
		handler grpc.UnaryHandler) (any, error) {
		c, hasCorrelation := commandCorrelationFromContext(ctx)
		resp, err := handler(ctx, req)
		if !hasCorrelation || s == nil || s.cmd == nil {
			return resp, err
		}

		requestID := correlationRequestID(req)
		commandName := ""
		outcome := ""
		ok := false
		if r, isCommandResult := resp.(*pb.CommandResult); isCommandResult && r != nil {
			if strings.TrimSpace(r.GetRequestId()) != "" {
				requestID = strings.TrimSpace(r.GetRequestId())
			}
			commandName = r.GetCommand()
			outcome = r.GetOutcome().String()
			ok = r.GetOk()
		}
		handlerError := ""
		if err != nil {
			handlerError = err.Error()
		}
		s.cmd.RecordCorrelation(c.operationID, c.commandID, c.attemptID,
			info.FullMethod, requestID, commandName, outcome, ok, handlerError)
		return resp, err
	}
}
