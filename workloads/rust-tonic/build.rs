fn main() -> Result<(), Box<dyn std::error::Error>> {
    tonic_prost_build::configure()
        .build_client(true)
        .build_server(true)
        .bytes(".grpc.testing.Payload.body")
        .compile_protos(&["../../proto/benchmark.proto"], &["../../proto"])?;
    println!("cargo:rerun-if-changed=../../proto/benchmark.proto");
    Ok(())
}
