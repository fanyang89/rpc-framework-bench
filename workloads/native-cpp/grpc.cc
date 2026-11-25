#include "load.h"
#include "benchmark.grpc.pb.h"
#include <grpcpp/grpcpp.h>

int main(int argc, char** argv) {
  try {
    auto config = load::parse(argc, argv);
    return load::run(config, [&] {
      grpc::ChannelArguments arguments;
      arguments.SetInt("grpc.use_local_subchannel_pool", 1);
      arguments.SetInt("grpc.enable_retries", 0);
      arguments.SetInt("grpc.enable_http_proxy", 0);
      auto channel = grpc::CreateCustomChannel(config.address, grpc::InsecureChannelCredentials(), arguments);
      if (!channel->WaitForConnected(std::chrono::system_clock::now() + std::chrono::seconds(10)))
        throw std::runtime_error("connect timeout");
      auto client = grpc::testing::TestService::NewStub(channel);
      grpc::testing::SimpleRequest request;
      request.mutable_payload()->set_body(std::string(config.request_size, '\0'));
      request.set_response_size(config.response_size);
      grpc::testing::SimpleResponse response;
      return [client = std::move(client), request, response = std::move(response), config]() mutable {
        grpc::ClientContext context;
        context.set_deadline(std::chrono::system_clock::now() + std::chrono::seconds(10));
        response.Clear();
        auto status = client->UnaryCall(&context, request, &response);
        if (!status.ok()) throw std::runtime_error(status.error_message());
        load::validate(response.payload().body().size(), config.response_size);
      };
    });
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
