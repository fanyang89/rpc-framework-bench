package main

import (
	"context"
	"flag"
	"log"
	"net"
	"os/signal"
	"syscall"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	pb "google.golang.org/grpc/interop/grpc_testing"
	"google.golang.org/grpc/status"
)

type testService struct {
	pb.UnimplementedTestServiceServer
}

func (testService) UnaryCall(_ context.Context, request *pb.SimpleRequest) (*pb.SimpleResponse, error) {
	if request.GetResponseSize() < 0 {
		return nil, status.Error(codes.InvalidArgument, "response_size must be non-negative")
	}
	return &pb.SimpleResponse{
		Payload: &pb.Payload{
			Type: pb.PayloadType_COMPRESSABLE,
			Body: make([]byte, int(request.GetResponseSize())),
		},
	}, nil
}

func main() {
	addr := flag.String("addr", "127.0.0.1:50052", "listen address")
	flag.Parse()

	listener, err := net.Listen("tcp", *addr)
	if err != nil {
		log.Fatal(err)
	}

	server := grpc.NewServer()
	pb.RegisterTestServiceServer(server, testService{})
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	go func() {
		<-ctx.Done()
		server.GracefulStop()
	}()
	if err := server.Serve(listener); err != nil {
		log.Fatal(err)
	}
}
