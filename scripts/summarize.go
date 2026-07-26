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
	Round                  int     `json:"round"`
	Label                  string  `json:"label"`
	ClientFrameworkVersion string  `json:"client_framework_version"`
	Errors                 uint64  `json:"errors"`
	OpsPerSecond           float64 `json:"ops_per_second"`
	P50US                  float64 `json:"p50_us"`
	P95US                  float64 `json:"p95_us"`
	P99US                  float64 `json:"p99_us"`
	Config                 struct {
		Concurrency int `json:"concurrency"`
	} `json:"config"`
}

type groupKey struct {
	client      string
	server      string
	concurrency int
}

type metrics struct {
	ops []float64
	p50 []float64
	p95 []float64
	p99 []float64
}

func median(values []float64) float64 {
	sort.Float64s(values)
	middle := len(values) / 2
	if len(values)%2 == 1 {
		return values[middle]
	}
	return (values[middle-1] + values[middle]) / 2
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
		if record.Round <= 0 || record.ClientFrameworkVersion == "" || record.Label == "" || record.Config.Concurrency <= 0 {
			return fmt.Errorf("%s:%d: missing round, client, server, or positive concurrency", path, lineNumber)
		}
		if record.Errors != 0 {
			return fmt.Errorf("%s:%d: refusing result with %d errors", path, lineNumber, record.Errors)
		}
		if record.OpsPerSecond < 0 || record.P50US < 0 || record.P95US < 0 || record.P99US < 0 {
			return fmt.Errorf("%s:%d: metrics must be non-negative", path, lineNumber)
		}

		key := groupKey{
			client:      record.ClientFrameworkVersion,
			server:      record.Label,
			concurrency: record.Config.Concurrency,
		}
		group := groups[key]
		if group == nil {
			group = &metrics{}
			groups[key] = group
		}
		group.ops = append(group.ops, record.OpsPerSecond)
		group.p50 = append(group.p50, record.P50US)
		group.p95 = append(group.p95, record.P95US)
		group.p99 = append(group.p99, record.P99US)
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
		if keys[i].client != keys[j].client {
			return keys[i].client < keys[j].client
		}
		if keys[i].concurrency != keys[j].concurrency {
			return keys[i].concurrency < keys[j].concurrency
		}
		return keys[i].server < keys[j].server
	})

	fmt.Println("client\tserver\tconcurrency\trounds\tmedian_ops_per_second\tmedian_p50_us\tmedian_p95_us\tmedian_p99_us\tmin_ops_per_second\tmax_ops_per_second")
	for _, key := range keys {
		group := groups[key]
		sort.Float64s(group.ops)
		fmt.Printf("%s\t%s\t%d\t%d\t%.3f\t%.3f\t%.3f\t%.3f\t%.3f\t%.3f\n",
			key.client,
			key.server,
			key.concurrency,
			len(group.ops),
			median(group.ops),
			median(group.p50),
			median(group.p95),
			median(group.p99),
			group.ops[0],
			group.ops[len(group.ops)-1],
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
