package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

func TestMedianDoesNotMutateInput(t *testing.T) {
	values := []float64{99, 95, 97, 93}
	wantValues := append([]float64(nil), values...)

	got, ok := median(values)
	if !ok || got != 96 {
		t.Fatalf("median(%v) = %v, %v; want 96, true", values, got, ok)
	}
	if !reflect.DeepEqual(values, wantValues) {
		t.Fatalf("median mutated input: got %v, want %v", values, wantValues)
	}
}

func TestFormatMedianTelemetry(t *testing.T) {
	tests := []struct {
		name   string
		values []float64
		want   string
	}{
		{name: "missing legacy telemetry", want: "n/a"},
		{name: "odd rounds", values: []float64{98.2, 96.4, 97.1}, want: "97.100"},
		{name: "even rounds", values: []float64{98, 96}, want: "97.000"},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if got := formatMedian(test.values); got != test.want {
				t.Fatalf("formatMedian(%v) = %q; want %q", test.values, got, test.want)
			}
		})
	}
}

func TestReadResultsMixedTelemetryFixture(t *testing.T) {
	groups := make(map[groupKey]*metrics)
	if err := readResults("testdata/mixed-telemetry.jsonl", groups); err != nil {
		t.Fatal(err)
	}

	key := groupKey{client: "fixture-client", server: "fixture-server", processes: 1, concurrency: 4, connections: 1}
	group := groups[key]
	if group == nil {
		t.Fatalf("missing group %v", key)
	}
	if len(group.ops) != 3 {
		t.Fatalf("round count = %d; want 3", len(group.ops))
	}
	if len(group.cpu) != 2 {
		t.Fatalf("telemetry count = %d; want 2", len(group.cpu))
	}
	if got := formatMedian(group.cpu); got != "97.000" {
		t.Fatalf("telemetry median = %q; want 97.000", got)
	}
}

func TestReadResultsLegacySchemaFallback(t *testing.T) {
	groups := make(map[groupKey]*metrics)
	if err := readResults("testdata/old-schema.jsonl", groups); err != nil {
		t.Fatal(err)
	}

	key := groupKey{client: "legacy-client", server: "legacy-server", processes: 1, concurrency: 4, connections: 1,
		version: "legacy-server/1", scenario: scenario{RequestSize: 1024, ResponseSize: 1024, Warmup: "2s", Duration: "10s"}}
	group := groups[key]
	if group == nil {
		t.Fatalf("missing legacy group %v", key)
	}
	if len(group.p50) != 1 || len(group.cpu) != 0 {
		t.Fatalf("legacy metric counts = latency %d, CPU %d; want 1, 0", len(group.p50), len(group.cpu))
	}
}

func TestReadResultsSingleProcessTelemetry(t *testing.T) {
	groups := make(map[groupKey]*metrics)
	if err := readResults("testdata/single-telemetry.jsonl", groups); err != nil {
		t.Fatal(err)
	}

	key := groupKey{client: "single-client", server: "single-server", processes: 1, concurrency: 256, connections: 8,
		version: "single-server/1", scenario: scenario{RequestSize: 1024, ResponseSize: 1024, Warmup: "2s", Duration: "10s"}}
	group := groups[key]
	if group == nil {
		t.Fatalf("missing single-process group %v", key)
	}
	if len(group.p50) != 1 || len(group.cpu) != 1 {
		t.Fatalf("single-process metric counts = latency %d, CPU %d; want 1, 1", len(group.p50), len(group.cpu))
	}
}

func TestReadResultsAggregateGroupingAndNullableLatency(t *testing.T) {
	groups := make(map[groupKey]*metrics)
	if err := readResults("testdata/multi-aggregate.jsonl", groups); err != nil {
		t.Fatal(err)
	}
	if len(groups) != 3 {
		t.Fatalf("group count = %d; want 3", len(groups))
	}

	key := groupKey{client: "aggregate-client", server: "aggregate-server", processes: 2, concurrency: 512, connections: 16,
		version: "aggregate-server/1", scenario: scenario{RequestSize: 1024, ResponseSize: 1024, Warmup: "2s", Duration: "10s"}}
	group := groups[key]
	if group == nil {
		t.Fatalf("missing aggregate group %v", key)
	}
	if len(group.ops) != 2 {
		t.Fatalf("aggregate round count = %d; want 2", len(group.ops))
	}
	if got := formatMedian(group.p50); got != "n/a" {
		t.Fatalf("aggregate latency = %q; want n/a", got)
	}
	if got := formatMedian(group.cpu); got != "96.500" {
		t.Fatalf("aggregate CPU median = %q; want 96.500", got)
	}

	connectionsKey := key
	connectionsKey.connections = 8
	if groups[connectionsKey] == nil {
		t.Fatalf("different total connections were merged: missing %v", connectionsKey)
	}
	processesKey := key
	processesKey.processes = 4
	if groups[processesKey] == nil {
		t.Fatalf("different process counts were merged: missing %v", processesKey)
	}
}

func TestSeparateScenarios(t *testing.T) {
	base := result{Round: 1, Label: "server", ClientFrameworkVersion: "client", ServerVersion: "v1"}
	base.Config.Concurrency, base.Config.Connections = 4, 1
	variants := []result{base}
	for _, change := range []func(*result){
		func(r *result) { r.Config.Mode = "bidi" },
		func(r *result) { r.Config.TLS = true },
		func(r *result) { r.Config.ServerThreads = 4 },
		func(r *result) { r.Config.CPPAPI = "callback" },
		func(r *result) { r.Config.Messages = 16 },
		func(r *result) { r.Config.RequestSize = 65536 },
		func(r *result) { r.ServerVersion = "v2" },
	} {
		record := base
		change(&record)
		variants = append(variants, record)
	}
	path := filepath.Join(t.TempDir(), "results.jsonl")
	file, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	for _, record := range variants {
		if err := json.NewEncoder(file).Encode(record); err != nil {
			t.Fatal(err)
		}
	}
	if err := file.Close(); err != nil {
		t.Fatal(err)
	}
	groups := make(map[groupKey]*metrics)
	if err := readResults(path, groups); err != nil {
		t.Fatal(err)
	}
	if len(groups) != len(variants) {
		t.Fatalf("merged distinct scenarios: %d groups", len(groups))
	}
}
