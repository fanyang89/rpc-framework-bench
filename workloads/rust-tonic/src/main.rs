use std::{env, net::SocketAddr};

use grpc_testing::{
    Payload, SimpleRequest, SimpleResponse, StreamingInputCallRequest, StreamingInputCallResponse,
    StreamingOutputCallRequest, StreamingOutputCallResponse,
    test_service_server::{TestService, TestServiceServer},
};
use prost::bytes::Bytes;
use tokio::sync::mpsc;
use tokio_stream::wrappers::ReceiverStream;
use tonic::transport::{Identity, ServerTlsConfig};
use tonic::{Request, Response, Status, transport::Server};

pub mod grpc_testing {
    tonic::include_proto!("grpc.testing");
}

// Shared read-only zero buffer avoids per-RPC heap allocations on large responses,
// matching the client template's reference-counted Bytes approach.
static ZERO_PAYLOAD: [u8; 1 << 20] = [0u8; 1 << 20];

fn payload_body(size: usize) -> Bytes {
    if size <= ZERO_PAYLOAD.len() {
        Bytes::from_static(&ZERO_PAYLOAD[..size])
    } else {
        Bytes::from(vec![0u8; size])
    }
}

#[derive(Default)]
struct BenchmarkService;

type OutputStream =
    tokio_stream::Iter<std::vec::IntoIter<Result<StreamingOutputCallResponse, Status>>>;
type BidiOutput = ReceiverStream<Result<StreamingOutputCallResponse, Status>>;

async fn send_responses(
    tx: &mpsc::Sender<Result<StreamingOutputCallResponse, Status>>,
    request: StreamingOutputCallRequest,
) -> Result<(), Status> {
    for parameter in request.response_parameters {
        if parameter.size < 0 {
            return Err(Status::invalid_argument("negative response size"));
        }
        tx.send(Ok(StreamingOutputCallResponse {
            payload: Some(Payload {
                r#type: 0,
                body: payload_body(parameter.size as usize),
            }),
        }))
        .await
        .map_err(|_| Status::cancelled("receiver closed"))?;
    }
    Ok(())
}

#[tonic::async_trait]
impl TestService for BenchmarkService {
    type StreamingOutputCallStream = OutputStream;
    type FullDuplexCallStream = BidiOutput;

    async fn streaming_output_call(
        &self,
        request: Request<StreamingOutputCallRequest>,
    ) -> Result<Response<OutputStream>, Status> {
        let params = request.into_inner().response_parameters;
        let mut responses = Vec::with_capacity(params.len());
        for parameter in params {
            if parameter.size < 0 {
                return Err(Status::invalid_argument("negative response size"));
            }
            responses.push(Ok(StreamingOutputCallResponse {
                payload: Some(Payload {
                    r#type: 0,
                    body: payload_body(parameter.size as usize),
                }),
            }));
        }
        Ok(Response::new(tokio_stream::iter(responses)))
    }

    async fn streaming_input_call(
        &self,
        request: Request<tonic::Streaming<StreamingInputCallRequest>>,
    ) -> Result<Response<StreamingInputCallResponse>, Status> {
        let mut stream = request.into_inner();
        let mut total: i32 = 0;
        while let Some(request) = stream.message().await? {
            let size = request.payload.map_or(0, |p| p.body.len());
            total = total
                .checked_add(
                    i32::try_from(size)
                        .map_err(|_| Status::invalid_argument("payload too large"))?,
                )
                .ok_or_else(|| Status::invalid_argument("aggregate exceeds int32"))?;
        }
        Ok(Response::new(StreamingInputCallResponse {
            aggregated_payload_size: total,
        }))
    }

    async fn full_duplex_call(
        &self,
        request: Request<tonic::Streaming<StreamingOutputCallRequest>>,
    ) -> Result<Response<BidiOutput>, Status> {
        let (tx, rx) = mpsc::channel(32);
        tokio::spawn(async move {
            let mut stream = request.into_inner();
            loop {
                let result = match stream.message().await {
                    Ok(Some(request)) => send_responses(&tx, request).await,
                    Ok(None) => break,
                    Err(error) => Err(error),
                };
                if let Err(error) = result {
                    let _ = tx.send(Err(error)).await;
                    break;
                }
            }
        });
        Ok(Response::new(ReceiverStream::new(rx)))
    }

    async fn unary_call(
        &self,
        request: Request<SimpleRequest>,
    ) -> Result<Response<SimpleResponse>, Status> {
        let response_size = request.into_inner().response_size;
        if response_size < 0 {
            return Err(Status::invalid_argument(
                "response_size must be non-negative",
            ));
        }
        Ok(Response::new(SimpleResponse {
            payload: Some(Payload {
                r#type: 0,
                body: payload_body(response_size as usize),
            }),
        }))
    }
}

async fn serve() -> Result<(), Box<dyn std::error::Error>> {
    let addr: SocketAddr = env::var("SERVER_ADDR")
        .unwrap_or_else(|_| "127.0.0.1:50053".to_owned())
        .parse()?;
    let mut server = Server::builder();
    if let Ok(cert) = env::var("TLS_CERT") {
        server = server.tls_config(ServerTlsConfig::new().identity(Identity::from_pem(
            std::fs::read(cert)?,
            std::fs::read(env::var("TLS_KEY")?)?,
        )))?;
    }
    server
        .add_service(TestServiceServer::new(BenchmarkService))
        .serve(addr)
        .await?;
    Ok(())
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let threads = env::var("SERVER_THREADS")
        .ok()
        .map(|value| value.parse::<usize>())
        .transpose()?
        .unwrap_or(1);
    if threads == 0 {
        return Err("SERVER_THREADS must be positive".into());
    }
    tokio::runtime::Builder::new_multi_thread()
        .worker_threads(threads)
        .enable_all()
        .build()?
        .block_on(serve())
}
