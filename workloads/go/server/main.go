package main

import (
	"context"
	"flag"
	"io"
	"log"
	"net"
	"os/signal"
	"syscall"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials"
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

func (testService) StreamingOutputCall(request *pb.StreamingOutputCallRequest, stream pb.TestService_StreamingOutputCallServer) error {
	for _, parameter := range request.ResponseParameters {
		if parameter.Size < 0 {
			return status.Error(codes.InvalidArgument, "negative response size")
		}
		if err := stream.Send(&pb.StreamingOutputCallResponse{Payload: &pb.Payload{Body: make([]byte, parameter.Size)}}); err != nil {
			return err
		}
	}
	return nil
}

func (testService) StreamingInputCall(stream pb.TestService_StreamingInputCallServer) error {
	var total int32
	for {
		request, err := stream.Recv()
		if err == io.EOF {
			return stream.SendAndClose(&pb.StreamingInputCallResponse{AggregatedPayloadSize: total})
		}
		if err != nil {
			return err
		}
		size := len(request.GetPayload().GetBody())
		if int64(total)+int64(size) > 2147483647 {
			return status.Error(codes.InvalidArgument, "aggregate exceeds int32")
		}
		total += int32(size)
	}
}

func (service testService) FullDuplexCall(stream pb.TestService_FullDuplexCallServer) error {
	for {
		request, err := stream.Recv()
		if err == io.EOF {
			return nil
		}
		if err != nil {
			return err
		}
		if err := service.StreamingOutputCall(request, stream); err != nil {
			return err
		}
	}
}

func main() {
	addr := flag.String("addr", "127.0.0.1:50052", "listen address")
	cert := flag.String("cert", "", "TLS certificate")
	key := flag.String("key", "", "TLS key")
	flag.Parse()

	listener, err := net.Listen("tcp", *addr)
	if err != nil {
		log.Fatal(err)
	}

	var options []grpc.ServerOption
	if *cert != "" || *key != "" {
		creds, err := credentials.NewServerTLSFromFile(*cert, *key)
		if err != nil {
			log.Fatal(err)
		}
		options = append(options, grpc.Creds(creds))
	}
	server := grpc.NewServer(options...)
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
