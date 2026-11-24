namespace cpp native_rpc

service Benchmark {
  binary call(1: binary payload, 2: i32 response_size)
}
