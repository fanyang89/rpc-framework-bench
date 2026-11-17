package main

import (
	"bufio"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"sort"
)

type result struct {
	Round                  int        `json:"round"`
	Label                  string     `json:"label"`
	ServerVersion          string     `json:"server_version"`
	ClientFrameworkVersion string     `json:"client_framework_version"`
	Errors                 uint64     `json:"errors"`
	OpsPerSecond           float64    `json:"ops_per_second"`
	P50US                  *float64   `json:"p50_us"`
	P95US                  *float64   `json:"p95_us"`
	P99US                  *float64   `json:"p99_us"`
	ServerCPU              *serverCPU `json:"server_cpu"`
	Config                 struct {
		scenario
		Concurrency      int `json:"concurrency"`
		Connections      int `json:"connections"`
		ClientProcesses  int `json:"client_processes"`
		TotalConcurrency int `json:"total_concurrency"`
		TotalConnections int `json:"total_connections"`
	} `json:"config"`
}

type scenario struct {
	Mode          string `json:"mode"`
	TLS           bool   `json:"tls"`
	Messages      int    `json:"messages"`
	ServerThreads int    `json:"server_threads"`
	CPPAPI        string `json:"cpp_api"`
	RequestSize   int    `json:"req_size"`
	ResponseSize  int    `json:"resp_size"`
	Warmup        string `json:"warmup"`
	Duration      string `json:"duration"`
}

type serverCPU struct {
	CPUSeconds         *float64 `json:"cpu_seconds"`
	WallSeconds        *float64 `json:"wall_seconds"`
	UtilizationPercent *float64 `json:"utilization_percent"`
}

type groupKey struct {
	scenario    scenario
	version     string
	client      string
	server      string
	processes   int
	concurrency int
	connections int
}

type metrics struct {
	ops []float64
	p50 []float64
	p95 []float64
	p99 []float64
	cpu []float64
}

func median(values []float64) (float64, bool) {
	if len(values) == 0 {
		return 0, false
	}
	sorted := append([]float64(nil), values...)
	sort.Float64s(sorted)
	middle := len(sorted) / 2
	if len(sorted)%2 == 1 {
		return sorted[middle], true
	}
	return (sorted[middle-1] + sorted[middle]) / 2, true
}

func formatMedian(values []float64) string {
	value, ok := median(values)
	if !ok {
		return "n/a"
	}
	return fmt.Sprintf("%.3f", value)
}

func readResults(path string, groups map[groupKey]*metrics) error {
	file, err := os.Open(path)
	if err != nil {
		return err
	}
	defer file.Close()

	scanner := bufio.NewScanner(file)
	scanner.Buffer(make([]byte, 64*1024), 1024*1024)
	lineNumber := 0
	for scanner.Scan() {
		lineNumber++
		if len(scanner.Bytes()) == 0 {
			continue
		}

		var record result
		if err := json.Unmarshal(scanner.Bytes(), &record); err != nil {
			return fmt.Errorf("%s:%d: %w", path, lineNumber, err)
		}
		processes := record.Config.ClientProcesses
		if processes == 0 {
			processes = 1
		}
		totalConcurrency := record.Config.TotalConcurrency
		if totalConcurrency == 0 {
			totalConcurrency = record.Config.Concurrency
		}
		totalConnections := record.Config.TotalConnections
		if totalConnections == 0 {
			totalConnections = record.Config.Connections
		}
		if record.Round <= 0 || record.ClientFrameworkVersion == "" || record.Label == "" ||
			processes <= 0 || totalConcurrency <= 0 || totalConnections <= 0 {
			return fmt.Errorf("%s:%d: missing round, client, server, or positive process/concurrency/connection counts", path, lineNumber)
		}
		if record.Errors != 0 {
			return fmt.Errorf("%s:%d: refusing result with %d errors", path, lineNumber, record.Errors)
		}
		if record.OpsPerSecond < 0 {
			return fmt.Errorf("%s:%d: throughput must be non-negative", path, lineNumber)
		}
		latencyCount := 0
		for _, latency := range []*float64{record.P50US, record.P95US, record.P99US} {
			if latency != nil {
				latencyCount++
				if *latency < 0 {
					return fmt.Errorf("%s:%d: latency must be non-negative or null", path, lineNumber)
				}
			}
		}
		if latencyCount != 0 && latencyCount != 3 {
			return fmt.Errorf("%s:%d: latency percentiles must all be numbers or all be null", path, lineNumber)
		}
		if record.ServerCPU != nil {
			cpu := record.ServerCPU
			if cpu.CPUSeconds == nil || cpu.WallSeconds == nil || cpu.UtilizationPercent == nil {
				return fmt.Errorf("%s:%d: server CPU telemetry is missing a field", path, lineNumber)
			}
			if *cpu.CPUSeconds < 0 || *cpu.WallSeconds <= 0 || *cpu.UtilizationPercent < 0 {
				return fmt.Errorf("%s:%d: server CPU telemetry must have non-negative CPU/utilization and positive wall time", path, lineNumber)
			}
		}

		key := groupKey{
			scenario:    record.Config.scenario,
			version:     record.ServerVersion,
			client:      record.ClientFrameworkVersion,
			server:      record.Label,
			processes:   processes,
			concurrency: totalConcurrency,
			connections: totalConnections,
		}
		group := groups[key]
		if group == nil {
			group = &metrics{}
			groups[key] = group
		}
		group.ops = append(group.ops, record.OpsPerSecond)
		if record.P50US != nil {
			group.p50 = append(group.p50, *record.P50US)
			group.p95 = append(group.p95, *record.P95US)
			group.p99 = append(group.p99, *record.P99US)
		}
		if record.ServerCPU != nil {
			group.cpu = append(group.cpu, *record.ServerCPU.UtilizationPercent)
		}
	}
	if err := scanner.Err(); err != nil {
		return fmt.Errorf("%s: %w", path, err)
	}
	return nil
}

func run() error {
	if len(os.Args) < 2 {
		return errors.New("usage: go run ./scripts/summarize.go <result.jsonl> [result.jsonl ...]")
	}

	groups := make(map[groupKey]*metrics)
	for _, path := range os.Args[1:] {
		if err := readResults(path, groups); err != nil {
			return err
		}
	}
	if len(groups) == 0 {
		return errors.New("no benchmark results found")
	}

	keys := make([]groupKey, 0, len(groups))
	for key := range groups {
		keys = append(keys, key)
	}
	sort.Slice(keys, func(i, j int) bool {
		if keys[i].scenario != keys[j].scenario {
			return fmt.Sprint(keys[i].scenario) < fmt.Sprint(keys[j].scenario)
		}
		if keys[i].version != keys[j].version {
			return keys[i].version < keys[j].version
		}
		if keys[i].client != keys[j].client {
			return keys[i].client < keys[j].client
		}
		if keys[i].processes != keys[j].processes {
			return keys[i].processes < keys[j].processes
		}
		if keys[i].concurrency != keys[j].concurrency {
			return keys[i].concurrency < keys[j].concurrency
		}
		if keys[i].connections != keys[j].connections {
			return keys[i].connections < keys[j].connections
		}
		return keys[i].server < keys[j].server
	})

	fmt.Println("client\tserver\tclient_processes\ttotal_concurrency\ttotal_connections\trounds\tmedian_ops_per_second\tmedian_p50_us\tmedian_p95_us\tmedian_p99_us\tmin_ops_per_second\tmax_ops_per_second\tmedian_server_cpu_utilization_percent\tserver_cpu_telemetry_rounds\tserver_version\tscenario")
	for _, key := range keys {
		group := groups[key]
		sort.Float64s(group.ops)
		medianOps, _ := median(group.ops)
		scenarioJSON, _ := json.Marshal(key.scenario)
		fmt.Printf("%s\t%s\t%d\t%d\t%d\t%d\t%.3f\t%s\t%s\t%s\t%.3f\t%.3f\t%s\t%d\t%s\t%s\n",
			key.client,
			key.server,
			key.processes,
			key.concurrency,
			key.connections,
			len(group.ops),
			medianOps,
			formatMedian(group.p50),
			formatMedian(group.p95),
			formatMedian(group.p99),
			group.ops[0],
			group.ops[len(group.ops)-1],
			formatMedian(group.cpu),
			len(group.cpu),
			key.version, scenarioJSON,
		)
	}
	return nil
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
