#include <algorithm>
#include <atomic>
#include <csignal>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <iterator>
#include <memory>
#include <string>
#include <thread>

#include <grpcpp/grpcpp.h>
#include <grpcpp/resource_quota.h>

#include "benchmark.grpc.pb.h"

namespace {
std::atomic<bool> g_shutdown_requested{false};

void HandleSignal(int) { g_shutdown_requested.store(true); }

int ParseServerThreads() {
  const char* raw = std::getenv("SERVER_THREADS");
  if (!raw || *raw == '\0') return 1;
  char* end = nullptr;
  long parsed = std::strtol(raw, &end, 10);
  if (end == raw || *end != '\0' || parsed <= 0 || parsed > 256) {
    throw std::invalid_argument("SERVER_THREADS must be a positive integer");
  }
  return static_cast<int>(parsed);
}
}  // namespace

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
    payload->mutable_body()->assign(static_cast<std::size_t>(request->response_size()), '\0');
    return grpc::Status::OK;
  }
  grpc::Status StreamingOutputCall(
      grpc::ServerContext*,
      const grpc::testing::StreamingOutputCallRequest* request,
      grpc::ServerWriter<grpc::testing::StreamingOutputCallResponse>* writer) override {
    grpc::testing::StreamingOutputCallResponse response;
    auto* payload = response.mutable_payload();
    payload->set_type(grpc::testing::COMPRESSABLE);
    const int count = request->response_parameters_size();
    for (int i = 0; i < count; ++i) {
      const int size = request->response_parameters(i).size();
      if (size < 0) return {grpc::StatusCode::INVALID_ARGUMENT, "negative size"};
      payload->mutable_body()->assign(static_cast<std::size_t>(size), '\0');
      if (!writer->Write(response)) return {grpc::StatusCode::CANCELLED, "write failed"};
    }
    return grpc::Status::OK;
  }
  grpc::Status StreamingInputCall(
      grpc::ServerContext*,
      grpc::ServerReader<grpc::testing::StreamingInputCallRequest>* reader,
      grpc::testing::StreamingInputCallResponse* response) override {
    grpc::testing::StreamingInputCallRequest request;
    int64_t total = 0;
    while (reader->Read(&request)) {
      total += request.payload().body().size();
      if (total > INT32_MAX) return {grpc::StatusCode::INVALID_ARGUMENT, "aggregate too large"};
    }
    response->set_aggregated_payload_size(static_cast<int32_t>(total));
    return grpc::Status::OK;
  }
  grpc::Status FullDuplexCall(
      grpc::ServerContext*,
      grpc::ServerReaderWriter<grpc::testing::StreamingOutputCallResponse,
                               grpc::testing::StreamingOutputCallRequest>* stream) override {
    grpc::testing::StreamingOutputCallRequest request;
    grpc::testing::StreamingOutputCallResponse response;
    auto* payload = response.mutable_payload();
    payload->set_type(grpc::testing::COMPRESSABLE);
    while (stream->Read(&request)) {
      const int count = request.response_parameters_size();
      for (int i = 0; i < count; ++i) {
        const int size = request.response_parameters(i).size();
        if (size < 0) return {grpc::StatusCode::INVALID_ARGUMENT, "negative size"};
        payload->mutable_body()->assign(static_cast<std::size_t>(size), '\0');
        if (!stream->Write(response)) return {grpc::StatusCode::CANCELLED, "write failed"};
      }
    }
    return grpc::Status::OK;
  }
};

using InputRequest = grpc::testing::StreamingInputCallRequest;
using InputResponse = grpc::testing::StreamingInputCallResponse;
using OutputRequest = grpc::testing::StreamingOutputCallRequest;
using OutputResponse = grpc::testing::StreamingOutputCallResponse;

class OutputReactor final : public grpc::ServerWriteReactor<OutputResponse> {
 public:
  explicit OutputReactor(const OutputRequest* request) : request_(request) {
    response_.mutable_payload()->set_type(grpc::testing::COMPRESSABLE);
    Next();
  }
  void OnWriteDone(bool ok) override {
    if (ok) Next();
    else Finish({grpc::StatusCode::CANCELLED, "write failed"});
  }
  void OnDone() override { delete this; }

 private:
  void Next() {
    const int count = request_->response_parameters_size();
    if (index_ == count) {
      Finish(grpc::Status::OK);
      return;
    }
    const int size = request_->response_parameters(index_++).size();
    if (size < 0) {
      Finish({grpc::StatusCode::INVALID_ARGUMENT, "negative size"});
      return;
    }
    response_.mutable_payload()->mutable_body()->assign(static_cast<std::size_t>(size), '\0');
    StartWrite(&response_);
  }
  const OutputRequest* request_;
  OutputResponse response_;
  int index_ = 0;
};

class InputReactor final : public grpc::ServerReadReactor<InputRequest> {
 public:
  explicit InputReactor(InputResponse* response) : response_(response) { StartRead(&request_); }
  void OnReadDone(bool ok) override {
    if (!ok) {
      response_->set_aggregated_payload_size(static_cast<int32_t>(total_));
      Finish(grpc::Status::OK);
      return;
    }
    total_ += request_.payload().body().size();
    if (total_ > INT32_MAX) {
      Finish({grpc::StatusCode::INVALID_ARGUMENT, "aggregate too large"});
      return;
    }
    request_.Clear();
    StartRead(&request_);
  }
  void OnDone() override { delete this; }

 private:
  InputResponse* response_;
  InputRequest request_;
  int64_t total_ = 0;
};

class BidiReactor final : public grpc::ServerBidiReactor<OutputRequest, OutputResponse> {
 public:
  BidiReactor() {
    response_.mutable_payload()->set_type(grpc::testing::COMPRESSABLE);
    StartRead(&request_);
  }
  void OnReadDone(bool ok) override {
    if (!ok) {
      Finish(grpc::Status::OK);
      return;
    }
    index_ = 0;
    Next();
  }
  void OnWriteDone(bool ok) override {
    if (ok) Next();
    else Finish({grpc::StatusCode::CANCELLED, "write failed"});
  }
  void OnDone() override { delete this; }

 private:
  void Next() {
    const int count = request_.response_parameters_size();
    if (index_ == count) {
      request_.Clear();
      StartRead(&request_);
      return;
    }
    const int size = request_.response_parameters(index_++).size();
    if (size < 0) {
      Finish({grpc::StatusCode::INVALID_ARGUMENT, "negative size"});
      return;
    }
    response_.mutable_payload()->mutable_body()->assign(static_cast<std::size_t>(size), '\0');
    StartWrite(&response_);
  }
  OutputRequest request_;
  OutputResponse response_;
  int index_ = 0;
};

class CallbackService final : public grpc::testing::TestService::CallbackService {
 public:
  grpc::ServerUnaryReactor* UnaryCall(grpc::CallbackServerContext* context,
                                      const grpc::testing::SimpleRequest* request,
                                      grpc::testing::SimpleResponse* response) override {
    auto* reactor = context->DefaultReactor();
    if (request->response_size() < 0) {
      reactor->Finish({grpc::StatusCode::INVALID_ARGUMENT, "negative size"});
    } else {
      auto* payload = response->mutable_payload();
      payload->set_type(grpc::testing::COMPRESSABLE);
      payload->mutable_body()->assign(static_cast<std::size_t>(request->response_size()), '\0');
      reactor->Finish(grpc::Status::OK);
    }
    return reactor;
  }
  grpc::ServerWriteReactor<OutputResponse>* StreamingOutputCall(
      grpc::CallbackServerContext*, const OutputRequest* request) override {
    return new OutputReactor(request);
  }
  grpc::ServerReadReactor<InputRequest>* StreamingInputCall(
      grpc::CallbackServerContext*, InputResponse* response) override {
    return new InputReactor(response);
  }
  grpc::ServerBidiReactor<OutputRequest, OutputResponse>* FullDuplexCall(
      grpc::CallbackServerContext*) override {
    return new BidiReactor;
  }
};

std::string ReadFile(const char* path) {
  if (!path) throw std::runtime_error("missing TLS key path");
  std::ifstream input(path);
  if (!input) throw std::runtime_error("cannot read TLS file");
  return {std::istreambuf_iterator<char>(input), std::istreambuf_iterator<char>()};
}

int main(int argc, char** argv) {
  std::string address = "127.0.0.1:50054";
  if (argc == 2) {
    address = argv[1];
  } else if (argc != 1) {
    return 2;
  }

  const int server_threads = ParseServerThreads();
  const char* api = std::getenv("CPP_API");
  const bool use_callback = api && std::string(api) == "callback";

  TestService service;
  CallbackService callback_service;
  grpc::ServerBuilder builder;
  auto credentials = grpc::InsecureServerCredentials();
  if (const char* cert = std::getenv("TLS_CERT")) {
    grpc::SslServerCredentialsOptions options;
    options.pem_key_cert_pairs.push_back({ReadFile(std::getenv("TLS_KEY")), ReadFile(cert)});
    credentials = grpc::SslServerCredentials(options);
  }
  builder.AddListeningPort(address, credentials);

  // Align polling/callback thread budgets with SERVER_THREADS to avoid 32-thread
  // oversubscription when pinned to 1–8 physical cores.
  builder.SetSyncServerOption(grpc::ServerBuilder::NUM_CQS, server_threads);
  builder.SetSyncServerOption(grpc::ServerBuilder::MIN_POLLERS, server_threads);
  builder.SetSyncServerOption(grpc::ServerBuilder::MAX_POLLERS, std::max(server_threads * 2, 2));
  if (use_callback) {
    grpc::ResourceQuota quota("cpp-callback-server");
    quota.SetMaxThreads(std::max(server_threads * 2, 4));
    builder.SetResourceQuota(quota);
    builder.RegisterService(&callback_service);
  } else {
    builder.RegisterService(&service);
  }

  std::unique_ptr<grpc::Server> server = builder.BuildAndStart();
  if (!server) {
    return 1;
  }

  std::signal(SIGINT, HandleSignal);
  std::signal(SIGTERM, HandleSignal);
  std::thread shutdown_watcher([&server] {
    while (!g_shutdown_requested.load()) {
      std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }
    server->Shutdown();
  });
  server->Wait();
  g_shutdown_requested.store(true);
  shutdown_watcher.join();
  return 0;
}
