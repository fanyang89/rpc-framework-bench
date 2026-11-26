package main

import (
	"context"
	"io"
	"net"
	"testing"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	pb "google.golang.org/grpc/interop/grpc_testing"
	"google.golang.org/grpc/status"
	"google.golang.org/grpc/test/bufconn"
)

type peer struct {
	pb.UnimplementedTestServiceServer
	fault string
}

func (p peer) UnaryCall(_ context.Context, r *pb.SimpleRequest) (*pb.SimpleResponse, error) {
	size := r.ResponseSize
	if p.fault == "status" {
		return nil, status.Error(codes.Internal, "unary failed")
	}
	if size == 0 {
		return &pb.SimpleResponse{}, nil
	}
	if p.fault == "short" {
		size--
	}
	return &pb.SimpleResponse{Payload: &pb.Payload{Body: make([]byte, size)}}, nil
}

func (p peer) StreamingOutputCall(r *pb.StreamingOutputCallRequest, s pb.TestService_StreamingOutputCallServer) error {
	for _, parameter := range r.ResponseParameters {
		if p.fault == "short" {
			return nil
		}
		if err := s.Send(&pb.StreamingOutputCallResponse{Payload: &pb.Payload{Body: make([]byte, parameter.Size)}}); err != nil {
			return err
		}
	}
	if p.fault == "status" {
		return status.Error(codes.Internal, "late failure")
	}
	if p.fault == "extra" {
		return s.Send(&pb.StreamingOutputCallResponse{Payload: &pb.Payload{Body: make([]byte, 8)}})
	}
	return nil
}

func (p peer) StreamingInputCall(s pb.TestService_StreamingInputCallServer) error {
	var total int32
	for {
		r, err := s.Recv()
		if err == io.EOF {
			if p.fault == "short" {
				total--
			}
			return s.SendAndClose(&pb.StreamingInputCallResponse{AggregatedPayloadSize: total})
		}
		if err != nil {
			return err
		}
		total += int32(len(r.GetPayload().GetBody()))
	}
}

func (p peer) FullDuplexCall(s pb.TestService_FullDuplexCallServer) error {
	for {
		r, err := s.Recv()
		if err == io.EOF {
			if p.fault == "status" {
				return status.Error(codes.Internal, "late failure")
			}
			if p.fault == "extra" {
				return s.Send(&pb.StreamingOutputCallResponse{Payload: &pb.Payload{Body: make([]byte, 8)}})
			}
			return nil
		}
		if err != nil {
			return err
		}
		if p.fault == "short" {
			return nil
		}
		for _, parameter := range r.ResponseParameters {
			if err := s.Send(&pb.StreamingOutputCallResponse{Payload: &pb.Payload{Body: make([]byte, parameter.Size)}}); err != nil {
				return err
			}
		}
	}
}

func TestOperationValidatesWholeRPC(t *testing.T) {
	for _, mode := range []string{"unary", "client-stream", "server-stream", "bidi"} {
		faults := []string{"", "short"}
		if mode == "unary" {
			faults = append(faults, "status", "empty")
		}
		if mode == "server-stream" || mode == "bidi" {
			faults = append(faults, "extra", "status")
		}
		for _, fault := range faults {
			t.Run(mode+"/"+fault, func(t *testing.T) {
				listener := bufconn.Listen(1024 * 1024)
				server := grpc.NewServer()
				pb.RegisterTestServiceServer(server, peer{fault: fault})
				done := make(chan struct{})
				go func() { defer close(done); _ = server.Serve(listener) }()
				t.Cleanup(func() { server.Stop(); listener.Close(); <-done })
				conn, err := grpc.NewClient("passthrough:///test",
					grpc.WithTransportCredentials(insecure.NewCredentials()),
					grpc.WithContextDialer(func(ctx context.Context, _ string) (net.Conn, error) { return listener.DialContext(ctx) }))
				if err != nil {
					t.Fatal(err)
				}
				defer conn.Close()
				cfg := config{Mode: mode, Messages: 3, RequestSize: 8, ResponseSize: 8}
				if fault == "empty" {
					cfg.ResponseSize = 0
				}
				request := &pb.SimpleRequest{ResponseSize: int32(cfg.ResponseSize), Payload: &pb.Payload{Body: make([]byte, 8)}}
				err = operation(pb.NewTestServiceClient(conn), cfg, request)()
				if (err != nil) != (fault != "" && fault != "empty") {
					t.Fatalf("fault %q: got %v", fault, err)
				}
			})
		}
	}
}
