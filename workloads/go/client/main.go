package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"math"
	"math/bits"
	"os"
	"slices"
	"sync"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/connectivity"
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

// Log-linear histogram with 8-bit mantissa (256 sub-buckets per octave, <0.4% error,
// exact below 512 ns, covers up to ~68.7 s) for O(1) zero-allocation latency recording.
const (
	histSubBits = 8
	histSubSize = 1 << histSubBits
	histBuckets = histSubSize * 30
)

type latencyHistogram struct {
	buckets [histBuckets]uint64
	count   uint64
	sumNS   uint64
	minNS   uint64
	maxNS   uint64
}

func histBucket(ns uint64) int {
	if ns < 2*histSubSize {
		return int(ns)
	}
	msb := 63 - bits.LeadingZeros64(ns)
	shift := uint(msb - histSubBits)
	octave := int(shift) + 1
	sub := int((ns >> shift) & (histSubSize - 1))
	idx := octave*histSubSize + sub
	if idx >= histBuckets {
		return histBuckets - 1
	}
	return idx
}

func histValue(idx int) uint64 {
	if idx < 2*histSubSize {
		return uint64(idx)
	}
	octave := idx >> histSubBits
	sub := uint64(idx & (histSubSize - 1))
	shift := uint(octave - 1)
	return (histSubSize + sub) << shift
}

func (h *latencyHistogram) record(ns int64) {
	if ns < 0 {
		ns = 0
	}
	v := uint64(ns)
	h.buckets[histBucket(v)]++
	if h.count == 0 || v < h.minNS {
		h.minNS = v
	}
	if v > h.maxNS {
		h.maxNS = v
	}
	h.count++
	h.sumNS += v
}

func (h *latencyHistogram) merge(other *latencyHistogram) {
	if other.count == 0 {
		return
	}
	if h.count == 0 || other.minNS < h.minNS {
		h.minNS = other.minNS
	}
	if other.maxNS > h.maxNS {
		h.maxNS = other.maxNS
	}
	h.count += other.count
	h.sumNS += other.sumNS
	for i := range h.buckets {
		h.buckets[i] += other.buckets[i]
	}
}

func (h *latencyHistogram) percentileUS(fraction float64) float64 {
	if h.count == 0 {
		return 0
	}
	rank := uint64(math.Ceil(fraction * float64(h.count)))
	if rank == 0 {
		rank = 1
	}
	var seen uint64
	for i, c := range h.buckets {
		seen += c
		if seen >= rank {
			v := histValue(i)
			if v < h.minNS {
				v = h.minNS
			}
			if v > h.maxNS {
				v = h.maxNS
			}
			return float64(v) / 1000.0
		}
	}
	return float64(h.maxNS) / 1000.0
}

type workerResult struct {
	hist          latencyHistogram
	errors        int64
	activeSeconds float64
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

// One operation is one complete RPC, including all messages and final status.
func operation(client pb.TestServiceClient, cfg config, request *pb.SimpleRequest) func() error {
	parameters := make([]*pb.ResponseParameters, cfg.Messages)
	for i := range parameters {
		parameters[i] = &pb.ResponseParameters{Size: int32(cfg.ResponseSize)}
	}
	inputRequest := &pb.StreamingInputCallRequest{Payload: request.Payload}
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
				if err := stream.Send(inputRequest); err != nil {
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

func waitReady(conn *grpc.ClientConn, timeout time.Duration) error {
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	conn.Connect()
	for {
		state := conn.GetState()
		if state == connectivity.Ready {
			return nil
		}
		if !conn.WaitForStateChange(ctx, state) {
			return fmt.Errorf("connection did not become ready within %s (last state: %s)", timeout, state)
		}
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
	// Connect eagerly before warmup so TCP/TLS handshakes are never charged to measurement.
	for _, connection := range connections {
		if err := waitReady(connection, 10*time.Second); err != nil {
			return err
		}
	}

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
	// Plain time.Time written before close(measurementGate) preserves Go's monotonic clock.
	var measurementStart time.Time
	var workers sync.WaitGroup
	for workerID := range cfg.Concurrency {
		client := clients[workerID%len(clients)]
		workers.Go(func() {
			call := operation(client, cfg, request)
			var local workerResult
			<-warmupGate
			warmupEnd := time.Now().Add(cfg.Warmup)
			for time.Now().Before(warmupEnd) {
				if err := call(); err != nil {
					local.errors++
				}
			}
			ready <- struct{}{}
			<-measurementGate
			startAt := measurementStart
			time.Sleep(time.Until(startAt))
			endAt := startAt.Add(cfg.Duration)
			now := time.Now()
			for now.Before(endAt) {
				started := now
				err := call()
				now = time.Now()
				if err != nil {
					local.errors++
					continue
				}
				local.hist.record(now.Sub(started).Nanoseconds())
			}
			if now.After(startAt) {
				local.activeSeconds = now.Sub(startAt).Seconds()
			} else {
				local.activeSeconds = cfg.Duration.Seconds()
			}
			results <- local
		})
	}
	close(warmupGate)
	for range cfg.Concurrency {
		<-ready
	}
	measurementStart = time.Now().Add(10 * time.Millisecond)
	close(measurementGate)
	workers.Wait()
	close(results)

	var combined latencyHistogram
	var errorCount int64
	var opsPerSecond float64
	for worker := range results {
		combined.merge(&worker.hist)
		errorCount += worker.errors
		if worker.hist.count > 0 && worker.activeSeconds > 0 {
			opsPerSecond += float64(worker.hist.count) / worker.activeSeconds
		}
	}

	output := result{
		Round:                  round,
		Label:                  label,
		ClientFrameworkVersion: "grpc-go/" + grpc.Version,
		ServerVersion:          serverVersion,
		Config:                 cfg,
		Completed:              int(combined.count),
		Errors:                 errorCount,
		OpsPerSecond:           opsPerSecond,
		P50US:                  combined.percentileUS(0.50),
		P95US:                  combined.percentileUS(0.95),
		P99US:                  combined.percentileUS(0.99),
	}
	if combined.count > 0 {
		output.MinUS = float64(combined.minNS) / 1000.0
		output.MaxUS = float64(combined.maxNS) / 1000.0
		output.MeanUS = float64(combined.sumNS) / float64(combined.count) / 1000.0
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
