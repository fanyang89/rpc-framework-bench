#include <cstdint>
#include <memory>
#include <string>

#include <grpcpp/grpcpp.h>

#include "benchmark.grpc.pb.h"

class TestService final : public grpc::testing::TestService::Service {
  grpc::Status UnaryCall(grpc::ServerContext*,
                         const grpc::testing::SimpleRequest* request,
                         grpc::testing::SimpleResponse* response) override {
    if (request->response_size() < 0) {
      return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT,
                          "response_size must be non-negative");
    }
    auto* payload = response->mutable_payload();
    payload->set_type(grpc::testing::COMPRESSABLE);
    payload->mutable_body()->resize(static_cast<std::size_t>(request->response_size()), '\0');
    return grpc::Status::OK;
  }
};

int main(int argc, char** argv) {
  std::string address = "127.0.0.1:50054";
  if (argc == 2) {
    address = argv[1];
  } else if (argc != 1) {
    return 2;
  }

  TestService service;
  grpc::ServerBuilder builder;
  builder.AddListeningPort(address, grpc::InsecureServerCredentials());
  builder.RegisterService(&service);
  std::unique_ptr<grpc::Server> server = builder.BuildAndStart();
  if (!server) {
    return 1;
  }
  server->Wait();
  return 0;
}
