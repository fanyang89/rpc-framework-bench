package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"math"
	"os"
	"slices"
	"sync"
	"sync/atomic"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/credentials/insecure"
	pb "google.golang.org/grpc/interop/grpc_testing"
)

type config struct {
	Addr         string        `json:"addr"`
	Warmup       time.Duration `json:"-"`
	Duration     time.Duration `json:"-"`
	WarmupText   string        `json:"warmup"`
	DurationText string        `json:"duration"`
	Concurrency  int           `json:"concurrency"`
	Connections  int           `json:"connections"`
	RequestSize  int           `json:"req_size"`
	ResponseSize int           `json:"resp_size"`
	Mode         string        `json:"mode"`
	Messages     int           `json:"messages"`
	TLSCA        string        `json:"tls_ca"`
}

type result struct {
	Round                  int     `json:"round"`
	Label                  string  `json:"label"`
	ClientFrameworkVersion string  `json:"client_framework_version"`
	ServerVersion          string  `json:"server_version"`
	Config                 config  `json:"config"`
	Completed              int     `json:"completed"`
	Errors                 int64   `json:"errors"`
	OpsPerSecond           float64 `json:"ops_per_second"`
	P50US                  float64 `json:"p50_us"`
	P95US                  float64 `json:"p95_us"`
	P99US                  float64 `json:"p99_us"`
	MinUS                  float64 `json:"min_us"`
	MaxUS                  float64 `json:"max_us"`
	MeanUS                 float64 `json:"mean_us"`
}

type workerResult struct {
	latencies []int64
	errors    int64
}

func validateResponse(response *pb.SimpleResponse, expectedSize int) error {
	if response.GetPayload() == nil {
		return errors.New("response payload is missing")
	}
	if len(response.GetPayload().GetBody()) != expectedSize {
		return fmt.Errorf("response body length is %d, want %d", len(response.GetPayload().GetBody()), expectedSize)
	}
	return nil
}

func percentile(sorted []int64, fraction float64) float64 {
	if len(sorted) == 0 {
		return 0
	}
	index := int(math.Ceil(fraction*float64(len(sorted)))) - 1
	if index < 0 {
		index = 0
	}
	return float64(sorted[index]) / 1000
}

// One operation is one complete RPC, including all messages and final status.
func operation(client pb.TestServiceClient, cfg config, request *pb.SimpleRequest) func() error {
	parameters := make([]*pb.ResponseParameters, cfg.Messages)
	for i := range parameters {
		parameters[i] = &pb.ResponseParameters{Size: int32(cfg.ResponseSize)}
	}
	outputRequest := &pb.StreamingOutputCallRequest{Payload: request.Payload, ResponseParameters: parameters}
	pingRequest := &pb.StreamingOutputCallRequest{Payload: request.Payload, ResponseParameters: parameters[:1]}
	return func() error {
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		switch cfg.Mode {
		case "unary":
			response, err := client.UnaryCall(ctx, request)
			if err != nil {
				return err
			}
			return validateResponse(response, cfg.ResponseSize)
		case "client-stream":
			stream, err := client.StreamingInputCall(ctx)
			if err != nil {
				return err
			}
			for range cfg.Messages {
				if err := stream.Send(&pb.StreamingInputCallRequest{Payload: request.Payload}); err != nil {
					return err
				}
			}
			response, err := stream.CloseAndRecv()
			if err != nil {
				return err
			}
			if int(response.AggregatedPayloadSize) != cfg.Messages*cfg.RequestSize {
				return errors.New("wrong aggregate payload size")
			}
			return nil
		case "server-stream":
			stream, err := client.StreamingOutputCall(ctx, outputRequest)
			if err != nil {
				return err
			}
			for range cfg.Messages {
				response, err := stream.Recv()
				if err != nil {
					return err
				}
				if err := validateResponse(&pb.SimpleResponse{Payload: response.Payload}, cfg.ResponseSize); err != nil {
					return err
				}
			}
			if _, err := stream.Recv(); err != io.EOF {
				return fmt.Errorf("expected final OK/EOF, got %v", err)
			}
			return nil
		case "bidi":
			stream, err := client.FullDuplexCall(ctx)
			if err != nil {
				return err
			}
			for range cfg.Messages {
				if err := stream.Send(pingRequest); err != nil {
					return err
				}
				response, err := stream.Recv()
				if err != nil {
					return err
				}
				if err := validateResponse(&pb.SimpleResponse{Payload: response.Payload}, cfg.ResponseSize); err != nil {
					return err
				}
			}
			if err := stream.CloseSend(); err != nil {
				return err
			}
			if _, err := stream.Recv(); err != io.EOF {
				return fmt.Errorf("expected final OK/EOF, got %v", err)
			}
			return nil
		}
		return errors.New("unknown mode")
	}
}

func run() error {
	var cfg config
	var label string
	var serverVersion string
	var compact bool
	var round int
	flag.StringVar(&cfg.Addr, "addr", "127.0.0.1:50051", "server address")
	flag.DurationVar(&cfg.Warmup, "warmup", time.Second, "per-worker warmup duration")
	flag.DurationVar(&cfg.Duration, "duration", 10*time.Second, "measurement duration")
	flag.IntVar(&cfg.Concurrency, "concurrency", 4, "number of closed-loop workers")
	flag.IntVar(&cfg.Connections, "connections", 1, "number of client connections")
	flag.IntVar(&cfg.RequestSize, "req_size", 1024, "request payload body size")
	flag.IntVar(&cfg.ResponseSize, "resp_size", 1024, "response payload body size")
	flag.StringVar(&label, "label", "", "result label")
	flag.StringVar(&serverVersion, "server_version", "", "server framework version")
	flag.IntVar(&round, "round", 0, "benchmark round number")
	flag.BoolVar(&compact, "compact", false, "emit compact JSON")
	flag.StringVar(&cfg.Mode, "mode", "unary", "unary, client-stream, server-stream, bidi")
	flag.IntVar(&cfg.Messages, "messages", 16, "messages per streaming RPC")
	flag.StringVar(&cfg.TLSCA, "tls_ca", "", "trusted TLS CA; empty means plaintext")
	flag.Parse()

	if cfg.Concurrency <= 0 || cfg.Connections <= 0 {
		return errors.New("concurrency and connections must be positive")
	}
	if cfg.Warmup < 0 || cfg.Duration <= 0 {
		return errors.New("warmup must be non-negative and duration must be positive")
	}
	if cfg.RequestSize < 0 || cfg.ResponseSize < 0 {
		return errors.New("req_size and resp_size must be non-negative")
	}
	if cfg.RequestSize > math.MaxInt32 || cfg.ResponseSize > math.MaxInt32 {
		return errors.New("req_size and resp_size must fit in int32")
	}
	if round < 0 {
		return errors.New("round must be non-negative")
	}
	if !slices.Contains([]string{"unary", "client-stream", "server-stream", "bidi"}, cfg.Mode) ||
		cfg.Messages <= 0 || cfg.Messages > 1024 || int64(cfg.Messages)*int64(cfg.RequestSize) > math.MaxInt32 {
		return errors.New("invalid mode or message count/aggregate size")
	}
	cfg.WarmupText = cfg.Warmup.String()
	cfg.DurationText = cfg.Duration.String()

	connections := make([]*grpc.ClientConn, cfg.Connections)
	clients := make([]pb.TestServiceClient, cfg.Connections)
	var transport credentials.TransportCredentials = insecure.NewCredentials()
	if cfg.TLSCA != "" {
		var err error
		transport, err = credentials.NewClientTLSFromFile(cfg.TLSCA, "localhost")
		if err != nil {
			return err
		}
	}
	for i := range connections {
		connection, err := grpc.NewClient(
			cfg.Addr,
			grpc.WithTransportCredentials(transport),
			grpc.WithDisableRetry(),
		)
		if err != nil {
			return err
		}
		connections[i] = connection
		clients[i] = pb.NewTestServiceClient(connection)
	}
	defer func() {
		for _, connection := range connections {
			_ = connection.Close()
		}
	}()

	request := &pb.SimpleRequest{
		ResponseType: pb.PayloadType_COMPRESSABLE,
		ResponseSize: int32(cfg.ResponseSize),
		Payload: &pb.Payload{
			Type: pb.PayloadType_COMPRESSABLE,
			Body: make([]byte, cfg.RequestSize),
		},
	}

	warmupGate := make(chan struct{})
	ready := make(chan struct{}, cfg.Concurrency)
	measurementGate := make(chan struct{})
	results := make(chan workerResult, cfg.Concurrency)
	var measurementStart atomic.Int64
	var workers sync.WaitGroup
	for workerID := range cfg.Concurrency {
		client := clients[workerID%len(clients)]
		workers.Go(func() {
			call := operation(client, cfg, request)
			local := workerResult{latencies: make([]int64, 0, 1024)}
			<-warmupGate
			warmupEnd := time.Now().Add(cfg.Warmup)
			for time.Now().Before(warmupEnd) {
				if err := call(); err != nil {
					local.errors++
				}
			}
			ready <- struct{}{}
			<-measurementGate
			startAt := time.Unix(0, measurementStart.Load())
			time.Sleep(time.Until(startAt))
			endAt := startAt.Add(cfg.Duration)
			for {
				started := time.Now()
				if !started.Before(endAt) {
					break
				}
				err := call()
				latency := time.Since(started)
				if err != nil {
					local.errors++
					continue
				}
				local.latencies = append(local.latencies, latency.Nanoseconds())
			}
			results <- local
		})
	}
	close(warmupGate)
	for range cfg.Concurrency {
		<-ready
	}
	startAt := time.Now().Add(10 * time.Millisecond)
	measurementStart.Store(startAt.UnixNano())
	close(measurementGate)
	workers.Wait()
	close(results)

	var latencies []int64
	var errorCount int64
	for worker := range results {
		latencies = append(latencies, worker.latencies...)
		errorCount += worker.errors
	}
	slices.Sort(latencies)
	var total int64
	for _, latency := range latencies {
		total += latency
	}

	output := result{
		Round:                  round,
		Label:                  label,
		ClientFrameworkVersion: "grpc-go/" + grpc.Version,
		ServerVersion:          serverVersion,
		Config:                 cfg,
		Completed:              len(latencies),
		Errors:                 errorCount,
		OpsPerSecond:           float64(len(latencies)) / cfg.Duration.Seconds(),
		P50US:                  percentile(latencies, 0.50),
		P95US:                  percentile(latencies, 0.95),
		P99US:                  percentile(latencies, 0.99),
	}
	if len(latencies) > 0 {
		output.MinUS = float64(latencies[0]) / 1000
		output.MaxUS = float64(latencies[len(latencies)-1]) / 1000
		output.MeanUS = float64(total) / float64(len(latencies)) / 1000
	}

	encoder := json.NewEncoder(os.Stdout)
	if !compact {
		encoder.SetIndent("", "  ")
	}
	if err := encoder.Encode(output); err != nil {
		return err
	}
	if errorCount != 0 {
		return fmt.Errorf("encountered %d RPC or validation errors", errorCount)
	}
	return nil
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
