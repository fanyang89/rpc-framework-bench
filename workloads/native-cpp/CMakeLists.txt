cmake_minimum_required(VERSION 3.20)
project(native_rpc_benchmark LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
find_package(Threads REQUIRED)
include_directories("${CMAKE_CURRENT_BINARY_DIR}")

# gRPC and brpc use different protobuf installations; build in separate trees.
option(GRPC_ONLY "Build the gRPC load generator" OFF)
if(GRPC_ONLY)
  find_package(Protobuf CONFIG REQUIRED)
  find_package(gRPC CONFIG REQUIRED)
  add_custom_command(
    OUTPUT benchmark.pb.cc benchmark.pb.h benchmark.grpc.pb.cc benchmark.grpc.pb.h
    COMMAND protobuf::protoc --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/../../proto
      --cpp_out=${CMAKE_CURRENT_BINARY_DIR} --grpc_out=${CMAKE_CURRENT_BINARY_DIR}
      --plugin=protoc-gen-grpc=$<TARGET_FILE:gRPC::grpc_cpp_plugin>
      "${CMAKE_CURRENT_SOURCE_DIR}/../../proto/benchmark.proto"
    DEPENDS ../../proto/benchmark.proto VERBATIM)
  add_executable(grpc-native grpc.cc benchmark.pb.cc benchmark.grpc.pb.cc)
  target_link_libraries(grpc-native PRIVATE gRPC::grpc++ protobuf::libprotobuf Threads::Threads)
  return()
endif()

find_package(CapnProto CONFIG REQUIRED)
capnp_generate_cpp(CAPNP_SOURCES CAPNP_HEADERS benchmark.capnp)
add_executable(capnp-native capnp.cc ${CAPNP_SOURCES})
target_link_libraries(capnp-native PRIVATE CapnProto::capnp-rpc Threads::Threads)

find_package(Thrift CONFIG REQUIRED)
add_custom_command(OUTPUT Benchmark.cpp Benchmark.h benchmark_types.h
  COMMAND "${THRIFT_COMPILER}" --gen cpp:no_skeleton -out "${CMAKE_CURRENT_BINARY_DIR}"
    "${CMAKE_CURRENT_SOURCE_DIR}/benchmark.thrift"
  DEPENDS benchmark.thrift VERBATIM)
add_executable(thrift-native thrift.cc Benchmark.cpp)
target_link_libraries(thrift-native PRIVATE thriftnb::thriftnb thrift::thrift Threads::Threads)

find_package(Protobuf REQUIRED)
find_package(OpenSSL REQUIRED)
find_package(ZLIB REQUIRED)
find_path(BRPC_INCLUDE_DIR brpc/server.h REQUIRED)
find_library(BRPC_LIBRARY brpc REQUIRED)
find_library(GFLAGS_LIBRARY gflags REQUIRED)
find_library(LEVELDB_LIBRARY leveldb REQUIRED)
find_library(SNAPPY_LIBRARY snappy REQUIRED)
protobuf_generate_cpp(BRPC_PROTO_SOURCES BRPC_PROTO_HEADERS brpc.proto)
add_executable(brpc-native brpc.cc ${BRPC_PROTO_SOURCES})
target_include_directories(brpc-native PRIVATE "${BRPC_INCLUDE_DIR}" "${Protobuf_INCLUDE_DIRS}")
target_link_libraries(brpc-native PRIVATE "${BRPC_LIBRARY}" "${Protobuf_LIBRARIES}"
  "${GFLAGS_LIBRARY}" "${LEVELDB_LIBRARY}" "${SNAPPY_LIBRARY}"
  OpenSSL::SSL OpenSSL::Crypto ZLIB::ZLIB Threads::Threads ${CMAKE_DL_LIBS})
