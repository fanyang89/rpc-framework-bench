use std::{env, error::Error, process, time::Duration};

use grpc_testing::{
    Payload, PayloadType, SimpleRequest, SimpleResponse, test_service_client::TestServiceClient,
};
use serde::Serialize;
use tokio::{sync::oneshot, task::JoinHandle, time::Instant};
use tonic::transport::{Channel, Endpoint};

mod grpc_testing {
    tonic::include_proto!("grpc.testing");
}

#[derive(Serialize)]
struct Config {
    addr: String,
    #[serde(skip)]
    warmup: Duration,
    #[serde(skip)]
    duration: Duration,
    #[serde(rename = "warmup")]
    warmup_text: String,
    #[serde(rename = "duration")]
    duration_text: String,
    concurrency: usize,
    connections: usize,
    req_size: usize,
    resp_size: usize,
}

struct Options {
    config: Config,
    label: String,
    server_version: String,
    round: u64,
    compact: bool,
}

#[derive(Serialize)]
struct BenchmarkResult {
    round: u64,
    label: String,
    client_framework_version: &'static str,
    server_version: String,
    config: Config,
    completed: usize,
    errors: u64,
    ops_per_second: f64,
    p50_us: f64,
    p95_us: f64,
    p99_us: f64,
    min_us: f64,
    max_us: f64,
    mean_us: f64,
}

struct WorkerResult {
    latencies: Vec<u64>,
    errors: u64,
}

impl Default for Options {
    fn default() -> Self {
        Self {
            config: Config {
                addr: "127.0.0.1:50051".to_owned(),
                warmup: Duration::from_secs(1),
                duration: Duration::from_secs(10),
                warmup_text: "1s".to_owned(),
                duration_text: "10s".to_owned(),
                concurrency: 4,
                connections: 1,
                req_size: 1024,
                resp_size: 1024,
            },
            label: String::new(),
            server_version: String::new(),
            round: 0,
            compact: false,
        }
    }
}

fn parse_duration(value: &str) -> Result<Duration, String> {
    if value == "0" {
        return Ok(Duration::ZERO);
    }
    if value.is_empty() || value.starts_with(['-', '+']) {
        return Err(format!("invalid duration {value:?}"));
    }

    let mut rest = value;
    let mut seconds = 0.0;
    while !rest.is_empty() {
        let number_end = rest
            .char_indices()
            .find_map(|(index, character)| {
                (!character.is_ascii_digit() && character != '.').then_some(index)
            })
            .unwrap_or(rest.len());
        if number_end == 0 || number_end == rest.len() {
            return Err(format!("invalid duration {value:?}"));
        }
        let number: f64 = rest[..number_end]
            .parse()
            .map_err(|_| format!("invalid duration {value:?}"))?;
        rest = &rest[number_end..];

        let (unit, multiplier) = [
            ("ns", 1e-9),
            ("us", 1e-6),
            ("µs", 1e-6),
            ("μs", 1e-6),
            ("ms", 1e-3),
            ("s", 1.0),
            ("m", 60.0),
            ("h", 3600.0),
        ]
        .into_iter()
        .find(|(unit, _)| rest.starts_with(unit))
        .ok_or_else(|| format!("invalid duration {value:?}"))?;
        seconds += number * multiplier;
        rest = &rest[unit.len()..];
    }

    if !seconds.is_finite() {
        return Err(format!("invalid duration {value:?}"));
    }
    Duration::try_from_secs_f64(seconds).map_err(|_| format!("invalid duration {value:?}"))
}

fn parse_bool(value: &str) -> Result<bool, String> {
    match value {
        "1" | "t" | "T" | "true" | "TRUE" | "True" => Ok(true),
        "0" | "f" | "F" | "false" | "FALSE" | "False" => Ok(false),
        _ => Err(format!("invalid boolean {value:?}")),
    }
}

fn parse_args() -> Result<Options, String> {
    let mut options = Options::default();
    let mut args = env::args().skip(1);
    while let Some(argument) = args.next() {
        let flag = argument
            .strip_prefix("--")
            .or_else(|| argument.strip_prefix('-'))
            .ok_or_else(|| format!("unexpected positional argument {argument:?}"))?;
        let (name, inline_value) = flag
            .split_once('=')
            .map_or((flag, None), |(name, value)| (name, Some(value)));

        if name == "compact" {
            options.compact = inline_value.map_or(Ok(true), parse_bool)?;
            continue;
        }

        let value = inline_value
            .map(str::to_owned)
            .or_else(|| args.next())
            .ok_or_else(|| format!("missing value for -{name}"))?;
        match name {
            "addr" => options.config.addr = value,
            "warmup" => {
                options.config.warmup = parse_duration(&value)?;
                options.config.warmup_text = value;
            }
            "duration" => {
                options.config.duration = parse_duration(&value)?;
                options.config.duration_text = value;
            }
            "concurrency" => {
                options.config.concurrency = value
                    .parse()
                    .map_err(|_| format!("invalid concurrency {value:?}"))?;
            }
            "connections" => {
                options.config.connections = value
                    .parse()
                    .map_err(|_| format!("invalid connections {value:?}"))?;
            }
            "label" => options.label = value,
            "server_version" => options.server_version = value,
            "round" => {
                options.round = value
                    .parse()
                    .map_err(|_| format!("invalid round {value:?}"))?;
            }
            "req_size" => {
                options.config.req_size = value
                    .parse()
                    .map_err(|_| format!("invalid req_size {value:?}"))?;
            }
            "resp_size" => {
                options.config.resp_size = value
                    .parse()
                    .map_err(|_| format!("invalid resp_size {value:?}"))?;
            }
            _ => return Err(format!("unknown flag -{name}")),
        }
    }

    let config = &options.config;
    if config.concurrency == 0 || config.connections == 0 {
        return Err("concurrency and connections must be positive".to_owned());
    }
    if config.duration.is_zero() {
        return Err("duration must be positive".to_owned());
    }
    if config.req_size > i32::MAX as usize || config.resp_size > i32::MAX as usize {
        return Err("req_size and resp_size must fit in int32".to_owned());
    }
    Ok(options)
}

fn validate_response(response: &SimpleResponse, expected_size: usize) -> bool {
    response
        .payload
        .as_ref()
        .is_some_and(|payload| payload.body.len() == expected_size)
}

async fn run_worker(
    mut client: TestServiceClient<Channel>,
    request: SimpleRequest,
    warmup: Duration,
    duration: Duration,
    expected_response_size: usize,
    ready: tokio::sync::mpsc::Sender<()>,
    measurement_start: oneshot::Receiver<Instant>,
) -> WorkerResult {
    let mut result = WorkerResult {
        latencies: Vec::with_capacity(4096),
        errors: 0,
    };

    let warmup_end = Instant::now() + warmup;
    while Instant::now() < warmup_end {
        match client.unary_call(request.clone()).await {
            Ok(response) if validate_response(response.get_ref(), expected_response_size) => {}
            _ => result.errors += 1,
        }
    }

    ready
        .send(())
        .await
        .expect("measurement coordinator dropped");
    let start_at = measurement_start
        .await
        .expect("measurement coordinator dropped");
    tokio::time::sleep_until(start_at).await;
    let end_at = start_at + duration;

    loop {
        let started = Instant::now();
        if started >= end_at {
            break;
        }
        let response = client.unary_call(request.clone()).await;
        let latency = started.elapsed();
        match response {
            Ok(response) if validate_response(response.get_ref(), expected_response_size) => {
                result
                    .latencies
                    .push(u64::try_from(latency.as_nanos()).expect("RPC latency must fit in u64"));
            }
            _ => result.errors += 1,
        }
    }
    result
}

fn percentile(sorted: &[u64], fraction: f64) -> f64 {
    if sorted.is_empty() {
        return 0.0;
    }
    let index = ((fraction * sorted.len() as f64).ceil() as usize).saturating_sub(1);
    sorted[index] as f64 / 1000.0
}

async fn benchmark(options: Options) -> Result<(), Box<dyn Error>> {
    let config = &options.config;
    let endpoint = Endpoint::from_shared(format!("http://{}", config.addr))?;
    let mut channels = Vec::with_capacity(config.connections);
    for _ in 0..config.connections {
        channels.push(endpoint.clone().connect().await?);
    }

    let request = SimpleRequest {
        response_type: PayloadType::Compressable as i32,
        response_size: config.resp_size as i32,
        payload: Some(Payload {
            r#type: PayloadType::Compressable as i32,
            body: vec![0; config.req_size],
        }),
    };
    let (ready_tx, mut ready_rx) = tokio::sync::mpsc::channel(config.concurrency);
    let mut start_senders = Vec::with_capacity(config.concurrency);
    let mut workers: Vec<JoinHandle<WorkerResult>> = Vec::with_capacity(config.concurrency);

    for worker_id in 0..config.concurrency {
        let client = TestServiceClient::new(channels[worker_id % channels.len()].clone());
        let (start_tx, start_rx) = oneshot::channel();
        start_senders.push(start_tx);
        workers.push(tokio::spawn(run_worker(
            client,
            request.clone(),
            config.warmup,
            config.duration,
            config.resp_size,
            ready_tx.clone(),
            start_rx,
        )));
    }
    drop(ready_tx);

    for _ in 0..config.concurrency {
        ready_rx
            .recv()
            .await
            .ok_or("worker stopped during warmup")?;
    }
    let start_at = Instant::now() + Duration::from_millis(10);
    for sender in start_senders {
        sender
            .send(start_at)
            .map_err(|_| "worker stopped before measurement")?;
    }

    let mut latencies = Vec::new();
    let mut errors = 0;
    for worker in workers {
        let worker = worker.await?;
        latencies.extend(worker.latencies);
        errors += worker.errors;
    }
    latencies.sort_unstable();

    let total: u128 = latencies.iter().map(|&latency| u128::from(latency)).sum();
    let completed = latencies.len();
    let (min_us, max_us, mean_us) = if completed == 0 {
        (0.0, 0.0, 0.0)
    } else {
        (
            latencies[0] as f64 / 1000.0,
            latencies[completed - 1] as f64 / 1000.0,
            total as f64 / completed as f64 / 1000.0,
        )
    };
    let output = BenchmarkResult {
        round: options.round,
        label: options.label,
        client_framework_version: "tonic/0.14.6",
        server_version: options.server_version,
        completed,
        errors,
        ops_per_second: completed as f64 / config.duration.as_secs_f64(),
        p50_us: percentile(&latencies, 0.50),
        p95_us: percentile(&latencies, 0.95),
        p99_us: percentile(&latencies, 0.99),
        min_us,
        max_us,
        mean_us,
        config: options.config,
    };

    if options.compact {
        serde_json::to_writer(std::io::stdout(), &output)?;
    } else {
        serde_json::to_writer_pretty(std::io::stdout(), &output)?;
    }
    println!();
    if errors != 0 {
        return Err(format!("encountered {errors} RPC or validation errors").into());
    }
    Ok(())
}

fn run() -> Result<(), Box<dyn Error>> {
    let options = parse_args()?;
    let threads = env::var("CLIENT_THREADS")
        .ok()
        .map(|value| value.parse::<usize>())
        .transpose()?
        .unwrap_or(4);
    if threads == 0 {
        return Err("CLIENT_THREADS must be positive".into());
    }
    tokio::runtime::Builder::new_multi_thread()
        .worker_threads(threads)
        .enable_all()
        .build()?
        .block_on(benchmark(options))
}

fn main() {
    if let Err(error) = run() {
        eprintln!("{error}");
        process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_go_style_durations() {
        assert_eq!(parse_duration("300ms").unwrap(), Duration::from_millis(300));
        assert_eq!(
            parse_duration("1m2.5s").unwrap(),
            Duration::from_millis(62_500)
        );
    }

    #[test]
    fn uses_nearest_rank_percentiles() {
        assert_eq!(percentile(&[1000, 2000, 3000, 4000], 0.50), 2.0);
        assert_eq!(percentile(&[1000, 2000, 3000, 4000], 0.99), 4.0);
    }
}
