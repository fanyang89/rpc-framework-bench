use std::{env, net::SocketAddr};

use grpc_testing::{
    Payload, SimpleRequest, SimpleResponse,
    test_service_server::{TestService, TestServiceServer},
};
use tonic::{Request, Response, Status, transport::Server};

pub mod grpc_testing {
    tonic::include_proto!("grpc.testing");
}

#[derive(Default)]
struct BenchmarkService;

#[tonic::async_trait]
impl TestService for BenchmarkService {
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
                body: vec![0; response_size as usize],
            }),
        }))
    }
}

async fn serve() -> Result<(), Box<dyn std::error::Error>> {
    let addr: SocketAddr = env::var("SERVER_ADDR")
        .unwrap_or_else(|_| "127.0.0.1:50053".to_owned())
        .parse()?;
    Server::builder()
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
