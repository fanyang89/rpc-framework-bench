use std::{env, error::Error, process, time::Duration};

use grpc_testing::{
    Payload, PayloadType, ResponseParameters, SimpleRequest, SimpleResponse,
    StreamingInputCallRequest, StreamingOutputCallRequest, test_service_client::TestServiceClient,
};
use prost::bytes::Bytes;
use serde::Serialize;
use tokio::{sync::oneshot, task::JoinHandle, time::Instant};
use tonic::transport::{Certificate, Channel, ClientTlsConfig, Endpoint};

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
    mode: String,
    messages: usize,
    tls_ca: String,
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

// Log-linear histogram with 8-bit mantissa (256 sub-buckets per octave, <0.4% error,
// exact below 512 ns, covers up to ~68.7 s) for O(1) zero-allocation latency recording.
const HIST_SUB_BITS: u32 = 8;
const HIST_SUB_SIZE: usize = 1 << HIST_SUB_BITS;
const HIST_BUCKETS: usize = HIST_SUB_SIZE * 30;

struct LatencyHistogram {
    buckets: Vec<u64>,
    count: u64,
    sum_ns: u128,
    min_ns: u64,
    max_ns: u64,
}

impl LatencyHistogram {
    fn new() -> Self {
        Self {
            buckets: vec![0u64; HIST_BUCKETS],
            count: 0,
            sum_ns: 0,
            min_ns: 0,
            max_ns: 0,
        }
    }

    fn bucket(ns: u64) -> usize {
        if ns < (2 * HIST_SUB_SIZE) as u64 {
            return ns as usize;
        }
        let msb = 63 - ns.leading_zeros();
        let shift = msb - HIST_SUB_BITS;
        let octave = (shift + 1) as usize;
        let sub = ((ns >> shift) as usize) & (HIST_SUB_SIZE - 1);
        (octave * HIST_SUB_SIZE + sub).min(HIST_BUCKETS - 1)
    }

    fn value(idx: usize) -> u64 {
        if idx < 2 * HIST_SUB_SIZE {
            return idx as u64;
        }
        let octave = idx >> HIST_SUB_BITS;
        let sub = (idx & (HIST_SUB_SIZE - 1)) as u64;
        let shift = (octave - 1) as u32;
        ((HIST_SUB_SIZE as u64) + sub) << shift
    }

    fn record(&mut self, ns: u64) {
        self.buckets[Self::bucket(ns)] += 1;
        if self.count == 0 || ns < self.min_ns {
            self.min_ns = ns;
        }
        if ns > self.max_ns {
            self.max_ns = ns;
        }
        self.count += 1;
        self.sum_ns += u128::from(ns);
    }

    fn merge(&mut self, other: &Self) {
        if other.count == 0 {
            return;
        }
        if self.count == 0 || other.min_ns < self.min_ns {
            self.min_ns = other.min_ns;
        }
        if other.max_ns > self.max_ns {
            self.max_ns = other.max_ns;
        }
        self.count += other.count;
        self.sum_ns += other.sum_ns;
        for (dst, &src) in self.buckets.iter_mut().zip(other.buckets.iter()) {
            *dst += src;
        }
    }

    fn percentile_us(&self, fraction: f64) -> f64 {
        if self.count == 0 {
            return 0.0;
        }
        let rank = ((fraction * self.count as f64).ceil() as u64).max(1);
        let mut seen = 0u64;
        for (idx, &c) in self.buckets.iter().enumerate() {
            seen += c;
            if seen >= rank {
                let v = Self::value(idx).clamp(self.min_ns, self.max_ns);
                return v as f64 / 1000.0;
            }
        }
        self.max_ns as f64 / 1000.0
    }
}

struct WorkerResult {
    hist: LatencyHistogram,
    errors: u64,
    active_seconds: f64,
}

#[derive(Clone)]
struct RequestTemplates {
    unary: SimpleRequest,
    input_messages: Vec<StreamingInputCallRequest>,
    output_request: StreamingOutputCallRequest,
    ping_request: StreamingOutputCallRequest,
    expected_aggregate: usize,
    expected_response_size: usize,
}

impl RequestTemplates {
    fn new(req_size: usize, resp_size: usize, messages: usize) -> Self {
        let payload = Some(Payload {
            r#type: PayloadType::Compressable as i32,
            body: Bytes::from(vec![0; req_size]),
        });
        let unary = SimpleRequest {
            response_type: PayloadType::Compressable as i32,
            response_size: resp_size as i32,
            payload: payload.clone(),
        };
        let input_item = StreamingInputCallRequest {
            payload: payload.clone(),
        };
        let input_messages = vec![input_item; messages];
        let output_request = StreamingOutputCallRequest {
            response_type: PayloadType::Compressable as i32,
            response_parameters: vec![
                ResponseParameters {
                    size: resp_size as i32,
                };
                messages
            ],
            payload: payload.clone(),
        };
        let ping_request = StreamingOutputCallRequest {
            response_type: PayloadType::Compressable as i32,
            response_parameters: vec![ResponseParameters {
                size: resp_size as i32,
            }],
            payload,
        };
        Self {
            unary,
            input_messages,
            output_request,
            ping_request,
            expected_aggregate: req_size * messages,
            expected_response_size: resp_size,
        }
    }
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
                mode: "unary".to_owned(),
                messages: 16,
                tls_ca: String::new(),
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
            "mode" => options.config.mode = value,
            "messages" => {
                options.config.messages = value.parse().map_err(|_| "invalid messages")?
            }
            "tls_ca" => options.config.tls_ca = value,
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
    if !["unary", "client-stream", "server-stream", "bidi"].contains(&config.mode.as_str())
        || config.messages == 0
        || config.messages > 1024
        || config.req_size as u64 * config.messages as u64 > i32::MAX as u64
    {
        return Err("invalid mode or message count/aggregate size".to_owned());
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
    templates: RequestTemplates,
    warmup: Duration,
    duration: Duration,
    ready: tokio::sync::mpsc::Sender<()>,
    measurement_start: oneshot::Receiver<Instant>,
    mode: String,
    messages: usize,
) -> WorkerResult {
    let mut result = WorkerResult {
        hist: LatencyHistogram::new(),
        errors: 0,
        active_seconds: duration.as_secs_f64(),
    };

    let warmup_end = Instant::now() + warmup;
    while Instant::now() < warmup_end {
        if !call(&mut client, &templates, &mode, messages).await {
            result.errors += 1;
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

    let mut now = Instant::now();
    while now < end_at {
        let started = now;
        let success = call(&mut client, &templates, &mode, messages).await;
        now = Instant::now();
        if success {
            let latency_ns = u64::try_from(now.saturating_duration_since(started).as_nanos())
                .expect("RPC latency must fit in u64");
            result.hist.record(latency_ns);
        } else {
            result.errors += 1;
        }
    }
    if now > start_at {
        result.active_seconds = now.duration_since(start_at).as_secs_f64();
    }
    result
}

async fn call(
    client: &mut TestServiceClient<Channel>,
    templates: &RequestTemplates,
    mode: &str,
    messages: usize,
) -> bool {
    let expected = templates.expected_response_size;
    tokio::time::timeout(Duration::from_secs(10), async {
        match mode {
            "unary" => {
                let response = client.unary_call(templates.unary.clone()).await?;
                if !validate_response(response.get_ref(), expected) {
                    return Err(tonic::Status::data_loss("wrong payload"));
                }
            }
            "client-stream" => {
                let response = client
                    .streaming_input_call(tokio_stream::iter(templates.input_messages.clone()))
                    .await?
                    .into_inner();
                if response.aggregated_payload_size as usize != templates.expected_aggregate {
                    return Err(tonic::Status::data_loss("wrong aggregate"));
                }
            }
            "server-stream" => {
                let mut stream = client
                    .streaming_output_call(templates.output_request.clone())
                    .await?
                    .into_inner();
                for _ in 0..messages {
                    let response = stream
                        .message()
                        .await?
                        .ok_or_else(|| tonic::Status::data_loss("missing message"))?;
                    if !response.payload.is_some_and(|p| p.body.len() == expected) {
                        return Err(tonic::Status::data_loss("wrong payload"));
                    }
                }
                if stream.message().await?.is_some() {
                    return Err(tonic::Status::data_loss("extra message"));
                }
            }
            "bidi" => {
                let (tx, rx) = tokio::sync::mpsc::channel(1);
                // Some peers send response headers only after the first request.
                tx.send(templates.ping_request.clone())
                    .await
                    .map_err(|_| tonic::Status::cancelled("sender closed"))?;
                let mut stream = client
                    .full_duplex_call(tokio_stream::wrappers::ReceiverStream::new(rx))
                    .await?
                    .into_inner();
                for index in 0..messages {
                    if index != 0 {
                        tx.send(templates.ping_request.clone())
                            .await
                            .map_err(|_| tonic::Status::cancelled("sender closed"))?;
                    }
                    let response = stream
                        .message()
                        .await?
                        .ok_or_else(|| tonic::Status::data_loss("missing message"))?;
                    if !response.payload.is_some_and(|p| p.body.len() == expected) {
                        return Err(tonic::Status::data_loss("wrong payload"));
                    }
                }
                drop(tx);
                if stream.message().await?.is_some() {
                    return Err(tonic::Status::data_loss("extra message"));
                }
            }
            _ => return Err(tonic::Status::invalid_argument("unknown mode")),
        }
        Ok::<(), tonic::Status>(())
    })
    .await
    .is_ok_and(|result| result.is_ok())
}

async fn benchmark(options: Options) -> Result<(), Box<dyn Error>> {
    let config = &options.config;
    let scheme = if config.tls_ca.is_empty() {
        "http"
    } else {
        "https"
    };
    let mut endpoint = Endpoint::from_shared(format!("{scheme}://{}", config.addr))?;
    if !config.tls_ca.is_empty() {
        endpoint = endpoint.tls_config(
            ClientTlsConfig::new()
                .domain_name("localhost")
                .ca_certificate(Certificate::from_pem(std::fs::read(&config.tls_ca)?)),
        )?;
    }
    endpoint = endpoint.connect_timeout(Duration::from_secs(10));
    let mut channels = Vec::with_capacity(config.connections);
    for _ in 0..config.connections {
        channels.push(endpoint.clone().connect().await?);
    }

    let templates = RequestTemplates::new(config.req_size, config.resp_size, config.messages);
    let (ready_tx, mut ready_rx) = tokio::sync::mpsc::channel(config.concurrency);
    let mut start_senders = Vec::with_capacity(config.concurrency);
    let mut workers: Vec<JoinHandle<WorkerResult>> = Vec::with_capacity(config.concurrency);

    for worker_id in 0..config.concurrency {
        let client = TestServiceClient::new(channels[worker_id % channels.len()].clone());
        let (start_tx, start_rx) = oneshot::channel();
        start_senders.push(start_tx);
        workers.push(tokio::spawn(run_worker(
            client,
            templates.clone(),
            config.warmup,
            config.duration,
            ready_tx.clone(),
            start_rx,
            config.mode.clone(),
            config.messages,
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

    let mut combined = LatencyHistogram::new();
    let mut errors = 0;
    let mut ops_per_second = 0.0;
    for worker in workers {
        let worker = worker.await?;
        combined.merge(&worker.hist);
        errors += worker.errors;
        if worker.hist.count > 0 && worker.active_seconds > 0.0 {
            ops_per_second += worker.hist.count as f64 / worker.active_seconds;
        }
    }

    let completed = combined.count as usize;
    let (min_us, max_us, mean_us) = if completed == 0 {
        (0.0, 0.0, 0.0)
    } else {
        (
            combined.min_ns as f64 / 1000.0,
            combined.max_ns as f64 / 1000.0,
            combined.sum_ns as f64 / completed as f64 / 1000.0,
        )
    };
    let output = BenchmarkResult {
        round: options.round,
        label: options.label,
        client_framework_version: concat!("tonic/", env!("TONIC_VERSION")),
        server_version: options.server_version,
        completed,
        errors,
        ops_per_second,
        p50_us: combined.percentile_us(0.50),
        p95_us: combined.percentile_us(0.95),
        p99_us: combined.percentile_us(0.99),
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
    fn uses_nearest_rank_percentiles_in_histogram() {
        let mut hist = LatencyHistogram::new();
        for &v in &[1000u64, 2000, 3000, 4000] {
            hist.record(v);
        }
        assert_eq!(hist.percentile_us(0.50), 2.0);
        assert_eq!(hist.percentile_us(0.99), 4.0);
    }

    #[test]
    fn histogram_accuracy_across_scales() {
        let mut hist = LatencyHistogram::new();
        for v in [100u64, 512, 15_500, 125_000, 1_500_000, 13_500_000] {
            hist.record(v);
        }
        let p50 = hist.percentile_us(0.50);
        assert!((p50 - 15.5).abs() < 0.1, "p50={p50}");
        let p99 = hist.percentile_us(0.99);
        assert!((p99 - 13_500.0).abs() < 60.0, "p99={p99}");
    }
}
