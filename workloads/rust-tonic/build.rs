fn detect_tonic_version() -> String {
    let manifest = std::fs::read_to_string("Cargo.toml").unwrap_or_default();
    for line in manifest.lines() {
        let trimmed = line.trim();
        if trimmed.starts_with("tonic ") || trimmed.starts_with("tonic=") {
            if let Some(start) = trimmed.find("version = \"") {
                let rest = &trimmed[start + 11..];
                if let Some(end) = rest.find('"') {
                    return rest[..end].trim_start_matches('=').to_owned();
                }
            }
        }
    }
    "unknown".to_owned()
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    println!("cargo:rustc-env=TONIC_VERSION={}", detect_tonic_version());
    println!("cargo:rerun-if-changed=Cargo.toml");
    tonic_prost_build::configure()
        .build_client(true)
        .build_server(true)
        .bytes(".grpc.testing.Payload.body")
        .compile_protos(&["../../proto/benchmark.proto"], &["../../proto"])?;
    println!("cargo:rerun-if-changed=../../proto/benchmark.proto");
    Ok(())
}
