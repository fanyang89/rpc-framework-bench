cmake_minimum_required(VERSION 3.20)
project(rpc_benchmark LANGUAGES CXX)
find_package(Protobuf CONFIG REQUIRED)
find_package(gRPC CONFIG REQUIRED)
set(proto "${CMAKE_CURRENT_SOURCE_DIR}/../../proto/benchmark.proto")
add_custom_command(
  OUTPUT benchmark.pb.cc benchmark.pb.h benchmark.grpc.pb.cc benchmark.grpc.pb.h
  COMMAND protobuf::protoc
    --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/../../proto
    --cpp_out=${CMAKE_CURRENT_BINARY_DIR}
    --grpc_out=${CMAKE_CURRENT_BINARY_DIR}
    --plugin=protoc-gen-grpc=$<TARGET_FILE:gRPC::grpc_cpp_plugin>
    "${proto}"
  DEPENDS "${proto}"
  VERBATIM)
add_executable(cpp-server server.cc benchmark.pb.cc benchmark.grpc.pb.cc)
target_compile_features(cpp-server PRIVATE cxx_std_17)
target_include_directories(cpp-server PRIVATE "${CMAKE_CURRENT_BINARY_DIR}")
target_link_libraries(cpp-server PRIVATE gRPC::grpc++ protobuf::libprotobuf)
