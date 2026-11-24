#include "load.h"
#include "brpc.pb.h"
#include <brpc/channel.h>
#include <brpc/server.h>
#include <brpc/closure_guard.h>
#include <gflags/gflags.h>

class Service final : public native_rpc::Benchmark {
  void Call(google::protobuf::RpcController* control, const native_rpc::Request* request,
            native_rpc::Response* response, google::protobuf::Closure* done) override {
    brpc::ClosureGuard guard(done);
    if (request->response_size() < 0 || request->response_size() > 1048576) {
      control->SetFailed("invalid response size");
      return;
    }
    response->mutable_payload()->assign(request->response_size(), '\0');
  }
};

int main(int argc, char** argv) {
  try {
    if (argc == 4 && std::string(argv[1]) == "server") {
      Service service;
      brpc::Server server;
      brpc::ServerOptions options;
      options.num_threads = load::number(argv[3], 1, 8);
      if (server.AddService(&service, brpc::SERVER_DOESNT_OWN_SERVICE) != 0 ||
          server.Start(("127.0.0.1:" + std::string(argv[2])).c_str(), &options) != 0)
        throw std::runtime_error("server startup failed");
      server.RunUntilAskedToQuit();
      return 0;
    }
    auto config = load::parse(argc, argv);
    google::SetCommandLineOption("bthread_concurrency", "8");
    std::atomic<int> index{0};
    return load::run(config, [&] {
      auto channel = std::make_shared<brpc::Channel>();
      brpc::ChannelOptions options;
      options.protocol = "baidu_std";
      options.connection_type = "single";
      options.connection_group = "worker-" + std::to_string(index.fetch_add(1));
      options.timeout_ms = options.connect_timeout_ms = 10000;
      options.max_retry = 0;
      if (channel->Init(config.address.c_str(), &options) != 0)
        throw std::runtime_error("channel startup failed");
      native_rpc::Request request;
      request.set_payload(std::string(config.request_size, '\0'));
      request.set_response_size(config.response_size);
      return [channel, request, config] {
        native_rpc::Benchmark_Stub client(channel.get());
        brpc::Controller control;
        native_rpc::Response response;
        client.Call(&control, &request, &response, nullptr);
        if (control.Failed()) throw std::runtime_error(control.ErrorText());
        load::validate(response.payload().size(), config.response_size);
      };
    });
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
